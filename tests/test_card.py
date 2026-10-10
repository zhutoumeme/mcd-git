"""card：demo 一年合成订单、统计口径、贡献图模型、SVG（自包含、确定、不带个人信息）和命令行为（全部离线）。"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mcd_git import card, stats
from mcd_git.demo import world
from mcd_git.errors import McdError
from mcd_git.ledger import FIELDS
from mcd_git.state import BRANCH_LABELS, parse_time

from .conftest import Runner, mcp_result

ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"
TODAY = date(2026, 10, 24)  # DEMO_NOW，周六
BANNED = ("连续", "streak", "段位", "称号", "大胃王", "抢")
HISTORY_IDS = [h["orderId"] for h in world.HISTORY]


def _demo_entries() -> List[Dict[str, Any]]:
    return world.year_orders() + world.history_entries()


def _cells(svg: str) -> List[Dict[str, str]]:
    """网格里的格子（不含图例）：图例那一行在 y=194。"""
    root = ET.fromstring(svg)
    return [r.attrib for r in root.iter(SVG + "rect") if r.get("class", "").startswith("l") and int(r.get("y", "0")) < 190]


# ---------------------------------------------------------------- demo 一年合成订单


def test_year_orders_are_deterministic_and_modest() -> None:
    orders = world.year_orders()
    assert orders == world.year_orders()
    assert 95 <= len(orders) <= 115
    days: Counter = Counter()
    weeks: Counter = Counter()
    weekend = 0
    breaks = {date(2026, 2, 16), date(2026, 7, 27)}
    for o in orders:
        when = parse_time(o["time"])
        assert when is not None and date(2025, 10, 20) <= when.date() <= date(2026, 10, 18)
        monday = when.date() - timedelta(days=when.weekday())
        assert monday not in breaks  # 春节、暑假各空一周
        assert stats.slot_of(o["time"]) != "night"  # 不生成夜宵
        if when.weekday() >= 5:
            weekend += 1
            assert stats.slot_of(o["time"]) in ("breakfast", "tea")  # 周末只有零星的早餐、下午茶
        assert set(o) - {"orderId"} <= set(FIELDS)
        days[when.date()] += 1
        weeks[monday] += 1
    assert max(days.values()) == 2 and max(weeks.values()) <= 3  # 好看不夸张：同一天最多 2 单、一周最多 3 单
    assert 12 <= weekend <= 24  # 大约每月一两次
    lunch = sum(1 for o in orders if stats.slot_of(o["time"]) == "lunch")
    assert lunch / len(orders) > 0.55
    ids = [o["orderId"] for o in orders]
    assert len(set(ids)) == len(ids) and not set(ids) & set(HISTORY_IDS)
    assert all(re.fullmatch(r"1024\d{24}", i) for i in ids)
    assert {o["state"] for o in orders} <= {"completed", "cancelled"}
    history = world.history_entries()
    assert [(h["paid"], h["original"], h["discount"]) for h in history[:1]] == [(3350, 3850, 500)]


def test_stats_rules() -> None:
    rows = [
        {"time": "2026-10-01 12:00:00", "state": "completed", "items": [{"name": "B", "qty": 2}], "paid": 1000, "discount": 200},
        {"time": "2026-10-02 08:00:00", "state": "completed", "items": [{"name": "A", "qty": 1}, {"name": "C", "qty": 1}], "paid": 500, "discount": None},
        {"time": "2026-10-03 12:30:00", "state": "cancelled", "items": [{"name": "C", "qty": 5}], "paid": 900, "discount": 100},
        {"time": "2026-10-04 23:30:00", "state": "pending", "items": [{"name": "C", "qty": 5}], "paid": 900, "discount": 100},
        {"time": "2026-10-05 08:30:00", "state": "unknown", "items": [{"name": "A", "qty": 1}], "paid": 300, "discount": 0},
    ]
    assert [stats.eaten(r) for r in rows] == [True, True, False, False, True]  # 取消的、没付款的不算
    assert stats.top_items(rows, 3) == [{"name": "A", "count": 2}, {"name": "B", "count": 2}, {"name": "C", "count": 1}]  # 按份数，平票按名字
    assert stats.slot_counts(rows) == {"breakfast": 2, "lunch": 1, "tea": 0, "dinner": 0, "night": 0}
    assert stats.top_slot(rows) == "breakfast"
    assert stats.top_slot(rows[:2]) == "breakfast"  # 早餐、午饭各 1 单：按 早餐 → 夜宵 的顺序取前面的
    assert stats.top_slot(rows[2:4]) is None
    assert stats.money(rows) == {"paid": 1800, "saved": 200, "savedKnown": 2, "savedUnknown": 1}
    assert stats.SLOT_LABELS["night"] == "夜宵" and BRANCH_LABELS["night"] == "夜里"  # log 里的说法不动


# ---------------------------------------------------------------- 模型


def test_grid_has_53_aligned_weeks() -> None:
    model = card.build(_demo_entries(), today=TODAY, with_savings=False, demo=True)
    weeks = model["weeks"]
    assert len(weeks) == 53 and all(len(column) == 7 for column in weeks)
    start = date(2025, 10, 20)
    assert model["window"] == {"start": "2025-10-20", "end": "2026-10-24"}
    for col, column in enumerate(weeks):
        for row, cell in enumerate(column):
            day = start + timedelta(days=col * 7 + row)
            if day > TODAY:
                assert cell is None  # 今天之后不画
            else:
                assert cell["date"] == day.isoformat() and day.weekday() == row  # 周一在第一行
    assert [c is not None for c in weeks[-1]] == [True] * 6 + [False]
    marked = [c["date"] for column in weeks for c in column if c and c["mark"]]
    assert marked == ["2026-10-24"] and model["is1024"] is True
    assert weeks[-1][4]["count"] == 2 and weeks[-1][4]["level"] == 2  # 10-23 早饭 + 午饭
    assert weeks[-1][1]["count"] == 0  # 10-20 那单取消了，不算
    assert weeks[-1][5]["count"] == 0 and weeks[-1][5]["mark"] is True  # 今天（10-24）还没吃，只描边
    assert model["total"] == sum(model["days"].values()) == 111
    assert model["topSlot"]["slot"] == "lunch" and model["topItem"]["name"] == "巨无霸套餐"
    assert model["savings"] is None and model["earliest"] == "2025-10-21"

    friday = card.build([], today=date(2026, 10, 23), with_savings=False, demo=True)
    assert friday["window"]["start"] == "2025-10-20" and friday["mark_date"] == "2025-10-24"
    assert [c is not None for c in friday["weeks"][-1]] == [True] * 5 + [False] * 2
    assert friday["is1024"] is False and "Happy" not in friday["subtitle"]
    sunday = card.build([], today=date(2026, 10, 25), with_savings=False, demo=True)  # 周日还在同一周（周一开头）
    assert sunday["window"]["start"] == "2025-10-20" and sunday["mark_date"] == "2026-10-24"
    assert all(c is not None for c in sunday["weeks"][-1])
    monday = card.build([], today=date(2026, 10, 26), with_savings=False, demo=True)  # 周一开新的一列，窗口整体右移一周
    assert monday["window"]["start"] == "2025-10-27" and monday["mark_date"] == "2026-10-24"
    assert [c is not None for c in monday["weeks"][-1]] == [True] + [False] * 6


def test_levels_and_month_labels() -> None:
    assert [card.level(n) for n in (0, 1, 2, 3, 4, 7)] == [0, 1, 2, 3, 4, 4]
    entries = [{"time": f"2026-10-0{d} 12:00:00", "state": "completed", "items": [{"name": "x", "qty": 1}]} for d in (5, 6, 6, 7, 7, 7)]
    model = card.build(entries, today=TODAY, with_savings=False, demo=False)
    assert [model["weeks"][50][i]["level"] for i in range(4)] == [1, 2, 3, 0]
    months = model["months"]
    assert months[0] == (2, "11月") and months[-1] == (50, "10月")  # 第一列的 10 月只露出两周，标签让给 11 月
    assert all(b[0] - a[0] >= 3 for a, b in zip(months, months[1:]))
    for col, label in months:
        monday = date(2025, 10, 20) + timedelta(weeks=col)
        assert label == f"{monday.month}月" and (monday - timedelta(weeks=1)).month != monday.month


# ---------------------------------------------------------------- SVG


def test_svg_is_self_contained_and_deterministic() -> None:
    model = card.build(_demo_entries(), today=TODAY, with_savings=False, demo=True)
    svg = card.render_svg(model)
    assert svg == card.render_svg(card.build(_demo_entries(), today=TODAY, with_savings=False, demo=True))  # 同数据逐字节相同
    root = ET.fromstring(svg)
    assert root.tag == SVG + "svg" and root.get("role") == "img" and root.get("aria-labelledby") == "t d"
    assert root.find(SVG + "title").text == "过去一年吃麦 111 次"
    assert "2025-10-20 至 2026-10-24" in root.find(SVG + "desc").text
    assert "prefers-color-scheme:dark" in svg and "<!-- 0x400 = 1024 -->" in svg
    for font in ("-apple-system", '"PingFang SC"', '"Microsoft YaHei"', "sans-serif"):
        assert font in svg
    for banned in ("<script", "<image", "<foreignObject", "@import", "url(", "href", "@font-face"):
        assert banned not in svg
    assert re.findall(r"https?://", svg) == ["http://"] and 'xmlns="http://www.w3.org/2000/svg"' in svg  # 除了 xmlns 没有任何外部地址
    assert len(svg.encode("utf-8")) < 40 * 1024 and svg.endswith("</svg>\n")
    cells = _cells(svg)
    assert len(cells) == 52 * 7 + 6
    marked = [c for c in cells if c["class"].endswith(" x")]
    assert marked == [{"class": "l0 x", "x": str(44 + 52 * 13), "y": str(88 + 5 * 13), "width": "10", "height": "10", "rx": "2"}]
    assert {(c["x"], c["y"]) for c in cells} == {(str(44 + col * 13), str(88 + row * 13)) for col in range(53) for row in range(7) if (col, row) != (52, 6)}
    texts = [t.text for t in root.iter(SVG + "text")]
    assert "Happy 0x400 Day · 1024 程序员节快乐" in texts and "10-24 程序员节" in texts
    assert "数据：演示合成数据，最早到 2025-10-21 · git mcd card 生成" in texts
    assert not any("¥" in (t or "") for t in texts)  # 默认不出现金额
    with_savings = card.render_svg(card.build(_demo_entries(), today=TODAY, with_savings=True, demo=True))
    assert "用券省下" in with_savings and "¥79.40" in with_savings


# ---------------------------------------------------------------- 命令


def test_card_in_real_mode_keeps_private_things_off_the_card(runner: Runner, tmp_path: Path) -> None:
    out = tmp_path / "x.svg"
    code, data = runner.json("card", "-o", str(out), mock=True)
    assert code == 0 and data["ok"] is True and data["data"]["status"] == "written"
    assert runner.count("order-list") == 1 and len(runner.calls) == 1  # 只刷一次，不轮询
    assert data["data"]["stats"]["total"] == 4 and data["data"]["stats"]["earliest"] == "2026-10-21"
    assert "savings" not in data["data"]["stats"] and data["data"]["bytes"] == out.stat().st_size
    svg = out.read_text(encoding="utf-8")
    assert "数据：麦当劳最近 10 单 + 本机记录，最早到 2026-10-21" in svg
    blob = json.dumps({k: v for k, v in data["data"].items() if k != "path"}, ensure_ascii=False)  # 临时目录路径不算
    for text in (svg, blob):
        assert not any(oid in text for oid in HISTORY_IDS) and not re.search(r"\d{12,}", text)  # 没有订单号
        assert "演示·" not in text and "示例市" not in text and "138****" not in text  # 没有门店、地址、手机号
        assert not re.search(r"\d{1,2}:\d{2}", text)  # 没有具体下单时刻
        assert "¥" not in text and "实付" not in text and "paid" not in text  # 没有金额
        assert not any(word in text for word in BANNED)
    assert "12150" not in blob  # 4 单实付合计

    code, _ = runner.run("show", HISTORY_IDS[0], mock=True)  # show 顺手记下这一单省了 5 元
    code, data = runner.json("card", "-o", str(out), "--with-savings", mock=True)
    assert code == 0 and data["data"]["stats"]["savings"] == 500 and data["data"]["stats"]["savingsKnownOrders"] == 1
    assert "用券省下" in out.read_text(encoding="utf-8") and "¥5.00" in out.read_text(encoding="utf-8")


def test_demo_card_terminal_preview(runner: Runner, tmp_path: Path) -> None:
    code, out = runner.run("--demo", "card", "-o", str(tmp_path / "c.svg"))
    assert code == 0
    for text in ("过去一年吃麦 111 次", "Happy 0x400 Day · 1024 程序员节快乐", "最常时段 午饭", "最常点 巨无霸套餐", "少 ·░▒▓█ 多", "◆ 10-24 程序员节"):
        assert text in out
    lines = out.splitlines()
    grid = [line for line in lines if re.match(r"^(一  |三  |五  | {4})[·░▒▓█◆]", line)]
    assert len(grid) == 7 and all(len(line) <= 4 + 53 for line in grid)  # 一格一个字符，100 列放得下
    assert re.search(r"^月\s+11\s+12\s+1\s", out, re.M)
    assert out.count("◆") == 2  # 10-24 那格 + 图例
    assert "（" in out and "demo 合成数据" in out and "GitHub Actions" in out
    assert not any(word in out for word in BANNED)
    first = (tmp_path / "c.svg").read_bytes()
    code, data = runner.json("--demo", "card", "-o", str(tmp_path / "d.svg"))
    assert code == 0 and (tmp_path / "d.svg").read_bytes() == first  # 再生成一次，输出一样
    stats_ = data["data"]["stats"]
    assert 95 <= stats_["total"] <= 125 and stats_["topSlot"]["slot"] == "lunch" and stats_["topSlot"]["label"] == "午饭"
    assert not any(word in json.dumps(data, ensure_ascii=False) for word in BANNED)


def test_card_default_output_and_help(runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.chdir(tmp_path)  # 默认写到当前目录：换到临时目录里试，绝不往仓库里写
    code, data = runner.json("--demo", "card")
    assert code == 0 and data["data"]["path"] == "mcd-contributions.svg"
    assert (tmp_path / "mcd-contributions.svg").stat().st_size == data["data"]["bytes"]
    code, data = runner.json("--demo", "card", "-o", str(tmp_path / "nope" / "c.svg"))
    assert code == 128 and "目录不存在" in data["error"]["message"]
    code, _ = runner.run("card", "-h")
    help_text = capsys.readouterr().out
    assert code == 0 and "--with-savings" in help_text and "-o" in help_text and "只读" in help_text


def test_card_with_no_records(runner: Runner, tmp_path: Path) -> None:
    out = tmp_path / "x.svg"
    runner.hooks["order-list"] = lambda args, allow_write: mcp_result({"success": True, "code": 200, "data": {"list": []}})
    code, data = runner.json("card", "-o", str(out), mock=True)
    assert code == 0 and data["data"]["status"] == "empty" and not out.exists()
    code, text = runner.run("card", "-o", str(out), mock=True)
    assert code == 0 and "还没有吃麦记录" in text and "git mcd --demo card" in text and not out.exists()

    def down(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        raise McdError("网络错误：timed out", transport=True)

    runner.hooks["order-list"] = down
    code, data = runner.json("card", "-o", str(out), mock=True)
    assert code == 1 and data["ok"] is False and "order-list 查询失败" in data["error"]["message"] and not out.exists()


def test_card_falls_back_to_ledger_when_order_list_fails(runner: Runner, tmp_path: Path) -> None:
    assert runner.run("log", mock=True)[0] == 0  # 先记下最近 10 单

    def down(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        raise McdError("网络错误：timed out", transport=True)

    runner.hooks["order-list"] = down
    code, data = runner.json("card", "-o", str(tmp_path / "x.svg"), mock=True)
    assert code == 0 and data["data"]["status"] == "written" and data["data"]["stats"]["total"] == 4
    assert data["data"]["warning"].startswith("order-list 查询失败")


def test_card_with_broken_ledger(runner: Runner, tmp_path: Path) -> None:
    path = Path(runner.tmp_path) / "mcd-git" / "ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2", encoding="utf-8")
    code, data = runner.json("card", "-o", str(tmp_path / "x.svg"), mock=True)
    assert code == 128 and data["error"]["type"] == "fatal" and "损坏了" in data["error"]["message"]
    assert any("删掉它" in h for h in data["error"]["hints"]) and not (tmp_path / "x.svg").exists()
