"""git mcd status：当前分支（按时段）、远端门店、购物车、ahead/behind、可用券、待支付订单。"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional

from rich.console import Console
from rich.text import Text

from .. import render as R
from ..cart import summary
from ..context import Context, Result, remote_brief
from ..errors import McdError
from ..money import fmt
from ..state import head_commit, last_order, parse_time
from .common import index_total_hint, line_json, remote_desc, remote_url, scope_gap


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser("status", parents=[common], help="看购物车、可用券、待支付订单", description="看当前门店、购物车、可用券和待支付订单。")
    p.set_defaults(func=run)


def pending_payment(ctx: Context) -> Optional[Dict[str, Any]]:
    """本地记录里最近一单还在支付窗口内、且上次看到的状态是待支付 → 提醒（不发请求）。"""
    order = last_order(ctx.state)
    if not order or order.get("state") != "pending":
        return None
    deadline = parse_time(order.get("expirePayTime"))
    if deadline is None or deadline <= ctx.now:
        return None
    return {"orderId": order["orderId"], "expirePayTime": order.get("expirePayTime"), "payH5Url": order.get("payH5Url")}


def run(ctx: Context, args: Any) -> Result:
    remote = ctx.require_remote()
    state = ctx.state
    lines = state.get("index") or []
    head = head_commit(state)
    ahead = 1 if head and not head.get("pushed") else 0

    coupons: Dict[str, Any] = {}
    try:
        rows = ctx.api().store_coupons(remote)
        today = ctx.now.strftime("%Y-%m-%d")
        tomorrow = (ctx.now + timedelta(days=1)).strftime("%Y-%m-%d")
        expiring = [
            {"title": c["title"], "end": c["end"]}
            for c in rows
            if c.get("end") and c["end"][:10] in (today, tomorrow)
        ]
        coupons = {"available": len(rows), "expiring": expiring}
    except McdError as err:
        coupons = {"error": err.message, "code": err.code}

    pay = pending_payment(ctx)
    if pay:
        # HEAD 已经换成新的、还没 push 的 commit 时，git mcd show 看到的是快照，提示里要带上订单号
        head_order = (head.get("pushed") or {}).get("orderId") if head else None
        pay["show"] = "git mcd show" if head_order == pay["orderId"] else f"git mcd show {pay['orderId']}"
    data = {
        "branch": ctx.branch,
        "remote": remote_brief(remote),
        "index": [line_json(line) for line in lines],
        "indexTotalHint": index_total_hint(lines),
        "head": {"commit": head["id"], "message": head["message"], "pushed": bool(head.get("pushed"))} if head else None,
        "ahead": ahead,
        "coupons": coupons,
        "pendingPayment": pay,
    }

    def draw(console: Console) -> None:
        branch = ctx.branch
        console.print(Text.assemble("On branch ", (branch, R.BRANCH)))
        if ctx.demo:
            console.print("（demo 模式：合成数据，不会真的下单）", style=R.DIM)
        console.print(Text.assemble("Your remote ", ("'origin'", R.BRANCH), " is ", remote_url(remote), "  ", (f"({remote_desc(remote)})", R.DIM)))
        if head is None:
            console.print()
            console.print("No commits yet")
        elif ahead:
            console.print(Text.assemble("Your branch is ahead of ", (f"'origin/{head.get('branch') or branch}'", R.REMOTE), " by 1 commit."))
            # 购物车里有东西时直接 push 会报 uncommitted_changes：先 amend 并进这一单，或者 reset 放弃；
            # HEAD 是另一家店 / 另一种取餐方式的单时 amend 会被拒绝，改给 reset --soft HEAD~1 撤回 HEAD
            gap = scope_gap(head["remote"], remote) if lines else None
            if not lines:
                console.print('  (use "git mcd push" to place the order)', style=R.DIM)
            elif gap is None:
                console.print('  (use "git mcd commit --amend" to add the cart to this order)', style=R.DIM)
                console.print('  (use "git mcd reset" to drop the cart, then "git mcd push" to place the order)', style=R.DIM)
            else:
                console.print(f"  (HEAD 是{gap}，购物车没法并进去)", style=R.DIM)
                console.print('  (use "git mcd reset" to drop the cart, then "git mcd push" to place the order)', style=R.DIM)
                console.print('  (use "git mcd reset --soft HEAD~1" to withdraw HEAD)', style=R.DIM)
        else:
            console.print(Text.assemble("Your branch is up to date with ", (f"'origin/{head.get('branch') or branch}'", R.REMOTE), "."))
        if pay:
            deadline = (pay.get("expirePayTime") or "")[11:16]
            console.print(Text.assemble(("订单 " + R.mask_order_id(pay["orderId"]) + f" 待支付，请在 {deadline} 前付款", "bold yellow")))
            console.print(f'  (付完运行 "{pay["show"]}" 查看取餐码)', style=R.DIM)
        console.print()
        if lines:
            console.print("Changes to be committed:")
            console.print('  (use "git mcd restore --staged <餐品>..." to unstage)', style=R.DIM)
            width = max(R.cell_len(summary(line)) for line in lines) if lines else 10
            width = min(max(width, 12), 44)
            for line in lines:
                label = R.pad(R.clip(summary(line), 44), width)
                amount = fmt(int(line.get("unit") or 0) * int(line["qty"]))
                row = Text("\t")
                row.append(f"new meal:   {label}  x{line['qty']}  {R.lpad(amount, 8)}", style=R.ADD)
                if line.get("coupon"):
                    row.append(f"  券：{line['coupon'].get('title')}", style=R.TAG)
                console.print(row)
            console.print()
        elif head is None or head.get("pushed"):
            console.print('nothing to commit (use "git mcd add <餐品>" to stage)')
            console.print()
        else:
            console.print("nothing to commit, cart clean")
            console.print()
        if "error" in coupons:
            console.print(Text.assemble(("warning: ", "yellow"), f"查券失败：{coupons['error']}"))
        elif coupons.get("available"):
            console.print(Text.assemble("Coupons: 本店可用 ", (str(coupons["available"]), R.TAG), " 张", ("（git mcd rebase 会自动挑最省的）" if lines else "")))
            for c in coupons.get("expiring") or []:
                when = "今天" if c["end"][:10] == ctx.now.strftime("%Y-%m-%d") else "明天"
                console.print(Text.assemble("\t", ("expiring:   ", R.TAG), f"{c['title']}  {when} {c['end'][11:16]} 到期"))
        else:
            console.print("Coupons: 本店暂无可用券", style=R.DIM)
        if not lines and (head is None or head.get("pushed")):
            R.hints(console, ['试试 git mcd add 巨无霸 中薯条 中可乐 --no-pickle' if ctx.demo else "git mcd ls-remote 看菜单，git mcd add <餐品> 放进购物车"])

    return Result(data, draw)
