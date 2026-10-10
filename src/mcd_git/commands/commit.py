"""git mcd commit：把购物车锁成一个本地快照（含核价结果），哈希像 git 一样由内容决定。"""
from __future__ import annotations

import copy
from typing import Any, Dict

from rich.console import Console
from rich.text import Text

from .. import render as R
from ..cart import items_of, summary
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..money import fmt
from ..state import fmt_time, head_commit, make_commit
from .common import line_json, order_scope, same_scope, scope_gap


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser("commit", parents=[common], help="锁定这一单（本地快照 + 核价）", description="把购物车锁成一个本地快照，并调一次 calculate-price 记下应付金额。不会下单。")
    p.add_argument("-m", "--message", metavar="<说明>", help="比如 午饭；不写就用时段名")
    p.add_argument("--amend", action="store_true", help="把购物车并进还没 push 的 HEAD")
    p.set_defaults(func=run)


def price_snapshot(price: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("price", "originalPrice", "discount", "productPrice", "deliveryPrice", "packingPrice", "products", "takeWays")
    return {k: copy.deepcopy(price.get(k)) for k in keys}


def run(ctx: Context, args: Any) -> Result:
    state = ctx.state
    remote = ctx.require_remote()
    head = head_commit(state)
    lines = copy.deepcopy(state.get("index") or [])
    parent = head["id"] if head else None
    message = (args.message or "").strip()
    scope = order_scope(remote)
    if args.amend:
        if head is None:
            raise GitMcdError("You have nothing to amend.", fatal=True)
        if head.get("pushed"):
            raise GitMcdError(f"HEAD {R.short(head['id'])} 已经 push 成订单了，不能 amend", fatal=True, hint="要再来一单：git mcd cherry-pick HEAD")
        if not same_scope(head["remote"], scope):
            raise GitMcdError("HEAD 是在另一家店 / 另一种取餐方式下 commit 的，不能 amend", hint="git mcd reset --soft HEAD~1 撤回后重新 commit")
        lines = copy.deepcopy(head["lines"]) + lines
        parent = head.get("parent")
        message = message or head["message"]
        scope = head["remote"]
    else:
        if head and not head.get("pushed"):
            if lines:
                # 购物车里有东西时直接 push 会报 uncommitted_changes，所以先给 amend / reset，不再让人先去 push；
                # HEAD 是另一家店 / 另一种取餐方式的单时，上面 --amend 分支里同样的 same_scope 判断会拒绝，就不提 amend
                gap = scope_gap(head["remote"], remote)
                first = "git mcd commit --amend 把购物车并进这一单" if gap is None else f"HEAD 是{gap}，购物车没法并进去"
                hint = [first, "不要购物车里的：git mcd reset，再 git mcd push 下单", "git mcd reset --soft HEAD~1 撤回重来"]
            else:
                hint = ["先 git mcd push 下单", "或 git mcd commit --amend 把购物车并进去", "或 git mcd reset --soft HEAD~1 撤回重来"]
            raise GitMcdError(f"HEAD {R.short(head['id'])}（{head['message']}）还没 push", hint=hint)
        if not lines:
            raise GitMcdError('nothing to commit (use "git mcd add <餐品>" to stage)')
    message = message or ctx.branch_label
    try:
        price = ctx.api().price(scope, items_of(lines))
    except McdError as err:
        raise GitMcdError("这一单现在算不了价，没有 commit", remote=err.remote_lines(), hint="看看是不是有东西卖完了：git mcd diff") from None
    commit = make_commit(
        parent=parent,
        branch=ctx.branch,
        message=message,
        time=fmt_time(ctx.now),
        remote=scope,
        lines=lines,
        price=price_snapshot(price),
    )
    if args.amend and head:
        state["commits"].pop(head["id"], None)
    state["commits"][commit["id"]] = commit
    state["head"] = commit["id"]
    state["index"] = []
    state["orig_index"] = None
    state["pending"] = None
    ctx.save()
    root = parent is None or parent not in state["commits"]
    saved = (price.get("originalPrice") or 0) - (price.get("price") or 0)

    def draw(console: Console) -> None:
        tag = f"{commit['branch']}{' (root-commit)' if root else ''}"
        console.print(Text.assemble("[", (tag, R.BRANCH), " ", (R.short(commit["id"]), R.HASH), "] ", message))
        stat = f" {len(lines)} 样，应付 {fmt(price.get('price'))}"
        if saved > 0:
            stat += f"（省 {fmt(saved)}）"
        console.print(stat)
        for line in lines:
            console.print(Text.assemble(" create meal ", (summary(line), R.ADD), f" x{line['qty']}"), style=R.DIM)
        R.hints(console, ["git mcd push 下单前会先给你看确认摘要"])

    return Result(
        {
            "commit": commit["id"],
            "short": R.short(commit["id"]),
            "branch": commit["branch"],
            "message": message,
            "payable": price.get("price"),
            "originalPrice": price.get("originalPrice"),
            "lines": [line_json(x) for x in lines],
            "amend": bool(args.amend),
        },
        draw,
    )
