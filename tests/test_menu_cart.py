"""0.2.1 购物车与菜单：条件优惠价不当单价（plan.md T17～T18）。

夹具在 demo 合成菜单上加一项「随单购麦金卡优惠」（current ¥37 / original ¥66，形态照 2026-10-11 真实探测），
中可乐本来就是「促销优惠」（现价 ¥8 / 原价 ¥10）。名字和 code 都是编的或 demo 里的。
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
from rich.cells import cell_len
from rich.console import Console

from mcd_git.api import Menu
from mcd_git.cli import main
from mcd_git.demo import world

from .conftest import FIXED_NOW, Runner, mcp_result

OFFER_CODE = "9900000882"
OFFER_NAME = "芝士安格斯厚牛堡四件套"
OFFER_NOTE = "¥37.00 需随单购麦金卡"
LONG_CODE = "9900000999"
LONG_NAME = "演示·程序员节超长名字的豪华分享桶（含麦辣鸡翅、麦乐鸡、中薯条和两杯中可乐）"


def _ok(data: Any) -> Dict[str, Any]:
    return mcp_result({"success": True, "code": 200, "message": "请求成功", "data": data})


def _menu_payload() -> Dict[str, Any]:
    payload = copy.deepcopy(world.meals_payload("pickup"))
    payload["meals"][OFFER_CODE] = {
        "name": OFFER_NAME,
        "image": "",
        "currentPrice": "37",
        "originalPrice": "66",
        "discountType": "随单购麦金卡优惠",
        "canWithOrder": True,
        "withOrder": {"membershipCode": "M0001", "specId": "S0001"},
    }
    # 不在任何分类里的长名餐品（ls-remote 归到「其他」）
    payload["meals"][LONG_CODE] = {"name": LONG_NAME, "image": "", "currentPrice": "99", "originalPrice": "99", "canWithOrder": False}
    payload["categories"].insert(0, {"name": "人气\n热卖", "meals": [{"code": OFFER_CODE, "tags": ["套餐"]}, {"code": "3001", "tags": ["单品"]}]})
    # 同一 code 挂在多个分类里（实测 150 条引用 / 137 个 code）
    combos = next(c for c in payload["categories"] if c["name"] == "超值\n套餐")
    combos["meals"].append({"code": OFFER_CODE, "tags": ["套餐"]})
    return payload


def _offer_detail(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
    code = str(args.get("code"))
    if code == OFFER_CODE:
        return _ok({"code": code, "name": OFFER_NAME, "image": "", "supportModify": False, "modification": None, "rounds": []})
    return _ok(world.detail_payload("pickup", code))


def _offer_price(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
    # 实测：不同时买随单购麦金卡时按原价 ¥66 算
    qty = sum(int(i.get("quantity") or 1) for i in args.get("items") or [])
    products = [{"productCode": OFFER_CODE, "productName": OFFER_NAME, "quantity": qty, "subtotal": 6600 * qty, "originalSubtotal": 6600 * qty}]
    return _ok({"price": 6600 * qty, "originalPrice": 6600 * qty, "discount": 0, "productList": products})


def _with_offer_menu(runner: Runner) -> None:
    assert runner.run("init", "--city", "示例市", "--keyword", "码农路", "--pick", "1", mock=True)[0] == 0
    runner.hooks["query-meals"] = lambda a, w: _ok(_menu_payload())
    runner.hooks["query-meal-detail"] = _offer_detail


def _state_path() -> Path:
    return Path(os.environ["MCD_GIT_HOME"]) / "state.json"


# ---------------------------------------------------------------- T17 条件优惠价


def test_menu_conditional_offer_uses_original_price() -> None:
    menu = Menu(_menu_payload())
    item = menu.get(OFFER_CODE)
    assert item["price"] == 6600 and item["original"] == 6600
    assert item["offer"] == {"price": 3700, "type": "随单购麦金卡优惠", "note": OFFER_NOTE}
    cola = menu.get("3001")  # 促销优惠：直接生效
    assert cola["price"] == 800 and cola["original"] == 1000 and cola["offer"] is None
    assert menu.categories[0] == ("人气热卖", [OFFER_CODE, "3001"])


def test_menu_offer_ignored_when_not_cheaper_or_unconditional() -> None:
    menu = Menu(
        {
            "meals": {
                "1": {"name": "甲", "currentPrice": "66", "originalPrice": "66", "discountType": "随单购麦金卡优惠"},
                "2": {"name": "乙", "currentPrice": "37", "discountType": "随单购麦金卡优惠"},
                "3": {"name": "丙", "currentPrice": "13.9", "originalPrice": "21", "discountType": "促销优惠"},
            },
            "categories": [],
        }
    )
    assert [(menu.get(c)["price"], menu.get(c)["offer"]) for c in "123"] == [(6600, None), (3700, None), (1390, None)]


def test_ls_remote_shows_real_price_with_offer_note(runner: Runner) -> None:
    _with_offer_menu(runner)
    code, out = runner.run("ls-remote", mock=True, width=160)  # 夹具里有 70 格的长名，宽一点免得条件说明折行
    assert code == 0
    row = next(line for line in out.splitlines() if OFFER_NAME in line)
    assert "¥66.00" in row and OFFER_NOTE in row and "原价" not in row
    cola = next(line for line in out.splitlines() if "中可乐" in line)
    assert "¥8.00" in cola and "原价 ¥10.00 促销优惠" in cola

    code, data = runner.json("ls-remote", mock=True)
    meal = next(m for m in data["data"]["meals"] if m["code"] == OFFER_CODE)
    assert meal["price"] == 6600 and meal["offerPrice"] == 3700 and meal["offerNote"] == OFFER_NOTE
    cola = next(m for m in data["data"]["meals"] if m["code"] == "3001")
    assert cola["price"] == 800 and cola["offerPrice"] is None and cola["offerNote"] is None


def test_add_and_status_show_real_price_with_offer_note(runner: Runner) -> None:
    _with_offer_menu(runner)
    code, out = runner.run("add", OFFER_CODE, "中可乐", mock=True)
    assert code == 0
    row = next(line for line in out.splitlines() if OFFER_NAME in line)
    assert "¥66.00" in row and f"（{OFFER_NOTE}）" in row
    cola = next(line for line in out.splitlines() if "中可乐" in line)
    assert "¥8.00" in cola and "需" not in cola

    code, out = runner.run("status", mock=True)
    assert code == 0
    row = next(line for line in out.splitlines() if OFFER_NAME in line)
    assert "¥66.00" in row and f"（{OFFER_NOTE}）" in row

    code, data = runner.json("status", mock=True)
    lines = {line["code"]: line for line in data["data"]["index"]}
    assert lines[OFFER_CODE]["unitPrice"] == 6600 and lines[OFFER_CODE]["offerNote"] == OFFER_NOTE
    assert lines["3001"]["unitPrice"] == 800 and "offerNote" not in lines["3001"]


# ---------------------------------------------------------------- T18 旧状态兼容


def test_old_state_lines_without_offer_note_still_work(runner: Runner) -> None:
    _with_offer_menu(runner)
    assert runner.run("add", OFFER_CODE, mock=True)[0] == 0
    path = _state_path()
    state = json.loads(path.read_text(encoding="utf-8"))
    for line in state["index"]:
        line.pop("offerNote", None)  # 0.2.0 写的状态没有这个键
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    runner.hooks["calculate-price"] = _offer_price

    code, out = runner.run("status", mock=True)
    assert code == 0 and OFFER_NAME in out and "需随单购" not in out
    code, out = runner.run("diff", mock=True)
    assert code == 0 and "¥66.00" in out
    code, out = runner.run("commit", "-m", "旧状态也能提交", mock=True)
    assert code == 0 and "旧状态也能提交" in out


# ---------------------------------------------------------------- T19 ls-remote 分组 / 列宽 / 分页


def _tty_run(runner: Runner, *argv: str, height: int = 10, width: int = 120) -> Tuple[int, str]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, height=height, width=width, highlight=False, markup=False, emoji=False)
    code = main(list(argv), console=console, env=dict(os.environ), client_factory=runner.factory, input_fn=lambda p: "", interactive=False, now=FIXED_NOW)
    return code, buf.getvalue()


@pytest.fixture
def pager_calls(monkeypatch: pytest.MonkeyPatch) -> List[bool]:
    calls: List[bool] = []

    @contextlib.contextmanager
    def fake_pager(self: Console, pager: Any = None, styles: bool = False, links: bool = False) -> Iterator[None]:
        calls.append(styles)
        yield

    monkeypatch.setattr(Console, "pager", fake_pager)
    monkeypatch.setenv("LESS", "x")
    monkeypatch.delenv("LESS")  # 测完恢复原样
    return calls


def test_ls_remote_groups_by_category_without_keyword(runner: Runner) -> None:
    _with_offer_menu(runner)
    code, out = runner.run("ls-remote", mock=True)
    assert code == 0
    lines = out.splitlines()
    headers = [line for line in lines if line.startswith("## ")]
    assert headers == ["## 人气热卖（2）", "## 超值套餐（4）", "## 汉堡（4）", "## 小食（3）", "## 甜品（1）", "## 饮品（2）", "## 咖啡（2）", "## 其他（1）"]
    offer_rows = [i for i, line in enumerate(lines) if OFFER_NAME in line]
    assert len(offer_rows) == 1  # 挂在两个分类里，只在第一个出现
    assert lines.index("## 人气热卖（2）") < offer_rows[0] < lines.index("## 超值套餐（4）")
    assert lines.index("## 其他（1）") < next(i for i, line in enumerate(lines) if LONG_CODE in line)
    assert "hint: git mcd ls-remote <关键词> 搜索" in out

    code, data = runner.json("ls-remote", mock=True)
    assert len(data["data"]["meals"]) == len(_menu_payload()["meals"])  # JSON 全量、不分组
    assert {m["code"]: m["category"] for m in data["data"]["meals"]}[OFFER_CODE] == "人气热卖"


def test_ls_remote_with_keyword_stays_flat(runner: Runner) -> None:
    _with_offer_menu(runner)
    code, out = runner.run("ls-remote", "可乐", mock=True)
    assert code == 0
    assert "## " not in out and "hint:" not in out
    assert "中可乐" in out and "大可乐" in out


def test_ls_remote_name_column_follows_terminal_width(runner: Runner) -> None:
    _with_offer_menu(runner)
    code, out = runner.run("ls-remote", mock=True, width=160)
    assert code == 0
    row = next(line for line in out.splitlines() if LONG_CODE in line)
    assert LONG_NAME in row and "…" not in row and "¥99.00" in row
    code, out = runner.run("ls-remote", mock=True, width=80)
    row = next(line for line in out.splitlines() if LONG_CODE in line)
    assert LONG_NAME not in row and "…" in row and "¥99.00" in row
    assert cell_len(row) <= 80


def test_ls_remote_pages_only_on_tty_longer_than_screen(runner: Runner, pager_calls: List[bool]) -> None:
    _with_offer_menu(runner)
    code, out = _tty_run(runner, "ls-remote", height=10)
    assert code == 0 and pager_calls == [True] and OFFER_NAME in out
    assert os.environ.get("LESS") == "FRX"
    code, _ = _tty_run(runner, "ls-remote", "可乐", height=40)  # 一屏放得下
    assert code == 0 and pager_calls == [True]
    code, _ = runner.run("ls-remote", mock=True)  # 非 tty
    assert code == 0 and pager_calls == [True]
    code, _ = _tty_run(runner, "ls-remote", "--json", height=10)
    assert code == 0 and pager_calls == [True]
