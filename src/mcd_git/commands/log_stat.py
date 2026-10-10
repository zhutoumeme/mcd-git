"""git mcd log --stat：吃麦统计（只读）。

仿 git log --stat：每单下面列出餐品行（「 巨无霸套餐 | 1 +」）和一行小结（几样、实付、省了多少），
最后仿 diff --stat 给一行总汇总，再按时段分布、最常点前 3 名（shortlog -sn 风格）。
数据来自本机账本：开始前调一次 order-list（最近 10 单）刷新，不轮询。已取消的单照常显示但不计入金额，
待支付的单单独标出、也不计入；口径和 card 一样（stats.eaten）。不显示热量。
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from .. import render as R
from .. import stats
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..money import fmt
from ..state import parse_time

NOTE = "数据来自本机账本 + 刚刚刷新的最近 10 单"
_SLOT_BAR = 20  # 时段分布最长那条画几个 +
_ITEM_BAR = 10  # 一行餐品最多画几个 +


def parse_day(text: Optional[str], flag: str) -> Optional[date]:
    if text is None:
        return None
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise GitMcdError(f"{flag} {text} 看不懂", hint="写成 2026-10-01") from None


def _day(entry: Dict[str, Any]) -> Optional[date]:
    when = parse_time(entry.get("time"))
    return when.date() if when else None


def select(entries: List[Dict[str, Any]], since: Optional[date], until: Optional[date], n: int) -> List[Dict[str, Any]]:
    """先按日期过滤（两头都含当天），再取最新的 n 单；entries 已经按时间倒序。"""
    out = []
    for entry in entries:
        if since is not None or until is not None:
            day = _day(entry)
            if day is None or (since is not None and day < since) or (until is not None and day > until):
                continue
        out.append(entry)
    return out[: max(1, n)]


def _short_status(entry: Dict[str, Any]) -> str:
    text = str(entry.get("status") or entry.get("state") or "")
    return text[2:] if text.startswith("订单") and len(text) > 2 else text


def entry_view(entry: Dict[str, Any]) -> Dict[str, Any]:
    """一单的展示 / JSON 形状：哈希优先用 push 它的本地 commit，否则用账本键（= git mcd log 显示的哈希）。"""
    digest = entry.get("commit") or entry["key"]
    slot = stats.slot_of(entry.get("time"))
    items = [{"name": i["name"], "qty": int(i.get("qty") or 1)} for i in entry.get("items") or []]
    return {
        "hash": digest,
        "short": R.short(digest),
        "time": entry.get("time"),
        "slot": slot,
        "slotLabel": stats.SLOT_LABELS.get(slot) if slot else None,
        "state": entry.get("state"),
        "status": entry.get("status"),
        "way": entry.get("way"),
        "items": items,
        "lines": len(items),
        "original": entry.get("original"),
        "discount": entry.get("discount"),
        "paid": entry.get("paid"),
        "counted": stats.eaten(entry),
    }


def summarize(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    cash = stats.money(entries)
    return {
        "orders": len(entries),
        "cancelled": sum(1 for e in entries if e.get("state") == "cancelled"),
        "pending": sum(1 for e in entries if e.get("state") == "pending"),
        "lines": sum(len(e.get("items") or []) for e in entries),
        "paid": cash["paid"],
        "saved": cash["saved"],
        "savedUnknown": cash["savedUnknown"],
        "slots": stats.slot_counts(entries),
        "top": stats.top_items(entries, 3),
    }


def _summary_line(s: Dict[str, Any]) -> str:
    extra = []
    if s["cancelled"]:
        extra.append(f"{s['cancelled']} 单已取消")
    if s["pending"]:
        extra.append(f"{s['pending']} 单待支付")
    head = f" {s['orders']} 单" + (f"（其中 {'，'.join(extra)}）" if extra else "")
    return f"{head}，{s['lines']} 样，实付 {fmt(s['paid'])}，省 {fmt(s['saved'])}"


def _draw_entry(console: Console, v: Dict[str, Any]) -> None:
    console.print(Text.assemble(("commit ", R.HASH), (v["hash"], R.HASH)))
    console.print(f"Date:   {str(v.get('time') or '')[:16]}")
    status = Text("Status: ")
    status.append(_short_status(v), style={"cancelled": R.DEL, "pending": "bold yellow"}.get(str(v.get("state")), ""))
    if v.get("way"):
        status.append(f" · {v['way']}")
    console.print(status)
    console.print()
    console.print(f"    {v.get('slotLabel') or ''}")
    console.print()
    items = v["items"]
    name_w = max((cell_len(i["name"]) for i in items), default=0)
    qty_w = max((len(str(i["qty"])) for i in items), default=1)
    for item in items:
        line = Text(f" {R.pad(item['name'], name_w)} | {str(item['qty']).rjust(qty_w)} ")
        line.append("+" * min(item["qty"], _ITEM_BAR), style=R.ADD)
        console.print(line, no_wrap=True, overflow="ellipsis")
    lines = f" {v['lines']} 样，"
    if v.get("state") == "cancelled":
        console.print(Text(lines + "已取消，不计入金额", style=R.DEL))
    elif v.get("state") == "pending":
        paid = f" {fmt(v['paid'])}" if v.get("paid") is not None else ""
        console.print(Text(lines + f"待支付{paid}，不计入金额", style="bold yellow"))
    else:
        saved = f"（省 {fmt(v['discount'])}）" if v.get("discount") else ""
        console.print(f"{lines}实付 {fmt(v.get('paid'))}{saved}")


def _draw_summary(console: Console, s: Dict[str, Any], views: List[Dict[str, Any]]) -> None:
    console.print(Text(_summary_line(s), style="bold"))
    if s["savedUnknown"]:
        sample = next((v["short"] for v in views if v["counted"] and v.get("discount") is None), "<哈希>")
        R.hints(console, [f"其中 {s['savedUnknown']} 单没有优惠明细（order-list 不返回原价），省下的钱没算进去；git mcd show {sample} 之后会补上"])
    console.print()
    console.print("按时段：")
    label_w = max(cell_len(label) for label in stats.SLOT_LABELS.values())
    top = max(s["slots"].values()) or 1
    count_w = max(len(str(n)) for n in s["slots"].values())
    for slot in stats.SLOT_ORDER:
        n = s["slots"][slot]
        line = Text(f"  {R.pad(stats.SLOT_LABELS[slot], label_w)} | {str(n).rjust(count_w)}")
        if n:
            line.append(" ")
            line.append("+" * math.ceil(n * _SLOT_BAR / top), style=R.ADD)
        console.print(line)
    console.print()
    console.print("最常点：")
    if not s["top"]:
        console.print("  （还没有计入的单）", style=R.DIM)
    for row in s["top"]:
        console.print(f"  {row['count']:6d}  {row['name']}")


def run(ctx: Context, args: Any) -> Result:
    since = parse_day(args.since, "--since")
    until = parse_day(args.until, "--until")
    warning: Optional[str] = None
    try:
        ctx.order_list()  # 刷一次本机账本；只调这一次，不轮询
    except McdError as err:
        warning = f"order-list 查询失败，只用本机记录：{err.message}"
    every = ctx.ledger.entries()  # 账本坏了在这里报 fatal
    chosen = select(every, since, until, args.max_count)
    views = [entry_view(e) for e in chosen]
    summary = summarize(chosen)
    data = {
        "entries": views,
        "summary": summary,
        "range": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "maxCount": max(1, args.max_count),
        },
        "note": NOTE,
        "warning": warning,
    }

    def draw(console: Console) -> None:
        if warning:
            console.print(Text.assemble(("warning: ", "yellow"), warning))
        if not views:
            if every:
                console.print("这段时间里没有吃麦记录")
                R.hints(console, ["去掉 --since / --until 看看全部"])
            else:
                console.print("还没有吃麦记录：最近 10 单里没有，本机也没记过")
                R.hints(console, ["先试试 git mcd --demo log --stat 看看效果"])
            return
        for i, v in enumerate(views):
            if i:
                console.print()
            _draw_entry(console, v)
        console.print()
        _draw_summary(console, summary, views)

    return Result(data, draw)
