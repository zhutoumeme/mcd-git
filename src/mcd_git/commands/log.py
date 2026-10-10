"""git mcd log / show：吃麦提交历史和订单详情（只读，不轮询）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .. import history
from .. import render as R
from ..cart import summary
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..money import fmt
from ..state import head_commit, last_order, order_record

_LANE_STYLES = ["", "red", "green", "yellow", "blue", "magenta"]
_STATE_STYLES = {"pending": "bold yellow", "preparing": "cyan", "delivering": "cyan", "completed": "green", "cancelled": "red", "local": "yellow"}


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser("log", parents=[common], help="吃麦提交历史（--graph 按时段分支画图）", description="本地 commit + 最近 10 单（order-list 没有分页）合成的提交历史。")
    p.add_argument("--graph", action="store_true", help="按时段（breakfast / lunch / tea / dinner）画分支图")
    p.add_argument("--oneline", action="store_true", help="一行一单")
    p.add_argument("-n", "--max-count", type=int, default=10, metavar="N", help="最多显示几条（默认 10）")
    p.set_defaults(func=run_log)

    p = sub.add_parser("show", parents=[common], help="订单详情、取餐码（默认 HEAD）", description="看一单的详情：订单状态、取餐码、取餐柜、外送进度。只在你运行时查一次，不轮询。")
    p.add_argument("target", nargs="?", default="HEAD", metavar="<commit|订单号>", help="默认 HEAD（最近一单）")
    p.set_defaults(func=run_show)


def _short_status(label: Optional[str]) -> str:
    text = str(label or "")
    return text[2:] if text.startswith("订单") and len(text) > 2 else text


def _mmdd(time: Optional[str]) -> str:
    text = str(time or "")
    return f"{text[5:10]} {text[11:16]}" if len(text) >= 16 else text


def graph(rows: List[Dict[str, Any]]) -> List[Tuple[Text, Optional[Text]]]:
    """每行返回 (图前缀, 这一行之后要不要画 |/ 合并线)。主线是 HEAD 所在时段，其它时段开侧线。"""
    if not rows:
        return []
    main = next((r["branch"] for r in rows if r.get("head")), rows[0]["branch"])
    last = {}
    for i, r in enumerate(rows):
        last[r["branch"]] = i
    columns: List[Optional[str]] = [main]
    out: List[Tuple[Text, Optional[Text]]] = []
    for i, row in enumerate(rows):
        branch = row["branch"]
        if branch == main:
            col = 0
        elif branch in columns:
            col = columns.index(branch)
        else:
            free = next((j for j in range(1, len(columns)) if columns[j] is None), None)
            if free is None:
                columns.append(branch)
                col = len(columns) - 1
            else:
                columns[free] = branch
                col = free
        prefix = Text()
        for j, owner in enumerate(columns):
            style = _LANE_STYLES[j % len(_LANE_STYLES)]
            if j == col:
                prefix.append("* ")
            elif owner is not None:
                prefix.append("| ", style=style)
            else:
                prefix.append("  ")
        connector = None
        if col != 0 and last[branch] == i:
            columns[col] = None
            if col == 1 and columns[0] is not None and last.get(main, -1) > i:
                connector = Text.assemble(("|", ""), ("/", _LANE_STYLES[1]))
        if branch == main and last[main] == i:
            columns[0] = None
        while len(columns) > 1 and columns[-1] is None:
            columns.pop()
        out.append((prefix, connector))
    return out


def _entry_line(e: Dict[str, Any]) -> Text:
    text = Text()
    text.append(R.short(e["hash"]), style=R.HASH)
    if e.get("decorations"):
        text.append(" ")
        text.append_text(R.decorations(e["decorations"]))
    text.append(f" {_mmdd(e.get('time'))}", style=R.DIM)
    text.append(f" {e.get('message') or ''}")
    text.append(f" — {e.get('brief')}", style=R.DIM)
    text.append(f" {fmt(e.get('total'))}" if e.get("total") is not None else "")
    text.append(f" {_short_status(e.get('statusLabel'))}", style=_STATE_STYLES.get(str(e.get("state")), R.DIM))
    return text


def run_log(ctx: Context, args: Any) -> Result:
    warning = None
    try:
        remote_orders: Optional[List[Dict[str, Any]]] = ctx.api().order_list()
    except McdError as err:
        remote_orders, warning = None, f"order-list 查询失败，只显示本地记录：{err.message}"
    rows = history.build(ctx.state, remote_orders)[: max(1, args.max_count)]
    data = {
        "entries": [
            {
                "hash": e["hash"],
                "short": R.short(e["hash"]),
                "time": e.get("time"),
                "branch": e.get("branch"),
                "message": e.get("message"),
                "items": e.get("items"),
                "total": e.get("total"),
                "orderId": e.get("orderId"),
                "status": e.get("statusLabel"),
                "state": e.get("state"),
                "store": e.get("store"),
                "decorations": e.get("decorations"),
                "local": e.get("local"),
            }
            for e in rows
        ],
        "note": "order-list 没有分页，只能看到最近 10 单",
        "warning": warning,
    }

    def draw(console: Console) -> None:
        if warning:
            console.print(Text.assemble(("warning: ", "yellow"), warning))
        if not rows:
            console.print("fatal: your current branch does not have any commits yet（还没有订单）")
            return
        if args.graph:
            for (prefix, connector), e in zip(graph(rows), rows):
                line = Text()
                line.append_text(prefix)
                line.append_text(_entry_line(e))
                console.print(line, no_wrap=True, overflow="ellipsis")
                if connector is not None:
                    console.print(connector)
            return
        if args.oneline:
            for e in rows:
                console.print(_entry_line(e), no_wrap=True, overflow="ellipsis")
            return
        for i, e in enumerate(rows):
            if i:
                console.print()
            head = Text("commit ", style=R.HASH)
            head.append(e["hash"], style=R.HASH)
            if e.get("decorations"):
                head.append(" ")
                head.append_text(R.decorations(e["decorations"]))
            console.print(head)
            if e.get("orderId"):
                console.print(Text.assemble("Order:  ", R.mask_order_id(e["orderId"]), " · ", (_short_status(e.get("statusLabel")), _STATE_STYLES.get(str(e.get("state")), ""))))
            console.print(f"Store:  {e.get('store') or ''}" + (f" · {e['mode']}" if e.get("mode") else ""))
            console.print(f"Date:   {str(e.get('time') or '')[:16]}")
            console.print()
            console.print(f"    {e.get('message') or ''}")
            console.print()
            for item in e.get("items") or []:
                console.print(f"    {item}")
            if e.get("total") is not None:
                console.print(f"    {fmt(e['total'])}", style=R.DIM)

    return Result(data, draw)


# ---------------------------------------------------------------- show


def _resolve(ctx: Context, target: str) -> Dict[str, Any]:
    """返回 {"commit": 本地 commit 或 None, "orderId": 订单号或 None, "entry": 历史条目或 None}。"""
    state = ctx.state
    text = target.strip()
    if text.upper() in ("HEAD", "@"):
        head = head_commit(state)
        if head:
            return {"commit": head, "orderId": (head.get("pushed") or {}).get("orderId")}
        rows = history.build(state, ctx.api().order_list())
        if not rows:
            raise GitMcdError("还没有订单", fatal=True)
        return {"commit": None, "orderId": rows[0].get("orderId"), "entry": rows[0]}
    if text.isdigit() and len(text) >= 12:
        commit = next((c for c in state.get("commits", {}).values() if (c.get("pushed") or {}).get("orderId") == text), None)
        return {"commit": commit, "orderId": text}
    if len(text) < 4:
        raise GitMcdError(f"ambiguous argument '{target}': 哈希至少写 4 位", fatal=True)
    local = [c for cid, c in state.get("commits", {}).items() if cid.startswith(text.lower())]
    if len(local) == 1:
        return {"commit": local[0], "orderId": (local[0].get("pushed") or {}).get("orderId")}
    rows = history.find(history.build(state, ctx.api().order_list()), text)
    if len(rows) == 1:
        return {"commit": None, "orderId": rows[0].get("orderId"), "entry": rows[0]}
    raise GitMcdError(f"ambiguous argument '{target}': unknown revision", fatal=True, hint="git mcd log --oneline 看看有哪些")


def run_show(ctx: Context, args: Any) -> Result:
    found = _resolve(ctx, args.target)
    commit = found.get("commit")
    order_id = found.get("orderId")
    head = head_commit(ctx.state)
    decor: List[str] = []
    if commit is not None and head is not None and commit["id"] == head["id"]:
        decor.append(f"HEAD -> {commit.get('branch')}")
    if commit is not None and not order_id:
        return _show_commit(ctx, commit, decor)
    order = ctx.api().query_order(order_id)
    record = order_record(ctx.state, order_id)
    if record is not None:
        record["statusLabel"], record["state"] = order["statusLabel"], order["state"]
        ctx.save()
    demo_paid = ctx.demo and record is not None and order["state"] in ("preparing", "delivering")
    data = {"commit": commit["id"] if commit else None, "order": order, "local": record}

    def draw(console: Console) -> None:
        title = Text()
        if commit is not None:
            title.append(f"commit {commit['id']}", style=R.HASH)
            if decor:
                title.append(" ")
                title.append_text(R.decorations(decor + ([f"origin/{commit.get('branch')}"] if commit.get("pushed") else [])))
        else:
            title.append(f"order {order['orderId']}", style=R.HASH)
        console.print(title)
        if commit is not None:
            console.print(f"Order:   {R.mask_order_id(order['orderId'] or order_id)}")
        status = Text.assemble("Status:  ", (order["statusLabel"], _STATE_STYLES.get(order["state"], "bold")))
        if demo_paid:
            status.append("  （demo：假装你已经扫码付过款）", style=R.DIM)
        console.print(status)
        where = order.get("storeName") or ""
        if order.get("takeWay"):
            where += f" · {(record or {}).get('takeWay') or order['takeWay']}"
        console.print(f"Store:   {where}")
        console.print(f"Date:    {str(order.get('createTime') or '')[:16]}")
        if record and record.get("reservation"):
            console.print(f"预约:    {record['reservation']}")
        if commit is not None:
            console.print()
            console.print(f"    {commit['message']}")
        console.print()
        if order["state"] == "pending":
            deadline = str(order.get("expirePayTime") or "")[11:16]
            console.print(Text(f"待支付：请在 {deadline} 前付款", style="bold yellow"))
            if record and record.get("payH5Url"):
                console.print(Text.assemble("支付链接：", (record["payH5Url"], R.link(record["payH5Url"]))))
            R.hints(console, ["付完再运行一次 git mcd show 看取餐码（mcd-git 不会后台轮询）"])
        if order.get("pickupCode"):
            console.print(Panel(Text(order["pickupCode"], style="bold", justify="center"), title=Text("取餐码"), expand=False, border_style="green", padding=(0, 3)))
        if order.get("lockerCode") or order.get("lockerQrCode"):
            line = Text("取餐柜")
            if order.get("lockerCode"):
                line.append(f"  密码 {order['lockerCode']}", style="bold")
            if order.get("lockerQrCode"):
                line.append("  二维码 ")
                line.append(order["lockerQrCode"], style=R.link(order["lockerQrCode"]))
            console.print(line)
        if order.get("delivery"):
            d = order["delivery"]
            console.print(f"配送:    {d.get('type') or ''} · 预计 {str(d.get('expect') or '')[11:16]} 送达")
        console.print()
        for p in order.get("products") or []:
            parts = " · ".join(c["name"] for c in p.get("items") or [])
            row = Text(f"  {p['name']} x{p['qty']}")
            if parts:
                row.append(f"  [{parts}]", style=R.DIM)
            if p.get("price") is not None:
                row.append(f"  {fmt(p['price'])}")
            console.print(row)
        money = Text("  ")
        if order.get("discount"):
            money.append(f"优惠 -{fmt(order['discount'])} · ", style=R.DIM)
        money.append(f"实付 {fmt(order.get('realTotal'))}", style="bold")
        console.print(money)
        for c in order.get("coupons") or []:
            console.print(f"  券：{c['name']}", style=R.TAG)

    return Result(data, draw)


def _show_commit(ctx: Context, commit: Dict[str, Any], decor: List[str]) -> Result:
    price = commit.get("price") or {}
    remote = commit.get("remote") or {}

    def draw(console: Console) -> None:
        title = Text(f"commit {commit['id']}", style=R.HASH)
        if decor:
            title.append(" ")
            title.append_text(R.decorations(decor))
        console.print(title)
        console.print(f"Store:  {remote.get('storeName')} · {remote.get('modeLabel')}")
        console.print(f"Date:   {str(commit.get('time') or '')[:16]}")
        console.print()
        console.print(f"    {commit['message']}")
        console.print()
        for line in commit.get("lines") or []:
            row = Text.assemble("  ", (summary(line), R.ADD), f" x{line['qty']}")
            if line.get("coupon"):
                row.append(f"  券：{line['coupon']['title']}", style=R.TAG)
            console.print(row)
        console.print(f"  应付 {fmt(price.get('price'))}（commit 时核价）", style="bold")
        if ctx.state.get("index"):
            # 购物车里有东西时直接 push 会报 uncommitted_changes：先 amend 并进这一单，或者 reset 放弃
            tips = ["还没 push，购物车里还有没 commit 的东西：git mcd commit --amend 并进这一单", "不要购物车里的：git mcd reset，再 git mcd push 下单"]
        else:
            tips = ["还没 push：git mcd push 下单（会先确认）"]
        latest = last_order(ctx.state)
        if latest and latest.get("orderId"):
            tips.append(f"看最近一次 push 的订单：git mcd show {latest['orderId']}")
        R.hints(console, tips)

    return Result({"commit": commit["id"], "pushed": False, "message": commit["message"], "payable": price.get("price"), "lines": [summary(x) for x in commit.get("lines") or []]}, draw)
