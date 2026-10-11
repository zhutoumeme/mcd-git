"""0.2.1 命令行体验：init 门店列表 / --limit / 只给 --city、600057 提示、子命令打错建议（plan.md T10～T16）。"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest
from rich.cells import cell_len

from mcd_git.cli import _mcd_code_in, _store_closed_hints
from mcd_git.commands.init import _store_columns
from mcd_git.errors import GitMcdError, McdError

from .conftest import FIXED_NOW, Runner, mcp_result

# 合成的长名门店（字段同 demo/world._store），名字、地址都是编的
LONG_STORES: List[Dict[str, Any]] = [
    {
        "storeCode": f"100010{i}",
        "storeName": f"演示·程序员节长名字门店{i}号店",
        "address": f"示例省示例市示例区码农大道 1024 号提交广场 B{i} 座一层{i}0{i} 室",
        "distance": [92, 874, 1269, 1369, 1412][i - 1],
        "businessStatus": True,
        "businessStartTime": "06:30",
        "businessEndTime": "23:00",
        "longitude": 0.0,
        "latitude": 0.0,
        "reservation": False,
        "reservationTimeOptions": [],
    }
    for i in range(1, 6)
]

CLOSED = {"success": False, "code": 600057, "message": "门店可能已关闭或不在营业时间", "traceId": "t" * 32}
CLOSED_HINT = "这家店现在可能没营业（写字楼店、快捷小站周末常不开）"
SWITCH_HINT = "换一家：git mcd init --city <城市> --keyword <地标>"
RESERVE_HINT = "或者预约：git mcd push --at HH:MM（这家店可约：午餐 10:44至14:15，下午茶 14:44至16:45，晚餐 17:14至20:45）"


def _long_stores(runner: Runner) -> None:
    runner.hooks["query-nearby-stores"] = lambda a, w: mcp_result({"success": True, "code": 200, "message": "请求成功", "data": json.loads(json.dumps(LONG_STORES))})


def _closed(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
    return mcp_result(CLOSED)


def _state_path() -> Path:
    return Path(os.environ["MCD_GIT_HOME"]) / "state.json"


def _table_lines(out: str) -> List[str]:
    return [line for line in out.splitlines() if line.startswith("   ") and not line.startswith("hint:")]


# ---------------------------------------------------------------- T10 只给 --city


@pytest.mark.parametrize("mock", [True, False])
def test_city_only_fails_locally(runner: Runner, mock: bool) -> None:
    code, out = runner.run("init", "--city", "南京", mock=mock)
    assert code == 1
    assert "关键词" in out
    assert "git mcd init --city 南京 --keyword 新街口" in out
    assert "只给 --keyword" not in out  # 探测：keyword-only 不可用，不给这条 hint
    assert runner.calls == []

    code, data = runner.json("init", "--city", "南京", mock=mock)
    assert code == 1 and data["ok"] is False
    assert "git mcd init --city 南京 --keyword 新街口" in data["error"]["hints"]
    assert runner.calls == []


# ---------------------------------------------------------------- T11 keyword-only


def test_keyword_only_still_requires_city(runner: Runner) -> None:
    # 0.2.1 探测：只带 keyword 接口同样返回 600058，所以 keyword-only 不放开，本地直接报错
    code, out = runner.run("init", "--keyword", "码农路", mock=True)
    assert code == 1 and "要同时带 --city" in out
    assert runner.calls == []


# ---------------------------------------------------------------- T12 --limit


def test_limit_truncates_locally_and_pick_matches_screen(runner: Runner) -> None:
    _long_stores(runner)
    code, out = runner.run("init", "--city", "示例市", "--keyword", "码农路", "--limit", "3", mock=True)
    assert code == 0
    assert "找到 3 家" in out
    assert len(_table_lines(out)) == 3
    assert "门店3号店" in out and "门店4号店" not in out
    assert runner.count("query-nearby-stores") == 1
    state = json.loads(_state_path().read_text(encoding="utf-8"))
    assert len(state["candidates"]["stores"]) == 3

    code, out = runner.run("init", "--pick", "4", mock=True)
    assert code == 1 and "序号范围 1-3" in out
    code, data = runner.json("init", "--pick", "3", mock=True)
    assert code == 0 and data["data"]["remote"]["storeCode"] == "1000103"
    assert runner.count("query-nearby-stores") == 1  # --pick 用缓存，不再搜


def test_limit_larger_than_returned_says_so(runner: Runner) -> None:
    _long_stores(runner)
    code, out = runner.run("init", "--city", "示例市", "--keyword", "码农路", "--limit", "8", mock=True)
    assert code == 0
    assert len(_table_lines(out)) == 5
    assert "麦当劳接口这次只返回了 5 家；想看别的店换个更具体的地标" in out
    assert runner.count("query-nearby-stores") == 1


def test_limit_equal_or_without_limit_no_note(runner: Runner) -> None:
    _long_stores(runner)
    code, out = runner.run("init", "--city", "示例市", "--keyword", "码农路", "--limit", "5", mock=True)
    assert code == 0 and "只返回了" not in out
    code, out = runner.run("init", "--city", "示例市", "--keyword", "码农路", mock=True)
    assert code == 0 and "只返回了" not in out and len(_table_lines(out)) == 5


@pytest.mark.parametrize("value", ["0", "-1"])
def test_limit_must_be_positive(runner: Runner, value: str) -> None:
    code, out = runner.run("init", "--limit", value, mock=True)
    assert code == 129 and "--limit 至少是 1" in out
    assert runner.calls == []
    code, out = runner.run("init", "--limit", "abc", mock=True)
    assert code == 129


# ---------------------------------------------------------------- T13 列宽


def test_wide_terminal_shows_full_names(runner: Runner) -> None:
    _long_stores(runner)
    code, out = runner.run("init", "--city", "示例市", "--keyword", "码农路", mock=True, width=160)
    assert code == 0
    for store in LONG_STORES:
        assert store["storeName"] in out
        assert store["address"] in out
    assert "…" not in out
    assert "营业中 06:30-23:00" in out
    assert "1412m" in out


def test_narrow_terminal_truncates_address_first(runner: Runner) -> None:
    _long_stores(runner)
    code, out = runner.run("init", "--city", "示例市", "--keyword", "码农路", mock=True, width=80)
    assert code == 0
    for line in out.splitlines():
        assert cell_len(line) <= 80, line
    rows = _table_lines(out)
    assert len(rows) == 5 and all("…" in row for row in rows)
    for store in LONG_STORES:
        assert store["storeName"] in out  # 店名优先保留全称
        assert store["address"] not in out
    assert "营业中 06:30-23:00" in out


def test_very_narrow_terminal_shrinks_name_too() -> None:
    name_w, addr_w = _store_columns([dict(LONG_STORES[0], distance=None, open=None, hours=None)], 40)
    assert addr_w == 10 and name_w == 40 - 2 - 2 * 3 - 10
    name_w, addr_w = _store_columns([dict(LONG_STORES[0], distance=None, open=None, hours=None)], 20)
    assert (name_w, addr_w) == (10, 10)


def test_favorites_have_no_status_column_but_hint(runner: Runner) -> None:
    code, out = runner.run("init", mock=True)
    assert code == 0
    rows = _table_lines(out)
    assert len(rows) == 2
    assert all("营业" not in row for row in rows)
    assert "收藏门店接口不返回营业时间；想看营业状态用 git mcd init --city <城市> --keyword <地标> 搜" in out


def test_search_results_show_business_hours(runner: Runner) -> None:
    code, out = runner.run("init", "--city", "示例市", "--keyword", "码农路", mock=True)
    assert code == 0
    assert "营业中 06:30-23:00" in out
    assert "收藏门店接口不返回营业时间" not in out


# ---------------------------------------------------------------- T14 status / init 结果


def test_status_shows_hours_only_when_known(runner: Runner) -> None:
    assert runner.run("init", "--city", "示例市", "--keyword", "码农路", mock=True)[0] == 0
    code, out = runner.run("init", "--pick", "1", mock=True)
    assert code == 0 and "到店自取 · 营业中 06:30-23:00" in out
    code, out = runner.run("status", mock=True)
    assert code == 0 and "营业中 06:30-23:00" in out

    assert runner.run("init", mock=True)[0] == 0  # 收藏门店没有营业字段
    code, out = runner.run("init", "--pick", "1", mock=True)
    assert code == 0 and "(到店自取)" in out and "营业" not in out
    code, out = runner.run("status", mock=True)
    assert code == 0 and "(到店自取)" in out and "营业" not in out


# ---------------------------------------------------------------- T15 600057


def _pick_store(runner: Runner) -> None:
    assert runner.run("init", "--city", "示例市", "--keyword", "码农路", "--pick", "1", mock=True)[0] == 0


def _assert_closed(out: str, *, reserve: bool = True) -> None:
    assert "code 600057" in out
    assert CLOSED_HINT in out
    assert SWITCH_HINT in out
    assert (RESERVE_HINT in out) is reserve


@pytest.mark.parametrize(
    "argv",
    [("ls-remote",), ("add", "巨无霸"), ("diff",), ("rebase",)],
)
def test_store_closed_hints_on_menu_and_price(runner: Runner, argv: Any) -> None:
    _pick_store(runner)
    if argv[0] in ("diff", "rebase"):
        assert runner.run("add", "巨无霸", mock=True)[0] == 0
    runner.hooks["query-meals"] = _closed
    runner.hooks["calculate-price"] = _closed
    code, out = runner.run(*argv, mock=True)
    assert code != 0
    _assert_closed(out)


def test_store_closed_hints_on_commit_and_push(runner: Runner) -> None:
    _pick_store(runner)
    assert runner.run("add", "巨无霸", mock=True)[0] == 0
    runner.hooks["calculate-price"] = _closed
    code, out = runner.run("commit", "-m", "午饭", mock=True)
    assert code != 0
    _assert_closed(out)

    runner.hooks.clear()
    assert runner.run("commit", "-m", "午饭", mock=True)[0] == 0
    runner.hooks["calculate-price"] = _closed
    code, out = runner.run("push", mock=True)
    assert code != 0
    _assert_closed(out)
    assert runner.count("create-order") == 0

    code, data = runner.json("push", mock=True)
    assert code != 0
    hints = data["error"]["hints"]
    assert CLOSED_HINT in hints and SWITCH_HINT in hints and RESERVE_HINT in hints
    assert len(hints) == len(set(hints))


def test_store_closed_without_reservation_has_no_push_at(runner: Runner) -> None:
    _pick_store(runner)
    path = _state_path()
    state = json.loads(path.read_text(encoding="utf-8"))
    state["remote"]["reservation"] = []
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    runner.hooks["query-meals"] = _closed
    code, out = runner.run("ls-remote", mock=True)
    assert code != 0
    _assert_closed(out, reserve=False)
    assert "push --at" not in out


def test_other_error_codes_get_no_store_closed_hint(runner: Runner) -> None:
    _pick_store(runner)
    runner.hooks["query-meals"] = lambda a, w: mcp_result({"success": False, "code": 60006, "message": "商品已售罄或不可售"})
    code, out = runner.run("ls-remote", mock=True)
    assert code != 0 and "code 60006" in out
    assert CLOSED_HINT not in out and "换一家" not in out


def test_mcd_code_in_and_hint_helpers() -> None:
    assert _mcd_code_in(McdError("x", code=600057)) == "600057"
    assert _mcd_code_in(McdError("x")) is None
    assert _mcd_code_in(GitMcdError("x", remote=["门店可能已关闭（code 600057，trace abc）"])) == "600057"
    assert _mcd_code_in(GitMcdError("x", remote=["code 600057 · trace abc"])) == "600057"
    assert _mcd_code_in(GitMcdError("x", remote=["code 6000570"])) == "6000570"
    assert _mcd_code_in(GitMcdError("x")) is None
    assert _store_closed_hints(None) == [CLOSED_HINT, SWITCH_HINT]
    delivery = {"mode": "delivery", "reservation": [{"date": "d", "today": True, "slots": [{"name": "午餐", "ranges": [["11:00", "14:59"]]}]}]}
    assert _store_closed_hints(delivery) == [CLOSED_HINT, SWITCH_HINT]
    remote = {
        "mode": "drive",
        "reservation": [
            {"date": "d1", "today": False, "slots": [{"name": "早餐", "ranges": [["06:44", "10:15"]]}]},
            {"date": "d2", "today": True, "slots": [{"name": "宵夜", "ranges": [["00:00", "04:45"], ["22:14", "23:59"]]}, {"name": "空", "ranges": []}]},
        ],
    }
    assert _store_closed_hints(remote)[-1] == "或者预约：git mcd push --at HH:MM（这家店可约：宵夜 00:00至04:45）"
    remote["reservation"][1]["today"] = False
    assert _store_closed_hints(remote)[-1] == "或者预约：git mcd push --at HH:MM（这家店可约：早餐 06:44至10:15）"


def test_store_closed_reservation_snapshot_drops_past_days() -> None:
    # 快照是 init 那天存的：today 标记已经不准，按 date 和今天比
    remote = {
        "mode": "pickup",
        "reservation": [
            {"date": "2026-10-24", "today": True, "slots": [{"name": "午餐", "ranges": [["10:44", "14:15"]]}]},
            {"date": "2026-10-25", "today": False, "slots": [{"name": "早餐", "ranges": [["06:44", "10:15"]]}]},
        ],
    }
    assert _store_closed_hints(remote, "2026-10-24")[-1] == "或者预约：git mcd push --at HH:MM（这家店可约：午餐 10:44至14:15）"
    assert _store_closed_hints(remote, "2026-10-25")[-1] == "或者预约：git mcd push --at HH:MM（这家店可约：早餐 06:44至10:15）"  # 快照里 today 还标在 10-24
    remote["reservation"][1]["today"] = True  # 快照里标错了也按日期挑今天
    assert _store_closed_hints(remote, "2026-10-24")[-1].startswith("或者预约：git mcd push --at HH:MM（这家店可约：午餐")
    assert _store_closed_hints(remote, "2026-10-27") == [CLOSED_HINT, SWITCH_HINT]  # 全过期了就不提预约
    future = {"mode": "drive", "reservation": remote["reservation"][1:]}  # 今天没有时段、明天有：写明日期
    assert _store_closed_hints(future, "2026-10-24")[-1] == "或者预约：git mcd push --at '2026-10-25 HH:MM'（这家店 2026-10-25 可约：早餐 06:44至10:15）"


def test_store_closed_days_after_init_has_no_stale_push_at(runner: Runner) -> None:
    _pick_store(runner)  # 选店那天是 2026-10-24（FIXED_NOW），快照里只有 10-24、10-25 两天
    runner.hooks["query-meals"] = _closed
    later = FIXED_NOW + timedelta(days=3)
    code, out = runner.run("ls-remote", mock=True, now=later)
    assert code != 0
    _assert_closed(out, reserve=False)
    assert "push --at" not in out


# ---------------------------------------------------------------- T16 子命令打错


def test_typo_subcommand_suggests(runner: Runner) -> None:
    code, out = runner.run("ls-remotr", mock=True)
    assert code == 129
    assert "你是不是想输入：git mcd ls-remote" in out
    code, data = runner.json("ls-remotr", mock=True)
    assert code == 129
    assert "你是不是想输入：git mcd ls-remote" in data["error"]["hints"]
    code, out = runner.run("--demo", "comit", "-m", "x")
    assert code == 129 and "你是不是想输入：git mcd commit" in out
    assert runner.calls == []


def test_unknown_subcommand_without_close_match(runner: Runner) -> None:
    code, out = runner.run("xyzzy", mock=True)
    assert code == 129
    assert "你是不是想输入" not in out
    assert runner.calls == []


def test_bad_option_on_valid_subcommand_no_suggestion(runner: Runner) -> None:
    code, out = runner.run("init", "--bogus", mock=True)
    assert code == 129 and "你是不是想输入" not in out


def test_help_typo_suggests(runner: Runner) -> None:
    code, out = runner.run("help", "ls-remotr")
    assert code == 1
    assert "你是不是想输入：git mcd ls-remote" in out
    assert "git mcd help 看全部命令" in out
    code, data = runner.json("help", "ls-remotr")
    assert data["error"]["hints"] == ["你是不是想输入：git mcd ls-remote", "git mcd help 看全部命令"]
    code, out = runner.run("help", "xyzzy")
    assert "你是不是想输入" not in out and "git mcd help 看全部命令" in out
