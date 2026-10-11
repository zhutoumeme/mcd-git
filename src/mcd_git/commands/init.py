"""git mcd init：选门店和取餐方式，相当于设置远端 origin。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from rich.cells import cell_len
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .. import render as R
from ..api import MODES
from ..context import Context, Result, make_remote, remote_brief
from ..errors import GitMcdError, UsageError
from ..state import display_path, empty_state
from .common import remote_desc, remote_url, same_scope

_MIN_COL = 10  # 店名、地址列最少留几格


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser(
        "init",
        parents=[common],
        help="选门店和取餐方式（= 设置远端）",
        description="选门店和取餐方式。不带搜索条件时列收藏门店；--city/--keyword 按地标搜；--pick 选第几家。",
    )
    p.add_argument("--mode", choices=sorted(MODES), default="pickup", help="pickup 到店自取（默认）/ drive 得来速 / delivery 麦乐送")
    p.add_argument("--city", help="城市，如 上海")
    p.add_argument("--keyword", help="地标或路名，如 人民广场")
    p.add_argument("--pick", help="选第几家（列表序号或 storeCode）")
    p.add_argument("--limit", type=int, metavar="N", help="最多列几家（接口一次大约只给 5 家，只在本地截取）")
    p.add_argument("--address", help="麦乐送：选第几个收货地址（列表序号）")
    p.set_defaults(func=run)


def _store_row(i: int, store: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": i,
        "storeCode": store["storeCode"],
        "storeName": store["storeName"],
        "address": store.get("address") or "",
        "distance": store.get("distance"),
        "open": store.get("open"),
        "hours": store.get("hours"),
    }


def _pick(ctx: Context, rows: List[Dict[str, Any]], selector: Optional[str], *, key: str, label: str, render_list: Any) -> Optional[Dict[str, Any]]:
    if selector:
        text = selector.strip()
        if text.isdigit() and 1 <= int(text) <= len(rows) and len(text) <= 3:
            return rows[int(text) - 1]
        for row in rows:
            value = str(row.get(key) or "")
            if value == text or (len(text) >= 4 and value.endswith(text)):
                return row
        raise GitMcdError(f"列表里没有「{selector}」这个{label}", hint=f"序号范围 1-{len(rows)}")
    if len(rows) == 1:
        return rows[0]
    if ctx.interactive:
        render_list(ctx.console)
        answer = ctx.ask(f"选哪一个{label}？[1-{len(rows)}] ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            return rows[int(answer) - 1]
        raise GitMcdError("没选，什么都没改")
    return None


def _store_dist(store: Dict[str, Any]) -> str:
    return f"{store['distance']}m" if store.get("distance") else ""


def _store_status(store: Dict[str, Any]) -> str:
    status = "营业中" if store.get("open") is True else ("已打烊" if store.get("open") is False else "")
    return f"{status} {store.get('hours') or ''}".strip()


def _store_columns(stores: List[Dict[str, Any]], total_width: int) -> Tuple[int, int]:
    """算店名列和地址列的宽度：(name_w, addr_w)。

    序号 2 格、距离、营业三列按内容实测宽度；每列左边留 2 格（第一列的就是缩进）。
    剩下的先保证店名全称，地址至少 10 格；放不下时店名再收缩，最少 10 格。
    """
    fixed = [2]
    dist_w = max((cell_len(_store_dist(s)) for s in stores), default=0)
    status_w = max((cell_len(_store_status(s)) for s in stores), default=0)
    fixed += [w for w in (dist_w, status_w) if w]
    columns = len(fixed) + 2
    remaining = total_width - sum(fixed) - 2 * columns
    name_full = max((cell_len(s.get("storeName") or "") for s in stores), default=0)
    addr_full = max((cell_len(s.get("address") or "") for s in stores), default=0)
    if name_full + addr_full <= remaining:
        return name_full, addr_full
    if remaining - name_full >= _MIN_COL:
        return name_full, remaining - name_full
    return max(_MIN_COL, remaining - _MIN_COL), _MIN_COL


def _render_stores(stores: List[Dict[str, Any]], source: str, notes: Sequence[str] = ()) -> Any:
    def draw(console: Console) -> None:
        title = "收藏门店" if source == "favorites" else "附近门店（按距离）"
        console.print(f"找到 {len(stores)} 家{title}：")
        name_w, addr_w = _store_columns(stores, console.width)
        show_dist = any(_store_dist(s) for s in stores)
        show_status = any(_store_status(s) for s in stores)
        table = Table(box=None, show_header=False, show_edge=False, pad_edge=True, padding=(0, 0, 0, 2))
        table.add_column(width=2, justify="right", no_wrap=True, overflow="ellipsis", style=R.HASH)
        table.add_column(width=name_w, no_wrap=True, overflow="ellipsis")
        table.add_column(width=addr_w, no_wrap=True, overflow="ellipsis", style=R.DIM)
        if show_dist:
            table.add_column(width=max(cell_len(_store_dist(s)) for s in stores), justify="right", no_wrap=True, overflow="ellipsis")
        if show_status:
            table.add_column(width=max(cell_len(_store_status(s)) for s in stores), no_wrap=True, overflow="ellipsis")
        for i, s in enumerate(stores, 1):
            row: List[Any] = [str(i), s.get("storeName") or "", s.get("address") or ""]
            if show_dist:
                row.append(_store_dist(s))
            if show_status:
                row.append(Text(_store_status(s), style=R.ADD if s.get("open") else R.DIM))
            table.add_row(*row)
        console.print(table)
        hints = list(notes)
        if source == "favorites":
            hints.append("收藏门店接口不返回营业时间；想看营业状态用 git mcd init --city <城市> --keyword <地标> 搜")
        R.hints(console, hints)

    return draw


def _apply_remote(ctx: Context, remote: Dict[str, Any]) -> Dict[str, Any]:
    old = ctx.state.get("remote")
    note = None
    if ctx.demo:
        # demo 是游乐场：每次 init 都从头开始（购物车、提交、演示订单一起清掉）
        ctx.store.reset()
        reset = getattr(ctx.client, "reset", None)
        if callable(reset):
            reset()
        ctx.ledger.reset()  # demo 账本也从头来：下次读取时重新播种
        ctx.state = empty_state()
    elif old and not same_scope(old, remote) and ctx.state.get("index"):
        ctx.state["index"] = []
        ctx.state["orig_index"] = None
        note = "换了门店或取餐方式，购物车已清空（商品 code 跟门店和渠道走）"
    ctx.state["remote"] = remote
    ctx.state["candidates"] = None
    ctx.save()
    return {"reinitialized": bool(old), "note": note}


def run(ctx: Context, args: Any) -> Result:
    limit = getattr(args, "limit", None)
    if limit is not None and limit < 1:
        raise UsageError("--limit 至少是 1")
    mode = args.mode
    if mode in ("pickup", "drive"):
        # 先在本地挡掉接口一定会拒的组合，不白调一次 MCP（也不用先有 Token）
        if args.city and not args.keyword:
            # 实测只给 city 时接口返回 code 600058「城市名或者关键词不能为空」
            raise GitMcdError("只给 --city 搜不了门店：还要一个地标或商圈关键词", hint=[f"git mcd init --city {args.city} --keyword 新街口"])
        if args.keyword and not args.city:
            # 实测只给 keyword 同样返回 600058（0.2.1 探测），所以 keyword-only 不放开
            raise GitMcdError("按关键词搜门店要同时带 --city", hint="git mcd init --city 上海 --keyword 人民广场")
    api = ctx.api()
    if mode in ("pickup", "drive"):
        cached = ctx.state.get("candidates") or {}
        notes: List[str] = []
        if args.pick and not (args.city or args.keyword) and cached.get("mode") == mode and cached.get("stores"):
            stores, source = cached["stores"], cached.get("source", "favorites")
        else:
            stores = api.nearby_stores(MODES[mode]["beType"], city=args.city, keyword=args.keyword)
            source = "search" if (args.city or args.keyword) else "favorites"
            if limit is not None:
                if source == "search" and stores and len(stores) < limit:
                    notes.append(f"麦当劳接口这次只返回了 {len(stores)} 家；想看别的店换个更具体的地标")
                stores = stores[:limit]
        if not stores:
            raise GitMcdError(
                "没有找到门店" if source == "search" else "这个账户还没有收藏门店",
                hint="换个地标试试：git mcd init --city 上海 --keyword 人民广场" if source == "search" else "按地标搜：git mcd init --city 上海 --keyword 人民广场",
            )
        chosen = _pick(ctx, stores, args.pick, key="storeCode", label="门店", render_list=_render_stores(stores, source, notes))
        if chosen is None:
            ctx.state["candidates"] = {"mode": mode, "source": source, "stores": stores}
            ctx.save()
            rows = [_store_row(i, s) for i, s in enumerate(stores, 1)]

            def draw(console: Console) -> None:
                _render_stores(stores, source, notes)(console)
                R.hints(console, ["git mcd init --pick <序号> 选一家"])

            return Result({"status": "choose", "kind": "store", "candidates": rows, "next": "git mcd init --pick <序号>"}, draw)
        remote = make_remote(chosen, mode)
    else:
        addresses = api.addresses()
        if not addresses:
            raise GitMcdError("这个账户还没有收货地址", hint="请先在麦当劳 App 里添加收货地址（mcd-git 不会替你新建地址）")

        def draw_addresses(console: Console) -> None:
            console.print(f"找到 {len(addresses)} 个收货地址：")
            for i, a in enumerate(addresses, 1):
                console.print(Text.assemble("  ", (R.lpad(str(i), 2), R.HASH), "  ", a["hint"]))

        address = _pick(ctx, addresses, args.address, key="addressId", label="收货地址", render_list=draw_addresses)
        if address is None:

            def draw(console: Console) -> None:
                draw_addresses(console)
                R.hints(console, ["git mcd init --mode delivery --address <序号> 选一个"])

            return Result(
                {"status": "choose", "kind": "address", "candidates": [{"index": i, "hint": a["hint"]} for i, a in enumerate(addresses, 1)]},
                draw,
            )
        stores = api.delivery_stores(address["addressId"], MODES["delivery"]["beType"])
        if not stores:
            raise GitMcdError("这个地址附近没有能送的门店")
        if args.pick:
            chosen = _pick(ctx, stores, args.pick, key="storeCode", label="门店", render_list=None)
        else:
            chosen = next((s for s in stores if s.get("open") is not False), stores[0])
        remote = make_remote(chosen, "delivery", address=address)

    info = _apply_remote(ctx, remote)
    where = display_path(ctx.store.root)

    def draw_done(console: Console) -> None:
        if ctx.demo:
            console.print(f"Reinitialized demo playground in {where}/")
            console.print("（demo 模式：合成数据，不会真的下单）", style=R.DIM)
        else:
            verb = "Reinitialized" if info["reinitialized"] else "Initialized"
            console.print(f"{verb} mcd remote in {where}/")
        console.print(Text.assemble(("origin", R.BRANCH), "  ", remote_url(remote), "  ", (f"({remote_desc(remote)})", R.DIM)))
        if info["note"]:
            R.hints(console, [info["note"]])
        R.hints(console, ["下一步：git mcd add <餐品>，比如 git mcd add 巨无霸 --no-pickle"])

    return Result({"status": "initialized", "remote": remote_brief(remote), "note": info["note"]}, draw_done)
