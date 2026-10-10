"""git mcd fetch：今日活动和可领券（只读）。领券是写操作（auto-bind-coupons），用 git mcd pull，会先确认。"""
from __future__ import annotations

from typing import Any, Dict, List

from rich.console import Console
from rich.text import Text

from .. import render as R
from ..context import Context, Result
from ..errors import McdError


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser("fetch", parents=[common], help="今日活动、可领券（只读）", description="看活动日历和可领的券。只读；领券用 git mcd pull（会先确认）")
    p.set_defaults(func=run)


def run(ctx: Context, args: Any) -> Result:
    api = ctx.api()
    events: List[Dict[str, Any]] = []
    errors: List[str] = []
    try:
        calendar = api.campaign_calendar()
        for day in calendar.get("dailyList") or []:
            for event in day.get("events") or []:
                article = event.get("articleDto") or {}
                if article.get("title"):
                    events.append({"date": day.get("date"), "today": bool(day.get("today")), "title": article["title"]})
    except McdError as err:
        errors.append(f"活动日历：{err.message}")
    claimable: List[Dict[str, Any]] = []
    try:
        claimable = [c for c in api.available_coupons() if c.get("status") in (None, "CAN_GET")]
    except McdError as err:
        errors.append(f"可领券：{err.message}")

    def draw(console: Console) -> None:
        console.print("From mcd.cn", style=R.DIM)
        for e in events[:8]:
            date = str(e.get("date") or "")[5:]
            console.print(Text.assemble(" * ", ("[活动]", R.TAG), f"     {date}  {e['title']}", ("  ← 今天" if e["today"] else "", R.ADD)))
        if claimable:
            names = "、".join(c["name"] for c in claimable[:3]) + ("…" if len(claimable) > 3 else "")
            console.print(Text.assemble(" * ", ("[可领券]", R.TAG), f"   {len(claimable)} 张：{names}"))
            console.print(Text.assemble("Your branch is behind ", ("'mcd/coupons'", R.REMOTE), f" by {len(claimable)}."))
            console.print("  (git mcd pull 领取：会先确认，一次领全部)", style=R.DIM)
        elif not errors:
            console.print("Already up to date.（没有可领的券）")
        for err in errors:
            console.print(Text.assemble(("warning: ", "yellow"), err))

    return Result({"events": events, "claimable": [c["name"] for c in claimable], "errors": errors}, draw)
