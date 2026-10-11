"""git mcd diff：像 unified diff 一样逐行看差价。

- 没用券：a/菜单价 vs b/应付（1 次 calculate-price）；
- 用了券：a/不用券 vs b/用券后（2 次 calculate-price；券和账户折扣互斥，只能各算一次再比）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from rich.console import Console
from rich.text import Text

from .. import render as R
from ..cart import Line, cart_fingerprint, items_of, strip_coupon, summary
from ..context import Context, Result
from ..money import fmt
from ..state import head_commit


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser("diff", parents=[common], help="原价 vs 应付，逐行看差价", description="看购物车的价格明细：没用券时对比菜单价和应付，用了券时对比用券前后。")
    p.add_argument("--staged", "--cached", action="store_true", help="（兼容 git 写法，效果相同）")
    p.set_defaults(func=run)


def match_rows(lines: Sequence[Line], priced: Dict[str, Any]) -> List[Tuple[Line, Optional[Dict[str, Any]]]]:
    """calculate-price 的 productList 和购物车行对齐：先按 code 顺序匹配，剩下的按位置。"""
    products = list(priced.get("products") or [])
    used = [False] * len(products)
    pairs: List[Tuple[Line, Optional[Dict[str, Any]]]] = []
    for line in lines:
        hit = None
        for i, p in enumerate(products):
            if not used[i] and p["code"] == str(line["code"]):
                hit = i
                break
        if hit is None:
            hit = next((i for i, flag in enumerate(used) if not flag), None)
        if hit is not None:
            used[hit] = True
            pairs.append((line, products[hit]))
        else:
            pairs.append((line, None))
    return pairs


def _fees(priced: Dict[str, Any], *, original: bool) -> List[Tuple[str, Optional[int]]]:
    rows = []
    if priced.get("deliveryPrice") is not None:
        rows.append(("配送费", priced.get("deliveryOriginalPrice") if original else priced.get("deliveryPrice")))
    if priced.get("packingPrice") is not None:
        rows.append(("打包费", priced.get("packingPrice")))
    return rows


def build_diff(api: Any, remote: Dict[str, Any], lines: List[Line]) -> Dict[str, Any]:
    has_coupon = any(line.get("coupon") for line in lines)
    current = api.price(remote, items_of(lines))
    rows: List[Dict[str, Any]] = []
    if has_coupon:
        plain = [strip_coupon(line) for line in lines]
        base = api.price(remote, items_of(plain))
        labels = ("不用券", "用券后")
        for (pl, pp), (cl, cp) in zip(match_rows(plain, base), match_rows(lines, current)):
            note = f"券：{cl['coupon']['title']}" if cl.get("coupon") else None
            rows.append({"name": summary(cl), "qty": cl["qty"], "a": pp["subtotal"] if pp else None, "b": cp["subtotal"] if cp else None, "note": note})
        a_total, b_total = base["price"], current["price"]
        a_fees, b_fees = _fees(base, original=False), _fees(current, original=False)
    else:
        base = current
        labels = ("菜单价", "应付")
        for line, p in match_rows(lines, current):
            a = p["original"] if p else None
            b = p["subtotal"] if p else None
            rows.append({"name": summary(line), "qty": line["qty"], "a": a, "b": b, "note": "优惠" if (a is not None and b is not None and b < a) else None})
        a_total, b_total = current["originalPrice"], current["price"]
        a_fees, b_fees = _fees(current, original=True), _fees(current, original=False)
    for (name, a), (_, b) in zip(a_fees, b_fees):
        rows.append({"name": name, "qty": None, "a": a, "b": b, "note": None})
    # 账户级优惠（比如权益卡）不体现在商品行，只体现在顶层 price：补一行把账对平
    for side, total, priced in (("a", a_total, base), ("b", b_total, current)):
        listed = sum(r[side] or 0 for r in rows if r["name"] != "已享优惠")
        gap = (listed - total) if total is not None else 0
        if gap > 0:
            label = (priced.get("enjoyed") or {}).get("promotionName") or "其他优惠"
            row = next((r for r in rows if r["name"] == "已享优惠"), None)
            if row is None:
                row = {"name": "已享优惠", "qty": None, "a": 0, "b": 0, "note": label}
                rows.append(row)
            row[side] = -gap
    changed = sum(1 for r in rows if r["a"] != r["b"])
    return {"labels": labels, "a": a_total, "b": b_total, "saved": (a_total or 0) - (b_total or 0), "rows": rows, "changed": changed, "coupon": has_coupon}


def render_diff(console: Console, remote: Dict[str, Any], diff: Dict[str, Any], *, note: Optional[str] = None) -> None:
    a_label, b_label = diff["labels"]
    width = max([R.cell_len(r["name"]) for r in diff["rows"]] + [10])
    width = min(width, 40)
    if note:
        console.print(note, style=R.DIM)
    console.print(f"diff --mcd a/{a_label} b/{b_label}", style=R.META)
    console.print(Text.assemble((f"--- a/{a_label}", R.META), "  ", fmt(diff["a"])))
    console.print(Text.assemble((f"+++ b/{b_label}", R.META), "  ", fmt(diff["b"])))
    meals = sum(1 for r in diff["rows"] if r["qty"] is not None)
    console.print(f"@@ {remote.get('storeName')} · {remote.get('modeLabel')} · {meals} 样 @@", style=R.HUNK)

    def row_text(sign: str, row: Dict[str, Any], amount: Optional[int], style: Optional[str], show_note: bool) -> Text:
        qty = f"x{row['qty']}" if row["qty"] is not None else ""
        text = Text(f"{sign}  {R.pad(R.clip(row['name'], 40), width)}  {R.pad(qty, 3)}  {R.lpad(fmt(amount), 8)}", style=style)
        if show_note and row.get("note"):
            text.append(f"  {row['note']}", style=R.TAG if sign == "+" else R.DIM)
        return text

    for row in diff["rows"]:
        if row["a"] == row["b"]:
            console.print(row_text(" ", row, row["b"], None, row["name"] == "已享优惠"))
        else:
            if row["a"] not in (None, 0) or row["name"] != "已享优惠":
                console.print(row_text("-", row, row["a"], R.DEL, False))
            console.print(row_text("+", row, row["b"], R.ADD, True))
    saved = diff["saved"]
    stat = f" {meals} 样，{diff['changed']} 行有变化，应付 {fmt(diff['a'])} → {fmt(diff['b'])}"
    if saved > 0:
        stat += f"（省 {fmt(saved)}）"
    console.print(stat)


def run(ctx: Context, args: Any) -> Result:
    lines = ctx.state.get("index") or []
    remote = ctx.require_remote()
    note = None
    if not lines:
        head = head_commit(ctx.state)
        if head and not head.get("pushed"):
            lines = head["lines"]
            remote = head["remote"]
            note = f"（购物车是空的，显示 HEAD {R.short(head['id'])} {head['message']}）"
        else:

            def draw_empty(console: Console) -> None:
                # 和 git diff 一样空输出；只在终端里给一行淡色提示，管道和脚本拿到的仍是空
                if console.is_terminal:
                    console.print(Text("购物车是空的：git mcd add <餐品>", style=R.DIM))

            return Result({"empty": True, "rows": []}, draw_empty)
    diff = build_diff(ctx.api(), remote, lines)
    # rebase 之后购物车没再变（指纹一致）就不再提示 rebase
    suggest_rebase = not diff["coupon"] and ctx.state.get("rebased") != cart_fingerprint(lines)

    def draw(console: Console) -> None:
        render_diff(console, remote, diff, note=note)
        if suggest_rebase:
            R.hints(console, ["试试 git mcd rebase：自动凑套餐、挑最省的券"])

    return Result(dict(diff, labels=list(diff["labels"])), draw)
