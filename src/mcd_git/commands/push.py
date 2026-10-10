"""git mcd push：真正下单（create-order）。整条命令就是一道确认闸，规则见 gate.py。"""
from __future__ import annotations

import argparse
import re
import shlex
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import gate
from .. import render as R
from ..api import scope_args
from ..cart import items_of, summary
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..money import fmt
from ..state import CN_TZ, fmt_time, head_commit, parse_time, verify_commit
from .diff import match_rows


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser(
        "push",
        parents=[common],
        help="真正下单，出支付二维码（会先确认）",
        description=(
            "把 HEAD 这一单真正下到麦当劳（create-order，生成待支付订单，不会自动付款）。"
            "终端里会先给确认摘要并问 y/N；非交互（Agent / 管道 / --json）只出摘要，"
            "用户明确同意后再加 --confirm <commit 哈希> 执行。"
        ),
    )
    p.add_argument("--at", metavar="HH:MM", help="预约时间：HH:MM（今天）或 'YYYY-MM-DD HH:MM'")
    way = p.add_mutually_exclusive_group()
    way.add_argument("--locker", action="store_true", help="外带 · 店外取餐柜（门店有才行）")
    way.add_argument("--eat-in", action="store_true", help="堂食")
    way.add_argument("--take-out", action="store_true", help="外带 · 店内自提")
    way.add_argument("--take-way", metavar="CODE", help="直接指定 takeWayCode（取自算价返回的 takeWayList）")
    p.add_argument("--no-tableware", action="store_true", help="不要餐具")
    p.add_argument("--remark", metavar="<备注>", help="订单备注（仅麦乐送，最多 50 字）")
    p.add_argument("--dry-run", action="store_true", help="只看确认摘要，不下单")
    p.add_argument("--confirm", metavar="<commit>", help="用户已同意：按预览里给的 commit 哈希下单")
    p.add_argument("-f", "--force", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=run)


# ---------------------------------------------------------------- 参数


def parse_at(ctx: Context, text: str) -> str:
    raw = text.strip()
    match = re.fullmatch(r"(\d{1,2})[:：](\d{2})", raw)
    when: Optional[datetime] = None
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise GitMcdError(f"--at {raw} 不是有效时间")
        when = ctx.now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        try:
            when = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
        except ValueError:
            raise GitMcdError(f"--at {raw} 看不懂", hint="写成 12:20 或 '2026-10-24 12:20'") from None
    if when <= ctx.now:
        raise GitMcdError(f"--at {raw} 已经过去了")
    if when - ctx.now > timedelta(days=3):
        raise GitMcdError(f"--at {raw} 太远了，门店只开放最近几天的预约")
    return when.strftime("%Y-%m-%d %H:%M")


def push_options(ctx: Context, args: Any, scope: Dict[str, Any]) -> Dict[str, Any]:
    at = parse_at(ctx, args.at) if args.at else None
    selector = None
    if args.locker:
        selector = "locker"
    elif args.eat_in:
        selector = "eat-in"
    elif args.take_out:
        selector = "take-out"
    elif args.take_way:
        selector = "code:" + args.take_way.strip()
    if int(scope["orderType"]) == 2 and selector:
        raise GitMcdError("麦乐送不用选取餐方式（--locker / --eat-in / --take-out 只用于到店和得来速）")
    remark = (args.remark or "").strip() or None
    if remark and int(scope["orderType"]) != 2:
        raise GitMcdError("--remark 只支持麦乐送订单", hint="到店的特殊要求请用特制：git mcd add <餐品> --no <配料> / --with <选项>")
    if remark and len(remark) > 50:
        raise GitMcdError(f"--remark 最多 50 个字（现在 {len(remark)} 个）")
    return {"at": at, "takeWay": selector, "tableware": False if args.no_tableware else None, "remark": remark}


def choose_take_way(scope: Dict[str, Any], take_ways: List[Dict[str, Any]], selector: Optional[str]) -> Optional[Dict[str, Any]]:
    """takeWayCode 只能取自这次 calculate-price 的 takeWayList（因店而异），不猜。"""
    if int(scope["orderType"]) == 2:
        return None
    if not take_ways:
        raise GitMcdError("failed to push some refs: 门店没有返回可用的取餐方式", hint="稍后再试，或换一家店：git mcd init")
    available = "、".join(f"{t['title']}·{t['subtitle']}（{t['code']}）" for t in take_ways)
    if selector is None:
        return take_ways[0]
    if selector == "locker":
        hit = next((t for t in take_ways if t["code"].startswith("locker")), None)
        if hit is None:
            raise GitMcdError("failed to push some refs: 这家店没有取餐柜", hint=f"可选：{available}")
        return hit
    if selector == "eat-in":
        hit = next((t for t in take_ways if t["code"] == "eat-in"), None)
    elif selector == "take-out":
        hit = next((t for t in take_ways if t["code"] == "take-in-store"), None) or next(
            (t for t in take_ways if t["code"] != "eat-in" and not t["code"].startswith("locker")), None
        )
    else:
        hit = next((t for t in take_ways if t["code"] == selector[5:]), None)
    if hit is None:
        raise GitMcdError("failed to push some refs: 这家店不支持这种取餐方式", hint=f"可选：{available}")
    return hit


def order_args(scope: Dict[str, Any], lines: List[Dict[str, Any]], options: Dict[str, Any], take_way: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    args = scope_args(scope, options["at"])
    args["items"] = items_of(lines)
    if take_way:
        args["takeWayCode"] = take_way["code"]
    if int(scope["orderType"]) == 2:
        if not scope.get("addressId"):
            raise GitMcdError("这一单没有收货地址", fatal=True, hint="git mcd init --mode delivery 重新选地址")
        args["addressId"] = scope["addressId"]
        if options["remark"]:
            args["remark"] = options["remark"]
    if options["tableware"] is not None:
        args["needTableware"] = options["tableware"]
    return args


def confirm_command(options: Dict[str, Any], short: str, *, demo: bool = False, json_mode: bool = False) -> str:
    """确认后要运行的命令：参数和预览时一模一样，demo / json 模式也原样带上。"""
    parts = ["git", "mcd"] + (["--demo"] if demo else []) + ["push"]
    if options["at"]:
        parts += ["--at", options["at"]]
    selector = options["takeWay"]
    if selector == "locker":
        parts.append("--locker")
    elif selector == "eat-in":
        parts.append("--eat-in")
    elif selector == "take-out":
        parts.append("--take-out")
    elif selector:
        parts += ["--take-way", selector[5:]]
    if options["tableware"] is False:
        parts.append("--no-tableware")
    if options["remark"]:
        parts += ["--remark", options["remark"]]
    parts += ["--confirm", short]
    if json_mode:
        parts.append("--json")
    return " ".join(shlex.quote(p) for p in parts)


# ---------------------------------------------------------------- 摘要


def build_summary(head: Dict[str, Any], options: Dict[str, Any], price: Dict[str, Any], take_way: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    scope = head["remote"]
    items = []
    for line, p in match_rows(head["lines"], price):
        items.append(
            {
                "name": summary(line),
                "qty": line["qty"],
                "subtotal": p["subtotal"] if p else None,
                "coupon": (line.get("coupon") or {}).get("title"),
            }
        )
    committed = (head.get("price") or {}).get("price")
    return {
        "commit": head["id"],
        "short": R.short(head["id"]),
        "message": head["message"],
        "store": scope.get("storeName"),
        "storeCode": scope.get("storeCode"),
        "mode": scope.get("modeLabel"),
        "takeWay": f"{take_way['title']} · {take_way['subtitle']}" if take_way else None,
        "takeWayCode": take_way["code"] if take_way else None,
        "reservation": options["at"],
        "tableware": "不要" if options["tableware"] is False else "默认",
        "remark": options["remark"],
        "address": scope.get("addressHint"),
        "items": items,
        "originalPrice": price.get("originalPrice"),
        "discount": (price.get("originalPrice") or 0) - (price.get("price") or 0),
        "deliveryPrice": price.get("deliveryPrice"),
        "packingPrice": price.get("packingPrice"),
        "payable": price.get("price"),
        "priceChanged": {"from": committed, "to": price.get("price")} if committed is not None and committed != price.get("price") else None,
    }


def render_summary(console: Console, s: Dict[str, Any], *, demo: bool) -> None:
    meta = Table.grid(padding=(0, 2))
    meta.add_column(style=R.DIM)
    meta.add_column()
    meta.add_row("门店", f"{s['store']}（{s['mode']}）")
    if s.get("address"):
        meta.add_row("送到", s["address"])
    if s.get("takeWay"):
        meta.add_row("取餐", s["takeWay"])
    meta.add_row("时间", f"预约 {s['reservation']}" if s.get("reservation") else "现在")
    meta.add_row("餐具", s["tableware"])
    if s.get("remark"):
        meta.add_row("备注", s["remark"])
    items = Table.grid(padding=(0, 2))
    items.add_column()
    items.add_column(justify="right")
    items.add_column(justify="right")
    for item in s["items"]:
        name = Text(item["name"])
        if item.get("coupon"):
            name.append(f"  券：{item['coupon']}", style=R.TAG)
        items.add_row(name, f"x{item['qty']}", fmt(item.get("subtotal")))
    total = Text()
    total.append(f"原价 {fmt(s['originalPrice'])}", style=R.DIM)
    if s["discount"]:
        total.append(f"   优惠 -{fmt(s['discount'])}", style=R.DIM)
    if s.get("deliveryPrice") is not None:
        total.append(f"   含配送 {fmt(s['deliveryPrice'])}", style=R.DIM)
    total.append("   应付 ")
    total.append(fmt(s["payable"]), style="bold green")
    body: List[Any] = [meta, Text(""), items, Text(""), total]
    if s.get("priceChanged"):
        body.append(Text(f"注意：价格和 commit 时不一样（{fmt(s['priceChanged']['from'])} → {fmt(s['priceChanged']['to'])}）", style="bold yellow"))
    title = f"即将 push {s['short']}「{s['message']}」" + ("（demo）" if demo else "")
    console.print(Panel(Group(*body), title=Text(title), title_align="left", border_style="yellow", expand=False))


# ---------------------------------------------------------------- 下单


def _place(ctx: Context, head: Dict[str, Any], args_: Dict[str, Any], s: Dict[str, Any]) -> Result:
    api = ctx.api()
    scope = head["remote"]
    approval = gate.issue("create-order", args_)
    ctx.state["pending"] = None  # 先把预览消费掉：同一份确认只能用一次
    ctx.save()
    try:
        created = api.create_order(approval, args_)
    except McdError as err:
        unknown = err.transport or (err.http_status or 0) >= 500
        hint = (
            ["不确定订单有没有建成：先运行 git mcd log 看最新订单，不要直接重试（避免重复下单）"]
            if unknown
            else ["什么都没下单；处理完上面的问题后重新 git mcd push 预览"]
        )
        raise GitMcdError(f"failed to push some refs: {err.message}", remote=err.remote_lines(), hint=hint) from None
    order = created.get("order") or {}
    order_id = created.get("orderId") or order.get("orderId") or ""
    expire = order.get("expirePayTime") or fmt_time(ctx.now + timedelta(minutes=15))
    state_name = order.get("state") if order.get("state") not in (None, "unknown") else "pending"
    record = {
        "orderId": order_id,
        "commit": head["id"],
        "time": fmt_time(ctx.now),
        "storeName": scope.get("storeName"),
        "mode": scope.get("modeLabel"),
        "takeWay": s.get("takeWay"),
        "reservation": s.get("reservation"),
        "payable": s.get("payable"),
        "payH5Url": created.get("payH5Url"),
        "expirePayTime": expire,
        "statusLabel": order.get("statusLabel") or "待支付",
        "state": state_name,
    }
    head["pushed"] = {"orderId": order_id, "time": record["time"]}
    ctx.state.setdefault("orders", []).append(record)
    ctx.save()
    first = head["lines"][0]["name"] if head["lines"] else "order"
    ref = first + (f" 等 {len(head['lines'])} 样" if len(head["lines"]) > 1 else "")
    pay_url = created.get("payH5Url")
    free = (s.get("payable") or 0) == 0

    def draw(console: Console) -> None:
        n = sum(int(line["qty"]) for line in head["lines"])
        console.print(f"Enumerating meals: {n}, done.")
        console.print(f"Writing objects: 100% ({n}/{n}), {fmt(s.get('payable'))}, done.")
        console.print(f"To mcd.cn:{scope.get('storeName')}")
        console.print(Text.assemble(" * ", ("[new order]", R.ADD), "      ", (head.get("branch") or ctx.branch, R.BRANCH), " -> ", (ref, R.BRANCH)))
        deadline = (expire or "")[11:16]
        if free and not pay_url:
            console.print(Text.assemble("订单 ", (R.mask_order_id(order_id), R.HASH), f" · 应付 {fmt(0)}，不用付款"))
        else:
            console.print(Text.assemble("订单 ", (R.mask_order_id(order_id), R.HASH), " · ", (record["statusLabel"], "bold yellow"), f" · 请在 {deadline} 前付款"))
        if pay_url:
            console.print(R.qr_text(pay_url, color=console.color_system is not None), no_wrap=True, crop=False)
            console.print(Text.assemble("支付链接：", (pay_url, R.link(pay_url))))
            if ctx.demo:
                console.print("（demo：演示链接，不会扣款）", style=R.DIM)
            R.hints(console, ["用手机扫码或打开麦当劳 App 完成支付（mcd-git 不会替你付款）", "付完运行 git mcd show 查看取餐码"])
        elif not free:
            R.hints(console, ["没拿到支付链接：打开麦当劳 App 的订单页付款", "付完运行 git mcd show 查看取餐码"])
        else:
            R.hints(console, ["运行 git mcd show 查看取餐码"])

    data = {
        "status": "pushed",
        "orderId": order_id,
        "payH5Url": pay_url,
        "expirePayTime": expire,
        "payable": s.get("payable"),
        "orderStatus": record["statusLabel"],
        "summary": s,
        "next": "付款后运行 git mcd show 查看取餐码",
    }
    return Result(data, draw)


def run(ctx: Context, args: Any) -> Result:
    if args.force:
        raise GitMcdError(
            "--force 不支持：订单不能强推",
            fatal=True,
            hint="每一单都要先确认；想改单就 git mcd reset --soft HEAD~1 改完重新 commit",
        )
    if args.dry_run and args.confirm:
        raise GitMcdError("--dry-run 和 --confirm 不能一起用")
    state = ctx.state
    head = head_commit(state)
    if head is None:
        if state.get("index"):
            raise GitMcdError("购物车还没 commit", fatal=True, hint="先 git mcd commit -m <说明>")
        raise GitMcdError("还没有可以 push 的 commit", fatal=True, hint="git mcd add <餐品> && git mcd commit -m <说明>")
    if head.get("pushed"):

        def draw_up(console: Console) -> None:
            console.print("Everything up-to-date")
            R.hints(console, ["HEAD 已经下过单了；再来一单：git mcd cherry-pick HEAD"])

        return Result({"status": "up-to-date", "orderId": head["pushed"].get("orderId")}, draw_up)
    if not verify_commit(head):
        raise GitMcdError(f"commit {R.short(head['id'])} 校验失败：快照内容被改过", fatal=True, hint="git mcd reset --soft HEAD~1 撤回后重新 commit")
    scope = head["remote"]
    options = push_options(ctx, args, scope)
    pending = state.get("pending") or {}
    api = ctx.api()

    if args.confirm:
        confirm = args.confirm.strip().lower()
        if len(confirm) < 7 or not re.fullmatch(r"[0-9a-f]+", confirm):
            raise GitMcdError("--confirm 要填预览里给的 commit 哈希（至少 7 位）")
        if not head["id"].startswith(confirm):
            raise GitMcdError(f"--confirm {confirm} 和 HEAD {R.short(head['id'])} 对不上", hint="HEAD 变了就重新预览：git mcd push")
        if pending.get("kind") != "push" or pending.get("commit") != head["id"]:
            raise GitMcdError(
                "还没有给用户看过这一单的确认摘要",
                hint=["先不带 --confirm 运行一次 git mcd push（参数相同），把摘要给用户看", "用户明确同意后，再加 --confirm <commit> 执行"],
            )
        if pending.get("options") != options:
            raise GitMcdError(
                "push 参数和预览时不一样",
                hint=["参数要和预览时一模一样；改了参数就重新预览", "预览时对应的命令：" + confirm_command(pending.get("options") or {}, R.short(head["id"]), demo=ctx.demo, json_mode=ctx.json)],
                detail={"previewed": pending.get("options"), "now": options},
            )
        previewed_at = parse_time(pending.get("time"))
        if previewed_at is None or ctx.now - previewed_at > gate.PENDING_TTL:
            state["pending"] = None
            ctx.save()
            raise GitMcdError("确认已过期（预览超过 10 分钟）", hint="重新运行 git mcd push 预览，再让用户确认一次")

    try:
        price = api.price(scope, items_of(head["lines"]), reservation=options["at"], need_tableware=options["tableware"])
    except McdError as err:
        raise GitMcdError(f"failed to push some refs: {err.message}", remote=err.remote_lines(), hint="什么都没下单") from None
    take_way = choose_take_way(scope, price.get("takeWays") or [], options["takeWay"])
    args_ = order_args(scope, head["lines"], options, take_way)
    fingerprint = gate.fingerprint(args_)
    s = build_summary(head, options, price, take_way)

    if args.confirm:
        if price.get("price") != pending.get("payable"):
            state["pending"] = None
            ctx.save()
            raise GitMcdError(
                f"failed to push some refs: 价格变了 {fmt(pending.get('payable'))} → {fmt(price.get('price'))}",
                hint="什么都没下单；重新运行 git mcd push 预览，让用户按新价格确认",
            )
        if fingerprint != pending.get("fingerprint"):
            state["pending"] = None
            ctx.save()
            raise GitMcdError("failed to push some refs: 下单参数和预览时不一致（取餐方式或商品变了）", hint="什么都没下单；重新预览")
        return _place(ctx, head, args_, s)

    state["pending"] = {
        "kind": "push",
        "commit": head["id"],
        "options": options,
        "payable": price.get("price"),
        "fingerprint": fingerprint,
        "time": fmt_time(ctx.now),
    }
    ctx.save()
    command = confirm_command(options, R.short(head["id"]), demo=ctx.demo, json_mode=ctx.json)

    if ctx.interactive and not args.dry_run:
        render_summary(ctx.console, s, demo=ctx.demo)
        answer = ctx.ask("确认下单？会在你的麦当劳账户里创建一笔待支付订单（不会自动付款）[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            state["pending"] = None
            ctx.save()
            return Result({"status": "aborted", "summary": s}, lambda console: console.print("没有下单。"))
        return _place(ctx, head, args_, s)

    def draw_preview(console: Console) -> None:
        render_summary(console, s, demo=ctx.demo)
        console.print("这一步会在你的麦当劳账户里创建一笔待支付订单（不会自动付款）。")
        console.print(Text.assemble("用户确认无误后运行：", (command, "bold")))

    return Result(
        {
            "status": "confirmation_required",
            "confirm": R.short(head["id"]),
            "summary": s,
            "next": command,
            "expiresInMinutes": int(gate.PENDING_TTL.total_seconds() // 60),
        },
        draw_preview,
    )
