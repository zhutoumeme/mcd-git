"""git mcd cherry-pick：把一单拣回购物车（再来一单），相当于 git cherry-pick -n。

商品 code 跟门店和渠道走（实测历史订单的 code 在别的店基本对不上），所以只要不是同一门店同一渠道，
就按当前门店菜单重新解析：先按 code，再按规范化后的名字，套餐再按子项名逐轮对齐。对不上的明确报 CONFLICT。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from rich.console import Console
from rich.text import Text

from .. import history
from .. import render as R
from ..cart import strip_coupon, summary
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..state import head_commit, next_line_id
from .common import line_json, make_line, remote_url, same_scope


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser("cherry-pick", parents=[common], help="把一单拣回购物车（再来一单）", description="把历史上的一单拣回购物车。只放进购物车，不会下单。")
    p.add_argument("target", metavar="<commit|订单号|HEAD>", help="HEAD 就是最近一单")
    p.set_defaults(func=run)


def _source(ctx: Context, target: str) -> Dict[str, Any]:
    state = ctx.state
    text = target.strip()
    if text.upper() in ("HEAD", "@"):
        head = head_commit(state)
        if head:
            return {"commit": head, "label": head["message"], "hash": head["id"]}
    elif len(text) >= 4 and not text.isdigit():
        local = [c for cid, c in state.get("commits", {}).items() if cid.startswith(text.lower())]
        if len(local) == 1:
            return {"commit": local[0], "label": local[0]["message"], "hash": local[0]["id"]}
    rows = history.find(history.build(state, ctx.api().order_list()), text)
    if len(rows) != 1:
        raise GitMcdError(f"bad revision '{target}'", fatal=True, hint="git mcd log --oneline 看看有哪些")
    row = rows[0]
    if row.get("local"):
        commit = state["commits"].get(row["hash"])
        if commit:
            return {"commit": commit, "label": commit["message"], "hash": commit["id"]}
    return {"order": row.get("order") or {}, "label": row.get("message"), "hash": row["hash"]}


def run(ctx: Context, args: Any) -> Result:
    state = ctx.state
    if state.get("index"):
        raise GitMcdError(
            "your local changes would be overwritten by cherry-pick.",
            hint=["购物车里还有东西：先 git mcd commit，或 git mcd reset 清空"],
        )
    remote = ctx.require_remote()
    src = _source(ctx, args.target)
    lines: List[Dict[str, Any]] = []
    conflicts: List[str] = []
    commit = src.get("commit")
    if commit is not None and same_scope(commit.get("remote"), remote):
        for line in commit.get("lines") or []:
            plain = strip_coupon(line)  # 券是一次性的，不跟着拣回来
            plain = copy.deepcopy(plain)
            plain["id"] = next_line_id(state, f"pick:{plain['code']}")
            lines.append(plain)
    else:
        if commit is not None:
            products = [
                {
                    "code": line["code"],
                    "name": line["name"],
                    "qty": line["qty"],
                    "items": [c["name"] for r in line.get("rounds") or [] for c in r["items"]],
                    "custom": line.get("custom") or {},
                }
                for line in (strip_coupon(x) for x in commit.get("lines") or [])
            ]
        else:
            products = [
                {"code": p["code"], "name": p["name"], "qty": p.get("qty", 1), "items": [i["name"] for i in p.get("items") or []], "custom": {}}
                for p in (src.get("order") or {}).get("products") or []
            ]
        api = ctx.api()
        menu = api.menu(remote)
        for p in products:
            code = p["code"] if p["code"] in menu.items else menu.by_name(p["name"])
            if code is None:
                conflicts.append(p["name"])
                continue
            custom = p.get("custom") or {}
            try:
                line, _ = make_line(ctx, api, remote, menu, code, max(1, int(p["qty"])), no=custom.get("no", []), with_=custom.get("with", []), choose=p["items"])
            except McdError:
                conflicts.append(p["name"])
                continue
            lines.append(line)
    if not lines:
        raise GitMcdError(
            f"could not apply {R.short(src['hash'])}... {src.get('label') or ''}：{remote.get('storeName')} 一样都对不上",
            hint="换一家店试试：git mcd init",
            detail={"conflicts": conflicts},
        )
    state["index"] = lines
    state["orig_index"] = None
    ctx.save()

    def draw(console: Console) -> None:
        console.print(Text.assemble("[", (ctx.branch, R.BRANCH), "] cherry-pick ", (R.short(src["hash"]), R.HASH), f" {src.get('label') or ''}"))
        for line in lines:
            console.print(Text.assemble(" add ", (f"'{summary(line)}'", R.ADD), f" x{line['qty']}"))
        for name in conflicts:
            console.print(Text.assemble(("CONFLICT (not on menu): ", "bold red"), f"{name} 在 {remote_url(remote)} 现在不可售，已跳过"))
        R.hints(console, ["只放进了购物车（相当于 cherry-pick -n）：git mcd diff 看价格，git mcd rebase 挑券，git mcd commit 锁单"])

    return Result({"source": src["hash"], "index": [line_json(x) for x in lines], "conflicts": conflicts}, draw)
