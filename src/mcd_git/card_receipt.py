"""git mcd card --style receipt：年度吃麦小票。竖版，热敏纸底色、上下锯齿边、等宽字体，适合发朋友圈 / 小红书。

- 商品行是最常点 TOP 5 和次数，右边画频次条，过长的餐品名用省略号截断；中间一块 53 × 7 的点阵热力图，红圈标出 10-24；
  时段分布只列有数据的时段；TOTAL 只有单数，加了 --with-savings 才在上面多一行「用券省下 ¥X」。
- 条形码只是装饰：由统计数据的 sha1 确定性地生成，水平居中。
- 小票是一个「物件」：浅色 / 深色主题下纸都是白的，只有外框背景跟着 prefers-color-scheme 变。
- 终端里打印同样内容的等宽文字小票（render_terminal）。
"""
from __future__ import annotations

import hashlib
import html
from typing import Any, Dict, List, Optional, Tuple

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from . import render as R
from . import stats
from .card_skyline import savings_note
from .money import fmt

PAPER = 440
X0, X1 = 34, PAPER - 34
MARGIN = 20
NAME_CELLS = 22  # 商品名最多占 22 个等宽格（中文一个字两格），再长就截断加「…」
FREQ_BAR = 80
SLOT_BAR = 120

_MONO = 'ui-monospace,"SF Mono",Menlo,Consolas,"PingFang SC","Microsoft YaHei","Noto Sans Mono CJK SC",monospace'
_CSS = (
    f"\nsvg{{font-family:{_MONO}}}text{{white-space:pre}}\n"
    ".o{fill:#E9E4DA}.sh{fill:#000000;opacity:.18}.pp{fill:#FBF8F1}.i{fill:#2A2621}.u{fill:#6B645A}.z{fill:#D8D1C4}"
    ".r{stroke:#2A2621;stroke-width:1}.dr{stroke-dasharray:4 3}.ring{fill:none;stroke:#D9342B;stroke-width:1.4}\n"
    ".b{font-weight:700}.xb{font-weight:800}\n"
    "@media (prefers-color-scheme:dark){.o{fill:#1D1F26}.sh{opacity:.5}}\n"
)


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _f(v: float) -> str:
    return f"{v:.1f}"


def head_label(model: Dict[str, Any]) -> str:
    return R.short(model["head"]) if model.get("head") else "—"


def branch_label(model: Dict[str, Any]) -> str:
    return model["topSlot"]["slot"] if model["topSlot"] else "—"


def item_rows(model: Dict[str, Any]) -> List[Tuple[int, str]]:
    """TOP 5：（次数，截断后的名字），顺序同 stats.top_items（次数多的在前，一样多按名字排）。"""
    return [(row["count"], R.clip(row["name"], NAME_CELLS)) for row in model["topItems"][:5]]


def slot_rows(model: Dict[str, Any]) -> List[Tuple[str, int]]:
    return [(stats.SLOT_LABELS[slot], model["slots"][slot]) for slot in stats.SLOT_ORDER if model["slots"].get(slot)]


def footer_lines(model: Dict[str, Any]) -> List[str]:
    """「数据：…，最早到 …」一行，「git mcd card 生成」那半句小票上不重复（最后一行已经写了本机生成）。"""
    return [model["footer"].split(" · ")[0]]


def barcode_bits(model: Dict[str, Any]) -> str:
    """装饰条形码的位串：统计摘要的 sha1，同样的数据永远是同一根码。"""
    seed = "|".join(
        [model["window"]["start"], model["window"]["end"], str(model["total"])]
        + [f"{n}:{name}" for n, name in item_rows(model)]
        + [f"{k}:{v}" for k, v in sorted(model["slots"].items())]
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return bin(int(digest, 16))[2:].zfill(160)[:112]


def render_svg(model: Dict[str, Any]) -> str:
    out: List[str] = []
    y = 64

    def text(content: str, x: float = X0, size: float = 13, anchor: str = "start", cls: str = "i") -> None:
        extra = "" if anchor == "start" else f' text-anchor="{anchor}"'
        out.append(f'<text class="{cls}" x="{_f(x)}" y="{y}" font-size="{size}"{extra}>{_esc(content)}</text>')

    def rule(dashed: bool = True, gap: int = 24) -> None:
        nonlocal y
        out.append(f'<line class="r{" dr" if dashed else ""}" x1="{X0}" y1="{y}" x2="{X1}" y2="{y}"/>')
        y += gap

    mid = PAPER / 2
    text("git mcd · 吃麦小票", mid, 20, "middle", "i xb")
    y += 24
    text(f"ANNUAL RECEIPT  {model['window']['start']} → {model['window']['end']}", mid, 11, "middle", "u")
    y += 26
    rule()
    text("COMMIT  0x400")
    text(f"BRANCH  {branch_label(model)}", X1, anchor="end")
    y += 20
    text(f"DATE    {model['today']}")
    text(f"HEAD    {head_label(model)}", X1, anchor="end")
    y += 22
    rule()
    text("QTY  ITEM", cls="i b")
    text("FREQ", X1, anchor="end", cls="i b")
    y += 22
    rows = item_rows(model)
    peak = max([n for n, _ in rows] or [1])
    for n, name in rows:
        text(f"{n:>3}  {name}")
        bar = max(4, round(n * FREQ_BAR / peak))
        out.append(f'<rect class="i" x="{X1 - bar}" y="{y - 9}" width="{bar}" height="8"/>')
        y += 20
    if not rows:
        text("  —  （没有餐品明细）", cls="u")
        y += 20
    y += 2
    rule()

    # 点阵热力图：53 周 × 7 天，一个点一天；10-24 套一个红圈
    cell = (X1 - X0) / len(model["weeks"])
    for col, column in enumerate(model["weeks"]):
        for row, day in enumerate(column):
            if day is None:
                continue
            cx, cy = X0 + col * cell + cell / 2, y + row * cell + cell / 2
            if day["mark"]:
                out.append(f'<circle class="ring" cx="{_f(cx)}" cy="{_f(cy)}" r="3.6"/>')
            if day["count"] == 0:
                out.append(f'<circle class="z" cx="{_f(cx)}" cy="{_f(cy)}" r="0.9"/>')
            else:
                out.append(f'<circle class="i" cx="{_f(cx)}" cy="{_f(cy)}" r="{"2.2" if day["count"] == 1 else "3"}"/>')
    y += int(7 * cell) + 22
    text("每个点是一天 · 红圈是 10-24", mid, 10.5, "middle", "u")
    y += 22
    rule()

    slots = slot_rows(model)
    most = max([n for _, n in slots] or [1])
    for label, n in slots:
        text(label)
        bar = max(4, round(n * SLOT_BAR / most))
        out.append(f'<rect class="i" x="{X1 - 34 - bar}" y="{y - 11}" width="{bar}" height="13"/>')
        text(str(n), X1, anchor="end")
        y += 20
    y += 2
    rule(dashed=False, gap=24)
    if model["savings"] is not None:
        text("用券省下")
        text(fmt(model["savings"]), X1, anchor="end")
        y += 20
        note = savings_note(model)
        if note:
            text(f"（{note}）", size=10.5, cls="u")
            y += 18
        y += 4
    text("TOTAL", size=16, cls="i xb")
    text(f"{model['total']} 单", X1, 16, "end", "i xb")
    y += 34

    bits = barcode_bits(model)
    widths = [2.2 if bit == "1" else 1.0 for bit in bits]
    full = sum(widths) + 0.6 * len(widths)
    bx = (PAPER - full) / 2
    for i, w in enumerate(widths):
        if i % 2 == 0:
            out.append(f'<rect class="i" x="{_f(bx)}" y="{y - 10}" width="{_f(w)}" height="46"/>')
        bx += w + 0.6
    y += 58
    text("*** HAPPY 0x400 DAY ***", mid, 13, "middle", "i b")
    y += 20
    for part in footer_lines(model):
        text(part, mid, 10, "middle", "u")
        y += 15
    y += 2
    text("非麦当劳官方票据 · 由 git mcd 本机生成", mid, 10.5, "middle", "u")
    y += 34
    height = y

    teeth = PAPER // 10
    top = "".join(f"L{i * 10 + 5},{6 if i % 2 == 0 else 0} " for i in range(teeth)) + f"L{PAPER},0"
    bottom = "".join(f"L{PAPER - i * 10 - 5},{height - (6 if i % 2 == 0 else 0)} " for i in range(teeth)) + f"L0,{height}"
    paper = f"M0,0 {top} L{PAPER},{height} {bottom} Z"
    w, h = PAPER + 2 * MARGIN, height + 2 * MARGIN
    title = f"年度吃麦小票：过去一年 {model['total']} 单"
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="-{MARGIN} -{MARGIN} {w} {h}" role="img" aria-labelledby="t d">',
        f'<title id="t">{_esc(title)}</title>',
        f'<desc id="d">{_esc(model["desc"])}</desc>',
        "<!-- 0x400 = 1024 -->",
        f"<style>{_CSS}</style>",
        f'<rect class="o" x="-{MARGIN}" y="-{MARGIN}" width="{w}" height="{h}"/>',
        f'<path class="sh" transform="translate(3,5)" d="{paper}"/>',
        f'<path class="pp" d="{paper}"/>',
    ]
    return "\n".join(head + out + ["</svg>"]) + "\n"


# ---------------------------------------------------------------- 终端

WIDTH = 57  # 53 个点 + 两边各留两格


def _line(left: str, right: str = "") -> str:
    gap = WIDTH - 4 - cell_len(left) - cell_len(right)
    return "  " + left + " " * max(1, gap) + right + "  "


def _center(content: str) -> str:
    gap = WIDTH - cell_len(content)
    left = max(0, gap // 2)
    return " " * left + content + " " * max(0, gap - left)


def render_terminal(console: Console, model: Dict[str, Any]) -> None:
    color = console.color_system is not None
    dashed = "  " + "-" * (WIDTH - 4) + "  "
    paper = "on #fbf8f1" if color else ""
    ink = ("#2a2621 " + paper).strip()
    muted = ("#6b645a " + paper).strip()

    def show(content: str, style: str = "") -> None:
        console.print(Text(content, style=style or ink), no_wrap=True)

    zig = ("/\\" * (WIDTH // 2 + 1))[:WIDTH]
    show(zig, muted)
    show(_center("git mcd · 吃麦小票"), ("bold " + ink).strip())
    show(_center(f"ANNUAL RECEIPT  {model['window']['start']} → {model['window']['end']}"), muted)
    show(dashed)
    show(_line("COMMIT  0x400", f"BRANCH  {branch_label(model)}"))
    show(_line(f"DATE    {model['today']}", f"HEAD    {head_label(model)}"))
    show(dashed)
    show(_line("QTY  ITEM", "FREQ"), ("bold " + ink).strip())
    rows = item_rows(model)
    peak = max([n for n, _ in rows] or [1])
    for n, name in rows:
        show(_line(f"{n:>3}  {name}", "▮" * max(1, round(n * 10 / peak))))
    if not rows:
        show(_line("  —  （没有餐品明细）"), muted)
    show(dashed)
    for row in range(7):
        line = Text("  ", style=ink)
        for column in model["weeks"]:
            day = column[row]
            if day is None:
                line.append(" ", style=ink)
            elif day["mark"]:
                line.append("◉", style=("bold #d9342b " + paper).strip() if color else "bold")
            else:
                line.append("·•●"[min(day["count"], 2)], style=(muted if day["count"] == 0 else ink))
        line.append(" " * (WIDTH - 2 - len(model["weeks"])), style=ink)
        console.print(line, no_wrap=True)
    show(_center("每个点是一天 · ◉ 是 10-24"), muted)
    show(dashed)
    slots = slot_rows(model)
    most = max([n for _, n in slots] or [1])
    for label, n in slots:
        show(_line(R.pad(label, 8), "█" * max(1, round(n * 16 / most)) + f" {n:>3}"))
    show("  " + "=" * (WIDTH - 4) + "  ")
    if model["savings"] is not None:
        show(_line("用券省下", fmt(model["savings"])))
        note = savings_note(model)
        if note:
            show(_line(f"（{note}）"), muted)
    show(_line("TOTAL", f"{model['total']} 单"), ("bold " + ink).strip())
    show(_center(""))
    bars = "".join("┃" if bit == "1" else "│" for bit in barcode_bits(model)[:40])
    show(_center(bars))
    show(_center(""))
    show(_center("*** HAPPY 0x400 DAY ***"), ("bold " + ink).strip())
    for part in footer_lines(model):
        show(_center(R.clip(part, WIDTH - 2)), muted)
    show(_center("非麦当劳官方票据 · 由 git mcd 本机生成"), muted)
    show(("\\/" * (WIDTH // 2 + 1))[:WIDTH], muted)
