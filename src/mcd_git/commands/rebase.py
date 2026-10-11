"""git mcd rebase：自动凑套餐（squash）、挑最省的券（coupon），只改本地购物车。"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from rich.console import Console
from rich.text import Text

from .. import render as R
from ..cart import cart_fingerprint, summary
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..money import fmt, yuan_to_fen
from ..optimizer import plan
from ..state import next_line_id
from .common import line_json


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser(
        "rebase",
        parents=[common],
        help="自动凑套餐、挑最优券",
        description="在吃的东西不变的前提下找最省的写法：单品凑成套餐、换上本店最划算的一张券。每个方案都真实算价（最多 20 次）。",
    )
    p.add_argument("--budget", metavar="元", help="预算上限（元）；超了会从最后加的开始 drop")
    p.add_argument("--no-coupon", action="store_true", help="只凑套餐，不用券")
    p.set_defaults(func=run)


_OP_STYLE = {"pick": R.ADD, "squash": R.HUNK, "coupon": R.TAG, "drop": R.DEL}


def run(ctx: Context, args: Any) -> Result:
    remote = ctx.require_remote()
    lines = ctx.state.get("index") or []
    if not lines:
        raise GitMcdError("nothing to rebase：购物车是空的", hint="先 git mcd add <餐品>")
    budget = None
    if args.budget is not None:
        budget = yuan_to_fen(args.budget)
        if budget is None or budget <= 0:
            raise GitMcdError(f"--budget {args.budget} 不是有效金额")
    api = ctx.api()
    menu = api.menu(remote)
    coupons: List[Dict[str, Any]] = []
    coupon_error = None
    if not args.no_coupon:
        try:
            coupons = api.store_coupons(remote)
        except McdError as err:
            coupon_error = err.message

    result = plan(ctx, api, remote, copy.deepcopy(lines), menu, coupons, budget=budget, new_id=lambda code: next_line_id(ctx.state, f"rebase:{code}"))
    changed = result["changed"]
    if changed:
        ctx.state["orig_index"] = copy.deepcopy(lines)
        ctx.state["index"] = result["lines"]
    # 不论改没改都记下购物车指纹：购物车没再变，diff 就不再提示 rebase
    ctx.state["rebased"] = cart_fingerprint(ctx.state["index"])
    ctx.save()
    coupon_stat = result["coupons"]
    branch = ctx.branch

    data = {
        "changed": changed,
        "before": result["before"],
        "after": result["after"],
        "saved": result["saved"],
        "tried": result["tried"],
        "priceCalls": result["calls"],
        "capped": result["capped"],
        "todo": result["todo"],
        "index": [line_json(x) for x in ctx.state["index"]],
        "couponError": coupon_error,
        "overBudget": result["overBudget"],
        "coupons": coupon_stat,
    }

    def draw(console: Console) -> None:
        console.print(f"rebase: 试了 {result['tried']} 种写法（calculate-price × {result['calls']}）", style=R.DIM)
        if not changed:
            console.print(Text.assemble("Current branch ", (branch, R.BRANCH), " is up to date."))
            console.print(f"已经是最省的写法了：应付 {fmt(result['before'])}")
        else:
            into = None
            for op in result["todo"]:
                starts_group = op["op"] == "pick" and op.get("into")
                if into is not None and (starts_group or not op.get("into")):
                    console.print(Text(f"        → {into}", style=R.DIM))
                    into = None
                style = _OP_STYLE.get(op["op"], "")
                row = Text()
                row.append(R.pad(op["op"], 7), style=style)
                row.append(R.pad(op["id"][:7], 8), style=R.HASH)
                if op["op"] == "coupon":
                    row.append(f"{op['coupon']}", style=R.TAG)
                    row.append(f"  → {op['name']}", style=R.DIM)
                else:
                    row.append(op["name"])
                    if op.get("qty", 1) > 1:
                        row.append(f" x{op['qty']}")
                console.print(row)
                if op.get("into"):
                    into = op["into"]
            if into is not None:
                console.print(Text(f"        → {into}", style=R.DIM))
            console.print(Text.assemble("Successfully rebased and updated ", (f"refs/heads/{branch}", R.BRANCH), "."))
            console.print(Text.assemble("应付 ", fmt(result["before"]), " → ", (fmt(result["after"]), "bold green"), (f"（省 {fmt(result['saved'])}）", "bold green")))
            for line in ctx.state["index"]:
                console.print(Text.assemble("\t", (summary(line), R.ADD), f"  x{line['qty']}"))
        if coupon_stat["available"] and not coupon_stat["matched"]:
            names: List[str] = []
            for c in coupon_stat["unmatched"]:
                names += [n for n in c["products"] if n not in names]
            listed = f"（券里是：{'、'.join(names[:3])}{'…' if len(names) > 3 else ''}）" if names else ""
            console.print(f"本店 {coupon_stat['available']} 张券都对不上购物车里的餐品{listed}", style=R.DIM)
        if result["capped"]:
            console.print(Text.assemble(("warning: ", "yellow"), "到了 20 次算价上限，后面的方案没试，可能不是全局最优"))
        if result["overBudget"]:
            console.print(Text.assemble(("warning: ", "yellow"), f"能 drop 的都试了，还是超预算（{fmt(result['after'])} > {fmt(budget)}）"))
        if coupon_error:
            console.print(Text.assemble(("warning: ", "yellow"), f"查券失败，只试了凑套餐：{coupon_error}"))
        if changed:
            R.hints(console, ["不满意就 git mcd reset ORIG_HEAD 撤销；满意就 git mcd commit -m <说明>"])

    return Result(data, draw)
