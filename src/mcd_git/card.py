"""git mcd card 的模型和渲染：GitHub 风格的吃麦贡献图（7 行 × 最多 53 周），全部纯函数，不碰 IO。

- 周一在第一行；最后一列是今天所在的周，今天之后的格子不画；颜色深浅按当天吃了几单（取消的、没付款的不算）。
- 窗口：grid 固定最近 53 周；skyline / receipt 用 build(adaptive=True)，从最早一单所在的周画到今天所在的周，
  最少 12 周、最多 53 周（麦当劳接口只给最近 10 单，新用户的卡片不会大半是空格子）。10-24 只在窗口里才标出来。
- SVG 自包含：不引用外部字体、图片、脚本，字体用系统字体栈，浅色 / 深色跟随 prefers-color-scheme；
  同样的数据输出逐字节相同（不含生成时间、路径、版本号）。
- 卡片上只有日期级的格子、总单数、最常点、最常时段；没有门店、地址、订单号、手机号、具体时刻和总消费，
  只有显式要了 --with-savings 才加一项「用券省下 ¥X」。
"""
from __future__ import annotations

import html
from collections import Counter
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from . import render as R
from . import stats
from .money import fmt
from .state import parse_time

WEEKS = 53
MIN_WEEKS = 12  # 自适应窗口最少 12 周：数据再少也是一张像样的卡片
MIN_LABEL_GAP = 3  # 月份标签至少隔 3 列，不会叠在一起
WEEKDAY_LABELS = {0: "一", 2: "三", 4: "五"}  # 同 GitHub 只标 Mon / Wed / Fri


def level(count: int) -> int:
    """固定阈值：0 / 1 / 2 / 3 / 4 单及以上。每周一两单的量，按分位数分档会让 1 单就是最深色。"""
    return min(max(int(count), 0), 4)


def window_start(today: date, weeks: int = WEEKS) -> date:
    return today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)


def window_weeks(earliest: Optional[date], today: date) -> int:
    """自适应窗口的周数：最早一单所在的周到今天所在的周（含两头），夹到 [MIN_WEEKS, WEEKS]；没有数据就是 MIN_WEEKS。"""
    if earliest is None:
        return MIN_WEEKS
    this_monday = today - timedelta(days=today.weekday())
    earliest_monday = earliest - timedelta(days=earliest.weekday())
    n = (this_monday - earliest_monday).days // 7 + 1
    return min(max(n, MIN_WEEKS), WEEKS)


def mark_date(today: date, start: date) -> Optional[date]:
    """窗口里最近的那个 10-24（程序员节）。"""
    candidate = date(today.year, 10, 24)
    if candidate > today:
        candidate = date(today.year - 1, 10, 24)
    return candidate if candidate >= start else None


def month_labels(start: date, weeks: int = WEEKS) -> List[Tuple[int, str]]:
    """某列的周一和前一列的周一不在同一个月，就在这一列标月份；离上一个标签不足 MIN_LABEL_GAP 列就跳过。
    第一列那个月只露出一两周时，标签让给下一个月（GitHub 也是这样）。SVG 和终端用同一份。"""
    marks = [(0, start.month)]
    for col in range(1, weeks):
        monday = start + timedelta(weeks=col)
        if monday.month != (monday - timedelta(weeks=1)).month:
            marks.append((col, monday.month))
    kept: List[Tuple[int, int]] = []
    for col, month in marks:
        if kept and col - kept[-1][0] < MIN_LABEL_GAP:
            if kept[-1][0] == 0:
                kept[-1] = (col, month)
            continue
        kept.append((col, month))
    return [(col, f"{month}月") for col, month in kept]


def build(
    entries: List[Dict[str, Any]],
    *,
    today: date,
    with_savings: bool,
    demo: bool,
    head: Optional[str] = None,
    adaptive: bool = False,
) -> Dict[str, Any]:
    """head：当前 HEAD 的短哈希（小票上印「HEAD」用），没有就是 None。
    adaptive：True 时从最早一单所在的周画起（window_weeks，最少 12 周、最多 53 周）；False 时固定 53 周（grid）。"""
    eaten: List[Tuple[date, Dict[str, Any]]] = []
    for entry in entries:
        when = parse_time(entry.get("time"))
        if when is not None and stats.eaten(entry):
            eaten.append((when.date(), entry))
    # 最早一单不限在 53 周里：一年多以前就吃过的人直接是满 53 周（window_weeks 会夹到 53）
    first = min((day for day, _ in eaten if day <= today), default=None)
    n_weeks = window_weeks(first, today) if adaptive else WEEKS
    start = window_start(today, n_weeks)
    counted = [(day, entry) for day, entry in eaten if start <= day <= today]
    span = {"weeks": n_weeks, "label": "过去一年" if n_weeks == WEEKS else f"最近 {n_weeks} 周"}
    per_day = Counter(day.isoformat() for day, _ in counted)
    rows = [entry for _, entry in counted]
    mark = mark_date(today, start)
    weeks: List[List[Optional[Dict[str, Any]]]] = []
    for col in range(n_weeks):
        column: List[Optional[Dict[str, Any]]] = []
        for row in range(7):
            day = start + timedelta(days=col * 7 + row)
            if day > today:
                column.append(None)
                continue
            n = per_day.get(day.isoformat(), 0)
            column.append({"date": day.isoformat(), "count": n, "level": level(n), "mark": day == mark})
        weeks.append(column)

    total = len(rows)
    top = stats.top_items(rows, 5)
    slot = stats.top_slot(rows)
    slot_count = stats.slot_counts(rows)[slot] if slot else 0
    money = stats.money(rows)
    earliest = min((day for day, _ in counted), default=None)
    is1024 = (today.month, today.day) == (10, 24)
    top_item = top[0] if top else None
    top_slot = {"slot": slot, "label": stats.SLOT_LABELS[slot], "count": slot_count} if slot else None

    tiles = [("总单数", str(total)), ("最常点", top_item["name"] if top_item else "—"), ("最常时段", top_slot["label"] if top_slot else "—")]
    if with_savings:
        tiles.append(("用券省下", fmt(money["saved"])))
    source = "演示合成数据" if demo else "麦当劳最近 10 单 + 本机记录"
    desc = f"吃麦贡献图：{start.isoformat()} 至 {today.isoformat()}，共 {total} 次"
    if top_item and top_slot:
        desc += f"；最常点{top_item['name']}，最常在{top_slot['label']}时段"
    if with_savings:
        desc += f"；用券省下 {fmt(money['saved'])}"
    return {
        "weeks": weeks,
        "months": month_labels(start, n_weeks),
        "mark_date": mark.isoformat() if mark else None,
        "total": total,
        "activeDays": len(per_day),
        "topItem": top_item,
        "topSlot": top_slot,
        "topItems": top,
        "slots": stats.slot_counts(rows),
        "head": head,
        "today": today.isoformat(),
        "savings": money["saved"] if with_savings else None,
        "savingsKnownOrders": money["savedKnown"] if with_savings else None,
        "earliest": earliest.isoformat() if earliest else None,
        "is1024": is1024,
        "window": {"start": start.isoformat(), "end": today.isoformat(), "weeks": n_weeks},
        "span": span,
        "days": {day: per_day[day] for day in sorted(per_day)},
        "demo": demo,
        "title": f"{span['label']}吃麦 {total} 次",
        "subtitle": "Happy 0x400 Day · 1024 程序员节快乐" if is1024 else f"最近 {n_weeks} 周 · git mcd card",
        "desc": desc + "。",
        "tiles": tiles,
        "footer": f"数据：{source}，最早到 {earliest.isoformat() if earliest else '—'} · git mcd card 生成",
    }


# ---------------------------------------------------------------- SVG

WIDTH, HEIGHT = 750, 288
GRID_X, GRID_Y, CELL, STEP = 44, 88, 10, 13
LEFT = 24

_CSS = """
svg{font-family:-apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
.bg{fill:#fffaf2;stroke:#f0e2cc;stroke-width:2}.bd{stroke:#f0e2cc;stroke-width:1}
.t1{fill:#3b2a1a}.t2{fill:#8a7560}
.h{font-size:18px;font-weight:700}.s{font-size:12px}.m{font-size:10px}.k{font-size:11px}.v{font-size:15px;font-weight:700}
.l0{fill:#f3ebdf}.l1{fill:#ffd9a0}.l2{fill:#ffb35c}.l3{fill:#f08a24}.l4{fill:#c45a10}
.x{stroke:#6b2f00;stroke-width:1.5}
@media (prefers-color-scheme:dark){
.bg{fill:#1f1a16;stroke:#3a2f26}.bd{stroke:#3a2f26}.t1{fill:#f5e9dc}.t2{fill:#b39f8a}
.l0{fill:#2e2722}.l1{fill:#5a3a15}.l2{fill:#8a5518}.l3{fill:#c8761c}.l4{fill:#f2a33a}.x{stroke:#ffe2b0}
}
"""


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _text(x: int, y: int, cls: str, content: str) -> str:
    return f'<text class="{cls}" x="{x}" y="{y}">{_esc(content)}</text>'


def _cell(x: int, y: int, cls: str) -> str:
    return f'<rect class="{cls}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2"/>'


def render_svg(model: Dict[str, Any]) -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="t d">',
        f'<title id="t">{_esc(model["title"])}</title>',
        f'<desc id="d">{_esc(model["desc"])}</desc>',
        "<!-- 0x400 = 1024 -->",
        f"<style>{_CSS}</style>",
        f'<rect class="bg" x="1" y="1" width="{WIDTH - 2}" height="{HEIGHT - 2}" rx="12"/>',
        _text(LEFT, 36, "t1 h", model["title"]),
        _text(LEFT, 56, "t2 s", model["subtitle"]),
    ]
    out += [_text(GRID_X + col * STEP, 80, "t2 m", label) for col, label in model["months"]]
    out += [_text(LEFT, GRID_Y + row * STEP + 9, "t2 m", label) for row, label in sorted(WEEKDAY_LABELS.items())]
    for col, column in enumerate(model["weeks"]):
        for row, cell in enumerate(column):
            if cell is not None:
                out.append(_cell(GRID_X + col * STEP, GRID_Y + row * STEP, f"l{cell['level']}" + (" x" if cell["mark"] else "")))
    legend_y = GRID_Y + 7 * STEP + 15
    out.append(_text(GRID_X, legend_y + 9, "t2 m", "少"))
    out += [_cell(GRID_X + 14 + i * STEP, legend_y, f"l{i}") for i in range(5)]
    out.append(_text(GRID_X + 80, legend_y + 9, "t2 m", "多"))
    if model["mark_date"]:
        out.append(_cell(GRID_X + 106, legend_y, "l0 x"))
        out.append(_text(GRID_X + 120, legend_y + 9, "t2 m", "10-24 程序员节"))
    out.append(f'<line class="bd" x1="{LEFT}" y1="214" x2="{WIDTH - LEFT}" y2="214"/>')
    tiles = model["tiles"]
    width = (WIDTH - 2 * LEFT) // len(tiles)
    for i, (label, value) in enumerate(tiles):
        x = LEFT + i * width
        out.append(_text(x, 232, "t2 k", label))
        out.append(_text(x, 252, "t1 v", R.clip(value, max(8, (width - 12) * 2 // 15))))
    out.append(_text(LEFT, 274, "t2 m", model["footer"]))
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 终端

# 终端背景深浅未知：0 单是一个 dim 的「·」（深浅背景上都不抢眼），1～4 单是由暗到亮的暖色小方块（按深色终端调的）。
# 用「▪」不用「■」：一格一个字符，■ 在不少字体里撑满整格，连成一条看不出格子
_TERM_COLORS = ("dim", "#8a5518", "#b8691c", "#e0861f", "#ffb340")
_PLAIN = "·░▒▓█"  # 没有颜色时（管道、NO_COLOR）靠字符疏密分档


def _glyph(lvl: int, mark: bool, color: bool) -> Tuple[str, str]:
    if mark:
        return "◆", ("bold " + _TERM_COLORS[lvl]) if color and lvl else "bold"
    if not color:
        return _PLAIN[lvl], ""
    return ("▪" if lvl else "·"), _TERM_COLORS[lvl]


def render_terminal(console: Console, model: Dict[str, Any]) -> None:
    color = console.color_system is not None
    console.print(Text(model["title"], style="bold"))
    console.print(Text(model["subtitle"], style=R.TAG if model["is1024"] else R.DIM))
    console.print()
    # 终端里一周只有一格宽，「11月12月」会挤成一团：这一行只写数字，行首标一个「月」
    months = Text(R.pad("月", 4), style=R.DIM)
    pos = 0
    for col, label in model["months"]:
        number = label.rstrip("月")
        if col < pos:
            continue
        months.append(" " * (col - pos))
        months.append(number, style=R.DIM)
        pos = col + cell_len(number)
    console.print(months, no_wrap=True)
    for row in range(7):
        line = Text(R.pad(WEEKDAY_LABELS.get(row, ""), 4), style=R.DIM)
        for column in model["weeks"]:
            cell = column[row]
            if cell is not None:
                line.append(*_glyph(cell["level"], cell["mark"], color))
        console.print(line, no_wrap=True)
    legend = Text("    少 ", style=R.DIM)
    for lvl in range(5):
        legend.append(*_glyph(lvl, False, color))
    legend.append(" 多", style=R.DIM)
    if model["mark_date"]:
        legend.append("   ")
        legend.append("◆", style="bold")
        legend.append(" 10-24 程序员节", style=R.DIM)
    console.print(legend, no_wrap=True)
    console.print()
    tiles = Text()
    for i, (label, value) in enumerate(model["tiles"]):
        if i:
            tiles.append(" · ", style=R.DIM)
        tiles.append(label + " ", style=R.DIM)
        tiles.append(value, style="bold")
    console.print(tiles)
    console.print(Text(model["footer"], style=R.DIM))
