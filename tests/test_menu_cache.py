"""0.2.4 本机菜单缓存（给命令行补全用）：只在本来就调 query-meals 的命令里顺手写，不多调 MCP；
文件 600 / 目录 700，只存 code / name / type 和门店范围；过期、换店、文件坏了都不给候选。"""
from __future__ import annotations

import json
import os
import stat
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mcd_git import menucache
from mcd_git.state import CN_TZ

from .conftest import FIXED_NOW, Runner

REMOTE: Dict[str, Any] = {"storeCode": "1450001", "mode": "pickup", "beType": 1, "orderType": 1, "beCode": None, "addressId": "A-1", "addressHint": "某某路 1 号"}


def _home() -> Path:
    return Path(os.environ["MCD_GIT_HOME"])


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _meals(*names: str) -> List[Dict[str, str]]:
    return [{"code": str(9000 + i), "name": n, "type": "套餐" if "套餐" in n else "单品"} for i, n in enumerate(names)]


# ---------------------------------------------------------------- 写入


def test_demo_ls_remote_writes_private_cache_with_only_names(runner: Runner) -> None:
    code, _ = runner.run("--demo", "ls-remote")
    assert code == 0
    path = _home() / "demo" / "menu-cache.json"
    assert path.is_file()
    assert _mode(path) == 0o600 and _mode(path.parent) == 0o700
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert set(data) == {"version", "storeCode", "scope", "writtenAt", "meals"}
    assert set(data["scope"]) == {"mode", "beType", "orderType", "beCode"}
    assert data["meals"] and all(set(m) == {"code", "name", "type"} for m in data["meals"])
    assert {m["type"] for m in data["meals"]} == {"套餐", "单品"}
    assert any(m["name"] == "巨无霸套餐" and m["type"] == "套餐" for m in data["meals"])
    for word in ("price", "Price", "addressId", "addressHint", "address", "Token"):
        assert word not in text
    assert not (_home() / "menu-cache.json").exists()  # 真实模式那份不碰


def test_real_mode_writes_cache_without_extra_mcp_calls(runner: Runner) -> None:
    assert runner.run("init", "--city", "示例市", "--keyword", "码农路", "--pick", "1", mock=True)[0] == 0
    assert runner.count("query-meals") == 0  # init 不调 query-meals，也不为缓存新增调用
    seen = []

    def step(*argv: str) -> None:
        before = runner.count("query-meals")
        code, out = runner.run(*argv, mock=True)
        assert code == 0, (argv, out)
        seen.append(runner.count("query-meals") - before)

    step("ls-remote")
    step("add", "巨无霸")
    step("rebase")
    assert runner.run("commit", "-m", "午饭", mock=True)[0] == 0
    code, data = runner.json("push", mock=True)  # mock 的合成世界下单，cherry-pick 要一个 push 过的 commit
    assert runner.json("push", "--confirm", data["data"]["confirm"], mock=True)[0] == 0
    step("cherry-pick", "HEAD")  # 同一家店：直接拷快照，本来就不查菜单
    assert runner.run("reset", mock=True)[0] == 0
    head = json.loads((_home() / "state.json").read_text(encoding="utf-8"))["head"]
    assert runner.run("init", "--city", "示例市", "--keyword", "码农路", "--pick", "2", mock=True)[0] == 0
    step("cherry-pick", head[:7])  # 换了店：按新店菜单对一遍
    assert seen == [1, 1, 1, 0, 1]  # 每条命令的 query-meals 次数照旧
    path = _home() / "menu-cache.json"
    assert path.is_file() and _mode(path) == 0o600
    assert not (_home() / "demo" / "menu-cache.json").exists()
    state = json.loads((_home() / "state.json").read_text(encoding="utf-8"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["storeCode"] == state["remote"]["storeCode"]
    meals, status = menucache.load(_home(), state["remote"], FIXED_NOW)
    assert status == "ok" and meals and any(m["name"] == "巨无霸" for m in meals)


def test_cache_write_failure_does_not_break_add(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("磁盘满了")

    monkeypatch.setattr(menucache, "write", broken)
    code, out = runner.run("--demo", "add", "巨无霸")
    assert code == 0, out
    assert not (_home() / "demo" / "menu-cache.json").exists()


def test_write_strips_names_and_rejects_bad_type(tmp_path: Path) -> None:
    root = tmp_path / "c"
    menucache.write(root, REMOTE, [{"code": "1", "name": "  巨无霸\t套餐 \n", "type": "套餐"}], FIXED_NOW)
    data = json.loads((root / "menu-cache.json").read_text(encoding="utf-8"))
    assert data["meals"] == [{"code": "1", "name": "巨无霸 套餐", "type": "套餐"}]
    assert "addressId" not in json.dumps(data) and "A-1" not in json.dumps(data)
    with pytest.raises(ValueError):
        menucache.write(root, REMOTE, [{"code": "1", "name": "x", "type": "其他"}], FIXED_NOW)
    assert [p.name for p in root.iterdir()] == ["menu-cache.json"]  # 没留下临时文件


# ---------------------------------------------------------------- 读取


def test_load_states(tmp_path: Path) -> None:
    root = tmp_path / "c"
    assert menucache.load(root, REMOTE, FIXED_NOW) == (None, "missing")
    assert menucache.load(root, None, FIXED_NOW) == (None, "no_remote")
    menucache.write(root, REMOTE, _meals("巨无霸"), FIXED_NOW)
    meals, status = menucache.load(root, REMOTE, FIXED_NOW + timedelta(hours=23))
    assert status == "ok" and meals == _meals("巨无霸")
    assert menucache.load(root, REMOTE, FIXED_NOW + timedelta(hours=25)) == (None, "expired")
    assert menucache.load(root, REMOTE, FIXED_NOW - timedelta(minutes=10)) == (None, "expired")
    assert menucache.load(root, REMOTE, FIXED_NOW - timedelta(minutes=3))[1] == "ok"  # 允许一点时钟偏差
    assert menucache.load(root, dict(REMOTE, storeCode="999"), FIXED_NOW) == (None, "store_mismatch")
    assert menucache.load(root, dict(REMOTE, beType=5, beCode="X"), FIXED_NOW) == (None, "store_mismatch")
    assert menucache.load(root, None, FIXED_NOW) == (None, "no_remote")
    path = root / "menu-cache.json"
    path.write_text("{坏了", encoding="utf-8")
    assert menucache.load(root, REMOTE, FIXED_NOW) == (None, "corrupt")
    path.write_text(json.dumps({"version": 1, "storeCode": "1450001", "scope": {}, "writtenAt": "x", "meals": [{"code": 1}]}), encoding="utf-8")
    assert menucache.load(root, REMOTE, FIXED_NOW) == (None, "corrupt")
    path.write_text(json.dumps({"version": 1, "storeCode": "1450001", "scope": {k: REMOTE[k] for k in menucache.SCOPE_KEYS}, "writtenAt": "不是时间", "meals": []}), encoding="utf-8")
    assert menucache.load(root, REMOTE, FIXED_NOW) == (None, "expired")


# ---------------------------------------------------------------- 匹配

MENU = _meals(
    "巨无霸",
    "麦辣鸡腿堡",
    "板烧鸡腿堡",
    "巨无霸套餐",
    "鸡腿堡套餐",
    "麦咖啡™美式",
    "麦咖啡®拿铁(大)",
    "麦乐鸡（5块）",
    "Coca-Cola 中杯",
)


def _names(items: List[Dict[str, Any]]) -> List[str]:
    return [i["name"] for i in items]


def test_match_prefix_before_contains() -> None:
    assert _names(menucache.match(MENU, "巨无霸")) == ["巨无霸", "巨无霸套餐"]
    assert _names(menucache.match(MENU, "鸡腿")) == ["鸡腿堡套餐", "麦辣鸡腿堡", "板烧鸡腿堡"]
    assert _names(menucache.match(MENU, "")) == _names(MENU)


def test_match_mccafe_prefix_survives_normalize() -> None:
    # textnorm.normalize 会去掉开头的「麦咖啡」，只用它的话「麦咖」补不出来
    assert _names(menucache.match(MENU, "麦咖")) == ["麦咖啡™美式", "麦咖啡®拿铁(大)"]
    assert _names(menucache.match(MENU, "美式")) == ["麦咖啡™美式"]


def test_match_brackets_case_quotes_and_escapes() -> None:
    assert _names(menucache.match(MENU, "麦乐鸡(5")) == ["麦乐鸡（5块）"]  # 半角括号匹配全角
    assert _names(menucache.match(MENU, "拿铁（大")) == ["麦咖啡®拿铁(大)"]
    assert _names(menucache.match(MENU, "coca")) == ["Coca-Cola 中杯"]
    assert _names(menucache.match(MENU, "Coca-Cola\\ 中")) == ["Coca-Cola 中杯"]
    assert _names(menucache.match(MENU, "'Coca-Cola 中")) == ["Coca-Cola 中杯"]
    assert _names(menucache.match(MENU, '"巨无霸')) == ["巨无霸", "巨无霸套餐"]


def test_match_dedup_limit_and_codes(tmp_path: Path) -> None:
    menucache.write(tmp_path, REMOTE, [{"code": "1", "name": "巨无霸  ", "type": "单品"}, {"code": "2", "name": "巨无霸", "type": "单品"}], FIXED_NOW)
    meals, _ = menucache.load(tmp_path, REMOTE, FIXED_NOW)
    assert meals is not None and [m["name"] for m in meals] == ["巨无霸", "巨无霸"]  # 尾随空格写入时已去掉
    assert [(i["code"], i["name"]) for i in menucache.match(meals, "巨")] == [("1", "巨无霸")]
    many = [{"code": str(100 + i), "name": f"汉堡{i}", "type": "单品"} for i in range(80)]
    assert len(menucache.match(many, "汉堡")) == 50
    assert len(menucache.match(many, "汉堡", limit=5)) == 5
    hits = menucache.match(many, "10", codes=True)
    codes = [h["code"] for h in hits if h["kind"] == "code"]
    assert codes == [str(c) for c in range(100, 110)] and hits[: len(codes)] == [h for h in hits if h["kind"] == "code"]
    assert all(h["kind"] == "name" for h in menucache.match(many, "10"))  # 不开 codes 就不按 code 补
    assert _names(menucache.match(many, "10")) == ["汉堡10"]
