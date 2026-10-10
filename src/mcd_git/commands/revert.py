"""git mcd revert：取消订单（cancel-order）。和 push 一样过确认闸：先出摘要，用户同意后 --confirm <订单号>。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import gate
from .. import render as R
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..money import fmt
from ..state import fmt_time, head_commit, last_order, order_record, parse_time

# cancel-order 的 cancelReasonCode（schema 里是字符串）
CANCEL_REASONS = [
    ("1", "改主意了"),
    ("2", "重复下单"),
    ("3", "点错了/点多了/点少了"),
    ("4", "地址/电话填错了"),
    ("5", "送达时间选错了"),
    ("-1", "其它原因"),
]


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser("revert", parents=[common], help="取消订单（会先确认）", description="取消一笔订单（cancel-order）。必须选取消原因；非交互时先出摘要，用户同意后加 --confirm <订单号>。")
    p.add_argument("target", nargs="?", default="HEAD", metavar="<订单号|commit|HEAD>", help="默认 HEAD（最近一次 push 的订单）")
    p.add_argument("--reason", metavar="<编号或原因>", help="1 改主意了 · 2 重复下单 · 3 点错了 · 4 地址电话填错 · 5 送达时间选错 · -1 其它")
    p.add_argument("--dry-run", action="store_true", help="只看摘要，不取消")
    p.add_argument("--confirm", metavar="<订单号>", help="用户已同意：按预览里的订单号取消")
    p.set_defaults(func=run)


def parse_reason(text: Optional[str]) -> Optional[Tuple[str, str]]:
    if text is None:
        return None
    raw = text.strip()
    for code, label in CANCEL_REASONS:
        if raw == code:
            return code, label
    for code, label in CANCEL_REASONS:
        if raw and (raw in label or label.startswith(raw)):
            return code, label
    raise GitMcdError(f"--reason {text} 不在可选范围里", hint=[f"{c} {label}" for c, label in CANCEL_REASONS])


def _target(ctx: Context, target: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    state = ctx.state
    text = target.strip()
    if text.upper() in ("HEAD", "@"):
        head = head_commit(state)
        order_id = (head.get("pushed") or {}).get("orderId") if head else None
        if not order_id:
            record = last_order(state)
            order_id = record.get("orderId") if record else None
        if not order_id:
            raise GitMcdError("没有可以 revert 的订单（HEAD 还没 push）", fatal=True, hint="还没 push 的 commit 用 git mcd reset --soft HEAD~1 撤回")
        return order_id, order_record(state, order_id)
    if text.isdigit() and len(text) >= 12:
        return text, order_record(state, text)
    hits = [c for cid, c in state.get("commits", {}).items() if len(text) >= 4 and cid.startswith(text.lower())]
    if len(hits) == 1 and (hits[0].get("pushed") or {}).get("orderId"):
        order_id = hits[0]["pushed"]["orderId"]
        return order_id, order_record(state, order_id)
    raise GitMcdError(f"bad revision '{target}'", fatal=True, hint="写订单号，或 mcd-git push 过的 commit 哈希")


def run(ctx: Context, args: Any) -> Result:
    if args.dry_run and args.confirm:
        raise GitMcdError("--dry-run 和 --confirm 不能一起用")
    state = ctx.state
    order_id, record = _target(ctx, args.target)
    reason = parse_reason(args.reason)
    pending = state.get("pending") or {}

    if args.confirm:
        confirm = args.confirm.strip()
        if confirm != order_id and not (len(confirm) >= 6 and order_id.endswith(confirm)):
            raise GitMcdError(f"--confirm {confirm} 和要取消的订单 {R.mask_order_id(order_id)} 对不上")
        if pending.get("kind") != "revert" or pending.get("orderId") != order_id:
            raise GitMcdError("还没有给用户看过这次取消的确认摘要", hint=["先不带 --confirm 运行一次 git mcd revert（参数相同）", "用户明确同意后再加 --confirm <订单号>"])
        if reason is None or pending.get("reason") != reason[0]:
            raise GitMcdError("--reason 和预览时不一样", hint="参数要和预览时一模一样")
        previewed_at = parse_time(pending.get("time"))
        if previewed_at is None or ctx.now - previewed_at > gate.PENDING_TTL:
            state["pending"] = None
            ctx.save()
            raise GitMcdError("确认已过期（预览超过 10 分钟）", hint="重新运行 git mcd revert 预览")

    api = ctx.api()
    order = api.query_order(order_id)
    if order["state"] in ("completed", "cancelled"):
        raise GitMcdError(f"订单 {R.mask_order_id(order_id)} {order['statusLabel']}，不能 revert")

    if reason is None:
        if ctx.interactive and not args.dry_run:
            for code, label in CANCEL_REASONS:
                ctx.console.print(f"  {R.lpad(code, 2)}  {label}")
            reason = parse_reason(ctx.ask("取消原因（编号）：").strip() or None)
        if reason is None:
            raise GitMcdError("要选一个取消原因：--reason <编号>", hint=[f"{c} {label}" for c, label in CANCEL_REASONS])
    cancel_args = {"orderId": order_id, "cancelReasonCode": reason[0]}

    summary = {
        "orderId": order_id,
        "status": order["statusLabel"],
        "store": order.get("storeName"),
        "items": [f"{p['name']} x{p['qty']}" for p in order.get("products") or []],
        "realTotal": order.get("realTotal"),
        "reason": {"code": reason[0], "label": reason[1]},
    }

    def render_summary(console: Console) -> None:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=R.DIM)
        grid.add_column()
        grid.add_row("订单", R.mask_order_id(order_id))
        grid.add_row("状态", order["statusLabel"])
        grid.add_row("门店", order.get("storeName") or "")
        grid.add_row("商品", "、".join(summary["items"]) or "—")
        grid.add_row("实付", fmt(order.get("realTotal")))
        grid.add_row("原因", f"{reason[0]} {reason[1]}")
        console.print(Panel(grid, title=Text("即将 revert（取消订单）" + ("（demo）" if ctx.demo else "")), title_align="left", border_style="red", expand=False))

    if args.confirm:
        return _cancel(ctx, order_id, record, cancel_args, summary)

    state["pending"] = {"kind": "revert", "orderId": order_id, "reason": reason[0], "time": fmt_time(ctx.now)}
    ctx.save()
    reason_arg = f"--reason={reason[0]}" if reason[0].startswith("-") else f"--reason {reason[0]}"
    command = "git mcd " + ("--demo " if ctx.demo else "") + f"revert {order_id} {reason_arg} --confirm {order_id}" + (" --json" if ctx.json else "")
    if ctx.interactive and not args.dry_run:
        render_summary(ctx.console)
        answer = ctx.ask("确认取消这笔订单？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            state["pending"] = None
            ctx.save()
            return Result({"status": "aborted", "summary": summary}, lambda console: console.print("没有取消。"))
        return _cancel(ctx, order_id, record, cancel_args, summary)

    def draw(console: Console) -> None:
        render_summary(console)
        console.print(Text.assemble("用户确认无误后运行：", (command, "bold")))

    return Result({"status": "confirmation_required", "confirm": order_id, "summary": summary, "next": command}, draw)


def _cancel(ctx: Context, order_id: str, record: Optional[Dict[str, Any]], cancel_args: Dict[str, Any], summary: Dict[str, Any]) -> Result:
    approval = gate.issue("cancel-order", cancel_args)
    ctx.state["pending"] = None
    ctx.save()
    try:
        result = ctx.api().cancel_order(approval, cancel_args)
    except McdError as err:
        raise GitMcdError(f"revert 失败：{err.message}", remote=err.remote_lines(), hint="git mcd show 看看订单现在的状态") from None
    if result.get("cancelResult") is False:
        raise GitMcdError("revert 失败：服务端没有取消成功", hint="git mcd show 看看订单现在的状态")
    if record is not None:
        record["statusLabel"], record["state"] = "已取消", "cancelled"
    commit = next((c for c in ctx.state.get("commits", {}).values() if (c.get("pushed") or {}).get("orderId") == order_id), None)
    ctx.save()

    def draw(console: Console) -> None:
        if commit is not None:
            console.print(Text.assemble("[", (commit.get("branch") or ctx.branch, R.BRANCH), " ", (R.short(commit["id"]), R.HASH), f"] Revert \"{commit['message']}\""))
        console.print(f" 订单 {R.mask_order_id(order_id)} 已取消（原因：{summary['reason']['label']}）")
        R.hints(console, ["退款进度以麦当劳 App 里的订单为准"])

    return Result({"status": "reverted", "orderId": order_id, "reason": summary["reason"]}, draw)
