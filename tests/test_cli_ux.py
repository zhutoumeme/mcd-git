"""0.2.1 命令行体验：init 门店列表 / --limit / 只给 --city（plan.md T10～T14）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest
from rich.cells import cell_len

from mcd_git.commands.init import _store_columns

from .conftest import Runner, mcp_result

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

def _long_stores(runner: Runner) -> None:
    runner.hooks["query-nearby-stores"] = lambda a, w: mcp_result({"success": True, "code": 200, "message": "请求成功", "data": json.loads(json.dumps(LONG_STORES))})


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
