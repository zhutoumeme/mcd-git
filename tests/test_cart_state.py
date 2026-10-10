"""购物车行构造（特制、换搭）、commit 哈希，以及 add / restore / reset / commit 的状态机。"""
from __future__ import annotations

import copy
import json

from mcd_git.cart import build_line, line_item
from mcd_git.demo import world
from mcd_git.state import commit_hash, make_commit, state_root, verify_commit


def _single(code: str, **kw):  # type: ignore[no-untyped-def]
    item = world.CATALOG["pickup"][code]
    return build_line(line_id="l1", code=code, menu_item=item, detail=world.detail_payload("pickup", code), **kw)


def test_no_pickle_sends_whole_multi_select_group() -> None:
    line, unmatched = _single("1001", no=["酸黄瓜"])
    assert unmatched == []
    assert line["notes"] == ["去酸黄瓜"]
    assert line["mods"] == [
        {"code": "M101", "key": "0-0", "quantity": 0},
        {"code": "M102", "key": "0-1", "quantity": 1},
        {"code": "M103", "key": "0-1", "quantity": 1},
    ]
    assert line_item(line)["modification"]["values"][0]["key"] == "0-0"


def test_single_choice_group_sends_only_the_choice() -> None:
    line, _ = _single("3001", with_=["少冰"])
    assert line["mods"] == [{"code": "M301", "key": "1-1", "quantity": 1}]
    line, _ = _single("3001", no=["冰"])  # 「去冰」
    assert line["mods"] == [{"code": "M301", "key": "0-0", "quantity": 1}]


def test_paid_addon_changes_unit_price_in_fen() -> None:
    plain, _ = _single("4001")
    shot, _ = _single("4001", with_=["加一份浓缩"])
    assert shot["unit"] - plain["unit"] == 400
    assert plain["mods"] == []  # 没定制就不传 modification


def test_unknown_customization_is_reported() -> None:
    line, unmatched = _single("2001", no=["酸黄瓜"])
    assert unmatched == ["去「酸黄瓜」"]
    assert line["mods"] == []


def test_combo_choose_and_nested_mod() -> None:
    line, unmatched = _single("9900001001", choose=["大薯条"], no=["酸黄瓜"])
    assert unmatched == []
    assert line["kind"] == "combo"
    assert line["unit"] == 3850 + 200
    item = line_item(line)
    assert [r["round"] for r in item["roundList"]] == ["1", "2", "3"]
    assert item["roundList"][1]["comboItemList"] == [{"code": "2002", "quantity": 1}]
    assert item["roundList"][0]["comboItemList"][0]["modification"]["values"][0] == {"code": "M101", "key": "0-0", "quantity": 0}


def test_commit_hash_is_content_addressed() -> None:
    line, _ = _single("1001")
    kwargs = dict(parent=None, branch="lunch", message="午饭", time="2026-10-24 11:50:00", remote={"storeCode": "1000001"}, lines=[line], price={"price": 2400})
    a = make_commit(**kwargs)
    b = make_commit(**kwargs)
    assert a["id"] == b["id"] and len(a["id"]) == 40
    assert verify_commit(a)
    tampered = copy.deepcopy(a)
    tampered["price"]["price"] = 1
    assert not verify_commit(tampered)
    tampered["pushed"] = {"orderId": "x"}  # pushed 不参与哈希
    assert commit_hash(dict(a, pushed={"orderId": "x"})) == a["id"]


def test_demo_state_is_separate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env = {"MCD_GIT_HOME": str(tmp_path / "cfg")}
    assert state_root(False, env) == tmp_path / "cfg"
    assert state_root(True, env) == tmp_path / "cfg" / "demo"


def test_stage_restore_reset_flow(runner) -> None:  # type: ignore[no-untyped-def]
    code, data = runner.json("--demo", "add", "巨无霸", "中薯条", "中可乐", "--no-pickle")
    assert code == 0 and [x["name"] for x in data["data"]["index"]] == ["巨无霸", "中薯条", "中可乐"]
    assert data["data"]["warnings"] == []
    # 同样的东西再 add 一次是合并数量
    code, data = runner.json("--demo", "add", "中薯条")
    assert [x["qty"] for x in data["data"]["index"]] == [1, 2, 1]
    code, data = runner.json("--demo", "restore", "--staged", "薯条")
    assert code == 0 and [x["name"] for x in data["data"]["index"]] == ["巨无霸", "中可乐"]
    code, out = runner.run("--demo", "restore", "--staged", "麦旋风")
    assert code == 1 and "did not match" in out
    code, out = runner.run("--demo", "reset")
    assert code == 0 and "Unstaged changes after reset" in out
    code, data = runner.json("--demo", "status")
    assert data["data"]["index"] == []


def test_ambiguous_and_unknown_meal(runner) -> None:  # type: ignore[no-untyped-def]
    code, data = runner.json("--demo", "add", "汉堡")
    assert code == 1 and data["ok"] is False
    assert len(data["error"]["detail"]["candidates"]) == 2
    code, out = runner.run("--demo", "add", "巨无霸2")
    assert "did not match any meals" in out and "巨无霸" in out
    code, data = runner.json("--demo", "status")
    assert data["data"]["index"] == []  # 解析失败整条不生效


def test_commit_state_machine(runner) -> None:  # type: ignore[no-untyped-def]
    code, out = runner.run("--demo", "commit", "-m", "空的")
    assert code == 1 and "nothing to commit" in out
    runner.run("--demo", "add", "热拿铁")
    code, data = runner.json("--demo", "commit", "-m", "下午茶")
    assert code == 0
    first = data["data"]["commit"]
    code, data = runner.json("--demo", "status")
    assert data["data"]["index"] == [] and data["data"]["ahead"] == 1
    # HEAD 没 push 不能再 commit
    runner.run("--demo", "add", "菠萝派")
    code, out = runner.run("--demo", "commit", "-m", "再来")
    assert code == 1 and "还没 push" in out
    # --amend 把购物车并进 HEAD，哈希变了
    code, data = runner.json("--demo", "commit", "--amend")
    assert code == 0 and data["data"]["commit"] != first
    assert [x["name"] for x in data["data"]["lines"]] == ["热拿铁", "菠萝派"]
    # reset --soft HEAD~1 撤回，餐品回到购物车
    code, data = runner.json("--demo", "reset", "--soft", "HEAD~1")
    assert code == 0 and [x["name"] for x in data["data"]["index"]] == ["热拿铁", "菠萝派"]
    code, data = runner.json("--demo", "status")
    assert data["data"]["head"] is None


def test_rebase_can_be_undone_with_orig_head(runner) -> None:  # type: ignore[no-untyped-def]
    runner.run("--demo", "add", "巨无霸", "中薯条", "中可乐")
    code, data = runner.json("--demo", "rebase")
    assert code == 0 and data["data"]["changed"]
    code, data = runner.json("--demo", "reset", "ORIG_HEAD")
    assert code == 0 and [x["name"] for x in data["data"]["index"]] == ["巨无霸", "中薯条", "中可乐"]
    code, out = runner.run("--demo", "reset", "ORIG_HEAD")
    assert code == 128 and "ORIG_HEAD" in out


def test_state_file_has_no_token(runner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MCD_MCP_TOKEN", "should-never-be-written")
    runner.run("--demo", "add", "巨无霸")
    files = list((runner.tmp_path / "mcd-git").rglob("*.json"))
    assert files
    for path in files:
        assert "should-never-be-written" not in path.read_text(encoding="utf-8")
        json.loads(path.read_text(encoding="utf-8"))
