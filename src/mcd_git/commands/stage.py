"""购物车（暂存区）相关：add、restore、reset、ls-remote。"""
from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.console import Console
from rich.text import Text

from .. import render as R
from ..cart import same_content, summary
from ..context import Context, Result
from ..errors import GitMcdError
from ..money import fmt
from ..state import head_commit
from ..textnorm import normalize
from .common import line_json, make_line, remote_url, resolve

MAX_QTY = 10


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser("add", parents=[common], help="放进购物车（支持 --no-pickle 等特制）", description="把餐品放进购物车（暂存区）。只改本地，不调下单接口。")
    p.add_argument("meals", nargs="+", metavar="<餐品>", help="餐品名或 code，可以一次放多个")
    p.add_argument("-n", "--count", type=int, default=1, metavar="N", help="每样几份（默认 1）")
    p.add_argument("--no-pickle", action="store_true", help="去酸黄瓜")
    p.add_argument("--no", action="append", default=[], metavar="<配料>", help="去掉某样配料，可重复，如 --no 洋葱")
    p.add_argument("--with", dest="with_", action="append", default=[], metavar="<选项>", help="选特制，如 --with 少冰、--with 加一份浓缩")
    p.add_argument("--choose", action="append", default=[], metavar="<选项>", help="套餐换搭，如 --choose 大薯条")
    p.set_defaults(func=run_add)

    p = sub.add_parser("restore", parents=[common], help="从购物车拿出来", description="把餐品移出购物车：按名字、status 里的序号或行 id。")
    p.add_argument("--staged", action="store_true", help="（兼容 git 写法，可省略）")
    p.add_argument("targets", nargs="+", metavar="<餐品>")
    p.set_defaults(func=run_restore)

    p = sub.add_parser(
        "reset",
        parents=[common],
        help="清空购物车 / 撤销 rebase / 撤回没 push 的 commit",
        description="git mcd reset 清空购物车；reset ORIG_HEAD 撤销上一次 rebase；reset --soft HEAD~1 撤回还没 push 的 commit。",
    )
    p.add_argument("--soft", action="store_true", help="撤回 commit，餐品放回购物车")
    p.add_argument("--hard", action="store_true", help="撤回 commit，并清空购物车")
    p.add_argument("target", nargs="?", metavar="<commit>", help="HEAD、HEAD~1 或 ORIG_HEAD")
    p.set_defaults(func=run_reset)

    p = sub.add_parser("ls-remote", parents=[common], help="看本店菜单", description="列出当前门店的菜单（可带关键词过滤）。")
    p.add_argument("keyword", nargs="?", metavar="<关键词>")
    p.set_defaults(func=run_ls_remote)


# ---------------------------------------------------------------- add


def run_add(ctx: Context, args: Any) -> Result:
    if not 1 <= args.count <= MAX_QTY:
        raise GitMcdError(f"每样一次最多 {MAX_QTY} 份", hint="给团队点餐的功能还在路线图里")
    remote = ctx.require_remote()
    api = ctx.api()
    menu = ctx.menu(remote)
    codes = [resolve(menu, query) for query in args.meals]  # 先全部解析，有一个不对就整条不生效
    no = list(args.no) + (["酸黄瓜"] if args.no_pickle else [])
    built = []
    misses: Dict[str, int] = {}
    for code in codes:
        line, unmatched = make_line(ctx, api, remote, menu, code, args.count, no=no, with_=args.with_, choose=args.choose)
        built.append(line)
        for item in unmatched:
            misses[item] = misses.get(item, 0) + 1
    warnings = [f"没有哪样能{item}，已忽略" for item, n in misses.items() if n == len(built)]

    index: List[Dict[str, Any]] = ctx.state.setdefault("index", [])
    added = []
    for line in built:
        twin = next((x for x in index if same_content(x, line)), None)
        if twin is not None and twin["qty"] + line["qty"] <= MAX_QTY:
            twin["qty"] += line["qty"]
            added.append(twin)
        else:
            index.append(line)
            added.append(line)
    ctx.state["orig_index"] = None
    ctx.save()

    def draw(console: Console) -> None:
        for line in built:
            row = Text.assemble("add ", (f"'{line['name']}'", R.ADD), f" x{line['qty']}  ", (fmt(int(line['unit']) * int(line['qty'])), R.DIM))
            parts = [c["name"] for r in line.get("rounds") or [] for c in r["items"]]
            if parts:
                row.append("  [" + " · ".join(parts) + "]", style=R.DIM)
            if line.get("notes"):
                row.append("  " + " ".join(line["notes"]), style=R.TAG)
            if line.get("offerNote"):
                row.append(f"  （{line['offerNote']}）", style=R.DIM)
            console.print(row)
        for w in warnings:
            console.print(Text.assemble(("warning: ", "yellow"), w))

    return Result({"added": [line_json(line) for line in built], "warnings": warnings, "index": [line_json(x) for x in index]}, draw)


# ---------------------------------------------------------------- restore


def _match_lines(index: List[Dict[str, Any]], target: str) -> List[int]:
    text = target.strip()
    if text.isdigit() and 1 <= int(text) <= len(index) and len(text) <= 2:
        return [int(text) - 1]
    hits = [i for i, line in enumerate(index) if len(text) >= 4 and line["id"].startswith(text.lower())]
    if hits:
        return hits
    q = normalize(text)
    exact = [i for i, line in enumerate(index) if normalize(line["name"]) == q]
    return exact or [i for i, line in enumerate(index) if q and q in normalize(line["name"])]


def run_restore(ctx: Context, args: Any) -> Result:
    index = ctx.state.get("index") or []
    drop = set()
    for target in args.targets:
        hits = _match_lines(index, target)
        if not hits:
            raise GitMcdError(f"pathspec '{target}' did not match any staged meals", hint="git mcd status 看看购物车里有什么")
        drop.update(hits)
    removed = [index[i] for i in sorted(drop)]
    ctx.state["index"] = [line for i, line in enumerate(index) if i not in drop]
    ctx.state["orig_index"] = None
    ctx.save()

    def draw(console: Console) -> None:
        for line in removed:
            console.print(Text.assemble("unstage ", (f"'{summary(line)}'", R.DEL), f" x{line['qty']}"))

    return Result({"removed": [line_json(x) for x in removed], "index": [line_json(x) for x in ctx.state["index"]]}, draw)


# ---------------------------------------------------------------- reset


def run_reset(ctx: Context, args: Any) -> Result:
    state = ctx.state
    target = (args.target or "HEAD").strip()
    if target in ("HEAD", "@"):
        if args.soft or args.hard:
            if not (args.hard and target == "HEAD"):
                raise GitMcdError("reset --soft / --hard 要带 HEAD~1", hint="git mcd reset --soft HEAD~1")
        removed = state.get("index") or []
        state["index"] = []
        state["orig_index"] = None
        ctx.save()

        def draw(console: Console) -> None:
            if removed:
                console.print("Unstaged changes after reset:")
                for line in removed:
                    console.print(Text.assemble((f"D\t{summary(line)}", R.DEL), f"  x{line['qty']}"))

        return Result({"action": "unstage-all", "removed": [line_json(x) for x in removed]}, draw)

    if target == "ORIG_HEAD":
        orig = state.get("orig_index")
        if orig is None:
            raise GitMcdError("没有可以撤销的 rebase（ORIG_HEAD 不存在）", fatal=True)
        state["index"] = copy.deepcopy(orig)
        state["orig_index"] = None
        ctx.save()

        def draw_orig(console: Console) -> None:
            console.print("HEAD is now at ORIG_HEAD（rebase 之前的购物车）")
            for line in state["index"]:
                console.print(Text.assemble("\t", (summary(line), R.ADD), f"  x{line['qty']}"))

        return Result({"action": "orig-head", "index": [line_json(x) for x in state["index"]]}, draw_orig)

    if target in ("HEAD~1", "HEAD^", "HEAD~"):
        head = head_commit(state)
        if head is None:
            raise GitMcdError("还没有 commit，没东西可以撤回", fatal=True)
        if head.get("pushed"):
            raise GitMcdError(
                f"HEAD {R.short(head['id'])} 已经 push 成订单了，不能 reset",
                fatal=True,
                hint="要取消订单用 git mcd revert（会先让你确认）",
            )
        parent = head.get("parent")
        state["commits"].pop(head["id"], None)
        state["head"] = parent if parent in state["commits"] else None
        if args.hard:
            state["index"] = []
        else:
            state["index"] = copy.deepcopy(head["lines"]) + (state.get("index") or [])
        if state.get("pending") and state["pending"].get("commit") == head["id"]:
            state["pending"] = None
        state["orig_index"] = None
        ctx.save()
        new_head = state["commits"].get(state["head"]) if state["head"] else None

        def draw_head(console: Console) -> None:
            if new_head:
                console.print(Text.assemble("HEAD is now at ", (R.short(new_head["id"]), R.HASH), f" {new_head['message']}"))
            else:
                console.print(f"已撤回 commit {R.short(head['id'])}（{head['message']}）")
            if not args.hard:
                console.print("餐品已放回购物车，改完再 git mcd commit", style=R.DIM)

        return Result({"action": "uncommit", "commit": head["id"], "hard": bool(args.hard), "index": [line_json(x) for x in state["index"]]}, draw_head)

    raise GitMcdError(f"ambiguous argument '{target}': unknown revision", fatal=True, hint="支持 HEAD、HEAD~1、ORIG_HEAD")


# ---------------------------------------------------------------- ls-remote


def run_ls_remote(ctx: Context, args: Any) -> Result:
    remote = ctx.require_remote()
    menu = ctx.menu(remote)
    items = list(menu.items.values())
    if args.keyword:
        q = normalize(args.keyword)
        items = [i for i in items if q in normalize(i["name"]) or i["code"] == args.keyword]
    rows = [
        {
            "code": i["code"],
            "name": i["name"],
            "type": "套餐" if menu.is_combo(i["code"]) else "单品",
            "price": i["price"],
            "original": i["original"],
            "discountType": i.get("discountType"),
            "category": (i.get("categories") or [""])[0],
            # price 已经是不附加条件就能拿到的价；条件优惠价单独给
            "offerPrice": (i.get("offer") or {}).get("price"),
            "offerNote": (i.get("offer") or {}).get("note"),
        }
        for i in items
    ]

    groups = _menu_groups(menu, rows) if not args.keyword and menu.categories else [(None, rows)]
    tips = ["git mcd ls-remote <关键词> 搜索"] if not args.keyword and rows else []

    def tail(row: Dict[str, Any]) -> str:
        """价格后面的淡色说明：条件优惠价，或者促销的原价。"""
        if row["offerNote"]:
            return f"  {row['offerNote']}"
        if row["original"] and row["price"] is not None and row["original"] > row["price"]:
            return f"  原价 {fmt(row['original'])} {row.get('discountType') or ''}".rstrip()
        return ""

    def render(console: Console) -> None:
        console.print(f"From {remote_url(remote)}", style=R.DIM)
        longest = max([R.cell_len(row["name"]) for row in rows] + [0])
        tail_w = max([R.cell_len(tail(row)) for row in rows] + [0])  # 给行尾说明留宽度，带说明的行不折行
        name_w = min(longest, max(22, console.width - 12 - 6 - 8 - 2 - tail_w))
        for title, members in groups:
            if title is not None:
                console.print(f"## {title}（{len(members)}）", style=R.DIM)
            for row in members:
                line = Text()
                line.append(R.pad(row["code"], 12), style=R.HASH)
                line.append(R.pad(row["type"], 6), style=R.DIM)
                line.append(R.pad(R.clip(row["name"], name_w), name_w + 2))
                line.append(R.lpad(fmt(row["price"]), 8))
                note = tail(row)
                if note:
                    line.append(note, style=R.DIM)
                console.print(line)
        if not rows:
            console.print("（没有匹配的餐品）", style=R.DIM)
        R.hints(console, tips)

    def draw(console: Console) -> None:
        # 估算行数：From + 每组标题 + 每样一行 + hint；超过一屏才走分页器（同 git，LESS=FRX：保留颜色、不满一屏自动退出）
        height = 1 + len(rows) + sum(1 for title, _ in groups if title is not None) + len(tips) + (0 if rows else 1)
        if console.is_terminal and height > console.height:
            os.environ.setdefault("LESS", "FRX")
            with console.pager(styles=True):
                render(console)
        else:
            render(console)

    return Result({"remote": remote_url(remote), "meals": rows}, draw)


def _menu_groups(menu: Any, rows: List[Dict[str, Any]]) -> List[Tuple[Optional[str], List[Dict[str, Any]]]]:
    """按菜单分类顺序分组；同一 code 只放在它第一次出现的分类里，没进任何分类的放最后「其他」。"""
    by_code = {row["code"]: row for row in rows}
    seen: Set[str] = set()
    groups: List[Tuple[Optional[str], List[Dict[str, Any]]]] = []
    for cname, codes in menu.categories:
        members = []
        for code in codes:
            if code in by_code and code not in seen:
                seen.add(code)
                members.append(by_code[code])
        if members:
            groups.append((cname or "其他", members))
    rest = [row for row in rows if row["code"] not in seen]
    if rest:
        groups.append(("其他", rest))
    return groups
