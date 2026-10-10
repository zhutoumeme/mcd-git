"""card --style：麦门天际线（默认）、年度吃麦小票、平面格子。SVG 自包含、确定、不带个人信息，10-24 有小旗 / 红圈（全部离线）。"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mcd_git import card, card_receipt, card_skyline
from mcd_git.demo import world

from .conftest import Runner

ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"
TODAY = date(2026, 10, 24)
STYLES = ("skyline", "receipt", "grid")
RENDER = {"skyline": card_skyline.render_svg, "receipt": card_receipt.render_svg, "grid": card.render_svg}
BANNED = ("连续", "streak", "段位", "称号", "大胃王", "抢")
HISTORY_IDS = [h["orderId"] for h in world.HISTORY]


def _demo_model(**kw: Any) -> Dict[str, Any]:
    return card.build(world.year_orders() + world.history_entries(), today=TODAY, with_savings=kw.pop("with_savings", False), demo=True, **kw)


def _order(day: str, name: str = "巨无霸套餐", qty: int = 1, hour: int = 12, discount: Any = None) -> Dict[str, Any]:
    return {"time": f"{day} {hour:02d}:10:00", "state": "completed", "items": [{"name": name, "qty": qty}], "paid": 3000, "discount": discount}


def _texts(svg: str) -> List[str]:
    return [t.text or "" for t in ET.fromstring(svg).iter(SVG + "text")]


def _assert_self_contained(svg: str) -> None:
    root = ET.fromstring(svg)  # 能被 xml.etree 解析
    assert root.tag == SVG + "svg" and root.get("role") == "img" and root.get("aria-labelledby") == "t d"
    assert root.find(SVG + "title").text and root.find(SVG + "desc").text
    for banned in ("<script", "<image", "<foreignObject", "@import", "@font-face", "xlink"):
        assert banned not in svg
    assert re.findall(r"https?://", svg) == ["http://"] and 'xmlns="http://www.w3.org/2000/svg"' in svg  # 除了 xmlns 没有任何外部地址
    assert all(ref.startswith("#") for ref in re.findall(r'href="([^"]*)"', svg))  # <use> 只引用文档内部
    assert all(ref.startswith("#") for ref in re.findall(r"url\(([^)]*)\)", svg))
    ids = re.findall(r'\bid="([^"]+)"', svg)
    assert len(ids) == len(set(ids))  # id 不重复，#引用不会指错
    assert set(re.findall(r'href="#([^"]+)"', svg)) <= set(ids)
    assert svg.endswith("</svg>\n")


@pytest.mark.parametrize("style", STYLES)
def test_every_style_is_self_contained_and_deterministic(style: str) -> None:
    svg = RENDER[style](_demo_model())
    _assert_self_contained(svg)
    assert svg == RENDER[style](_demo_model())  # 同样数据两次生成逐字节相同
    assert "prefers-color-scheme" in svg
    assert "¥" not in svg and "用券省下" not in svg  # 默认不出现金额
    assert not any(word in svg for word in BANNED)


def test_skyline_burgers_flag_and_animation() -> None:
    model = _demo_model()
    svg = card_skyline.render_svg(model)
    assert len(svg.encode("utf-8")) <= 100 * 1024
    root = ET.fromstring(svg)
    assert root.find(SVG + "title").text == "麦门天际线：过去一年吃麦 111 次"
    texts = _texts(svg)
    assert "麦门天际线" in texts and "$ git mcd card  # 每天吃几次，就叠几层汉堡" in texts
    assert {"过去一年", "111 次", "最常点", "巨无霸套餐", "最常时段", "lunch · 午饭", "10-24", "Happy 0x400 Day"} <= set(texts)
    assert "数据：演示合成数据，最早到 2025-10-21 · git mcd card 生成" in texts
    days = sum(1 for column in model["weeks"] for c in column if c is not None)
    assert svg.count('href="#tile"') == days == 52 * 7 + 6  # 每天一块地砖，没订单的日子也有
    layers = sum(min(c["count"], 4) for column in model["weeks"] for c in column if c)
    assert svg.count('href="#b"') == layers and svg.count('href="#c"') == sum(1 for column in model["weeks"] for c in column if c and c["count"])
    # 10-24（今天，还没吃）照样插旗；旗子在最后一列周六那块地砖上
    assert svg.count('href="#fl"') == 1 and ">0x400</text>" in svg
    x, y = card_skyline._top_center(52, 5, 0)
    assert f'<use href="#fl" x="{x}" y="{y}"/>' in svg and svg.count('class="tm"') == 1
    assert 'href="#p"' not in svg  # demo 里同一天最多 2 单，不用「+」
    # 纯 CSS 动画：只播一次、fill-mode both、总时长 ≤ 3 秒；reduced-motion 关掉；默认深色、light 换奶油底
    css = svg.split("<style>")[1].split("</style>")[0]
    assert "@media (prefers-reduced-motion:reduce){.f{animation:none}}" in css
    assert "@media (prefers-color-scheme:light)" in css and "both" in css and "infinite" not in css
    duration = float(re.search(r"animation:drop ([0-9.]+)s", css).group(1))
    delays = [float(d) for d in re.findall(r"animation-delay:([0-9.]+)s", svg)]
    assert delays and max(delays) + duration <= 3.0


def test_skyline_caps_at_four_layers_with_plus() -> None:
    entries = [_order("2026-10-12", hour=8 + i) for i in range(6)] + [_order("2026-10-13") for _ in range(4)] + [_order("2026-10-14")]
    model = card.build(entries, today=TODAY, with_savings=False, demo=False)
    svg = card_skyline.render_svg(model)
    assert svg.count('href="#p"') == 1  # 只有 6 单那天加「+」，正好 4 单那天不加
    assert svg.count('href="#b"') == 4 + 4 + 1  # 6 单也只叠 4 层
    x, y = card_skyline._origin(51, 0)
    assert all(f'<use href="#b" x="{x}" y="{y - i * card_skyline.LAYER}"/>' in svg for i in range(4))
    assert f'<use href="#b" x="{x}" y="{y - 4 * card_skyline.LAYER}"/>' not in svg


def test_skyline_stays_under_100kb_even_when_every_day_is_busy() -> None:
    start = card.window_start(TODAY)
    entries = [_order((start + timedelta(days=i)).isoformat(), hour=8 + k) for i in range((TODAY - start).days + 1) for k in range(5)]
    svg = card_skyline.render_svg(card.build(entries, today=TODAY, with_savings=True, demo=False))
    assert len(svg.encode("utf-8")) <= 100 * 1024
    _assert_self_contained(svg)


def test_receipt_top5_order_truncation_and_ring() -> None:
    long_name = "超级无敌豪华双层芝士安格斯厚牛堡大套餐配薯条"
    entries = (
        [_order("2026-09-01", "菠萝派", 3)]
        + [_order("2026-09-02", long_name, 5)]
        + [_order("2026-09-03", "热拿铁", 2), _order("2026-09-04", "美式咖啡", 2)]
        + [_order("2026-09-07", "巨无霸套餐", 4, hour=8)]
        + [_order("2026-09-08", "中薯条", 1, hour=15)]
    )
    model = card.build(entries, today=TODAY, with_savings=False, demo=False, head="abcdef0123456789")
    svg = card_receipt.render_svg(model)
    _assert_self_contained(svg)
    texts = _texts(svg)
    rows = [t for t in texts if re.match(r"^\s*\d+  ", t)]
    clipped = card_receipt.item_rows(model)[0][1]
    assert clipped.endswith("…") and len(clipped) < len(long_name) and long_name not in "".join(texts)  # desc 里保留全名给读屏
    assert rows == [f"  5  {clipped}", "  4  巨无霸套餐", "  3  菠萝派", "  2  热拿铁", "  2  美式咖啡"]  # 次数多的在前，一样多按名字；第 6 个不上票
    assert "中薯条" not in svg
    assert {"git mcd · 吃麦小票", "COMMIT  0x400", "BRANCH  lunch", "DATE    2026-10-24", "HEAD    abcdef0", "TOTAL", "6 单", "*** HAPPY 0x400 DAY ***"} <= set(texts)
    assert "非麦当劳官方票据 · 由 git mcd 本机生成" in texts and "ANNUAL RECEIPT  2025-10-20 → 2026-10-24" in texts
    assert [t for t in texts if t in ("早餐", "午饭", "下午茶", "晚饭", "夜宵")] == ["早餐", "午饭", "下午茶"]  # 只列有数据的时段
    assert svg.count('class="ring"') == 1  # 10-24 红圈
    cell = (card_receipt.X1 - card_receipt.X0) / 53
    assert f'cx="{card_receipt.X0 + 52 * cell + cell / 2:.1f}"' in svg.split('class="ring"')[1].split("/>")[0]
    # 条形码由统计数据确定、水平居中
    bars = re.findall(r'<rect class="i" x="([0-9.]+)" y="[0-9.]+" width="([0-9.]+)" height="46"/>', svg)
    left, right = float(bars[0][0]), float(bars[-1][0]) + float(bars[-1][1])
    assert abs((left - 0) - (card_receipt.PAPER - right)) < 3
    assert card_receipt.barcode_bits(model) == card_receipt.barcode_bits(card.build(entries, today=TODAY, with_savings=False, demo=False))
    assert "HEAD    —" in card_receipt.render_svg(card.build(entries, today=TODAY, with_savings=False, demo=False))


@pytest.mark.parametrize("style", STYLES)
def test_money_only_with_savings(style: str) -> None:
    entries = [_order("2026-10-12", discount=500), _order("2026-10-13"), _order("2026-10-14", discount=None)]
    plain = RENDER[style](card.build(entries, today=TODAY, with_savings=False, demo=False))
    assert "¥" not in plain and "用券省下" not in plain
    rich = RENDER[style](card.build(entries, today=TODAY, with_savings=True, demo=False))
    assert "用券省下" in rich and "¥5.00" in rich
    if style != "grid":  # 只有 1 单有优惠明细：交代清楚这是下限（grid 的 SVG 沿用原样）
        assert "只算有优惠明细的 1 单，是下限" in rich
    full = card.build([_order("2026-10-12", discount=500)], today=TODAY, with_savings=True, demo=False)
    assert "是下限" not in RENDER[style](full)


@pytest.mark.parametrize("style", STYLES)
def test_card_styles_keep_private_things_off_the_card(runner: Runner, tmp_path: Path, style: str) -> None:
    out = tmp_path / f"{style}.svg"
    code, data = runner.json("card", "--style", style, "-o", str(out), mock=True)
    assert code == 0 and data["data"]["status"] == "written" and data["data"]["style"] == style
    assert runner.count("order-list") == 1 and len(runner.calls) == 1
    svg = out.read_text(encoding="utf-8")
    _assert_self_contained(svg)
    assert "数据：麦当劳最近 10 单 + 本机记录，最早到 2026-10-21" in svg
    blob = json.dumps({k: v for k, v in data["data"].items() if k != "path"}, ensure_ascii=False)
    for text in (svg, blob):
        assert not any(oid in text for oid in HISTORY_IDS) and not re.search(r"\d{12,}", text)  # 没有订单号
        assert "演示·" not in text and "示例市" not in text and "138****" not in text  # 没有门店、地址、手机号
        assert not re.search(r"\d{1,2}:\d{2}", text)  # 没有具体下单时刻
        assert "¥" not in text and "实付" not in text and "paid" not in text  # 没有金额
        assert not any(word in text for word in BANNED)

    code, text = runner.run("card", "--style", style, "-o", str(out), "--with-savings", mock=True)
    assert code == 0 and "用券省下" in out.read_text(encoding="utf-8")
    assert "是下限" in text and "order-list 不返回优惠明细" in text  # 终端 hint 交代口径（4 单都没有优惠明细）


def test_terminal_previews(runner: Runner, tmp_path: Path) -> None:
    code, out = runner.run("--demo", "card", "-o", str(tmp_path / "s.svg"))
    assert code == 0 and "少 ·░▒▓█ 多" in out and "终端画不出 3D" in out and "麦门天际线（--style skyline）" in out
    code, out = runner.run("--demo", "card", "--style", "receipt", "-o", str(tmp_path / "r.svg"))
    assert code == 0
    for text in ("git mcd · 吃麦小票", "COMMIT  0x400", "BRANCH  lunch", "QTY  ITEM", "TOTAL", "111 单", "*** HAPPY 0x400 DAY ***", "@ 是 10-24", "非麦当劳官方票据"):
        assert text in out
    assert not any(ch in out for ch in "•●◉")  # 点阵只用 ASCII，避免歧义宽度字符错位
    assert "少 ·░▒▓█ 多" not in out and "发朋友圈" in out and "GitHub Actions" in out
    assert not any(word in out for word in BANNED)
    code, out = runner.run("--demo", "card", "--style", "grid", "-o", str(tmp_path / "g.svg"))
    assert code == 0 and "终端画不出 3D" not in out and "少 ·░▒▓█ 多" in out


def test_style_defaults_and_bad_style(runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, data = runner.json("--demo", "card")
    assert code == 0 and data["data"]["style"] == "skyline" and data["data"]["path"] == "mcd-contributions.svg"
    code, data = runner.json("--demo", "card", "--style", "receipt")
    assert code == 0 and data["data"]["path"] == "mcd-receipt.svg" and (tmp_path / "mcd-receipt.svg").exists()
    code, _ = runner.run("--demo", "card", "--style", "3d")
    assert code == 129


@pytest.mark.parametrize("style,asset", [("skyline", "card-demo.svg"), ("receipt", "card-receipt-demo.svg"), ("grid", "card-grid-demo.svg")])
def test_demo_cards_match_committed_assets(runner: Runner, tmp_path: Path, style: str, asset: str) -> None:
    """README 里的三张卡片都是全新 demo 世界在 DEMO_NOW 生成的，逐字节一致（Python 3.9 上也一样）。"""
    out = tmp_path / "c.svg"
    code, _ = runner.run("--demo", "card", "--style", style, "-o", str(out))
    assert code == 0
    committed = (ROOT / "assets" / asset).read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == committed, f"assets/{asset} 过期了：重跑 .venv/bin/python scripts/make_screenshots.py"
