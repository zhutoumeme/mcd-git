"""git mcd rebase 的优化器：在「吃的东西不变」的前提下找应付最少的写法。

候选只有两类变换，组合起来做有限枚举：
- squash：购物车里的单品正好能凑满某个套餐的每一轮（主食、小食、饮料都在购物车里）→ 换成套餐。
  凑不满就不换，绝不为了便宜往购物车里多塞东西；
- coupon：本店可用券里，商品能对上购物车某一行的 → 那一行的一份换成券商品。一单只能用一张券。
每个候选都真实调一次 calculate-price（券和账户折扣互斥，本地叠加算不准），每条命令最多 20 次，
带缓存；超出上限就停，并如实告诉用户「可能不是全局最优」。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .api import Menu, McdApi
from .cart import Line, build_line, items_of, strip_coupon, summary
from .errors import GitMcdError, McdError
from .textnorm import normalize

MAX_COMBOS = 4
MAX_COUPON_DETAILS = 3


class Candidate:
    def __init__(self, lines: List[Line], ops: List[Dict[str, Any]], label: str) -> None:
        self.lines = lines
        self.ops = ops
        self.label = label
        self.price: Optional[int] = None
        self.error: Optional[str] = None

    @property
    def key(self) -> str:
        return repr(items_of(self.lines))


def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return str(a.get("code")) == str(b.get("code")) or normalize(str(a.get("name") or "")) == normalize(str(b.get("name") or ""))


def _combo_codes(menu: Menu, singles: Sequence[Line]) -> List[str]:
    """名字里含购物车主食名的套餐（「巨无霸」→「巨无霸套餐」），按主食价格从高到低，最多 4 个。"""
    codes: List[str] = []
    for single in sorted(singles, key=lambda line: -int(line.get("unit") or 0)):
        target = normalize(single["name"])
        if not target:
            continue
        for code, item in menu.items.items():
            if menu.is_combo(code) and target in normalize(item["name"]) and code not in codes:
                codes.append(code)
    return codes[:MAX_COMBOS]


def _cover(detail: Dict[str, Any], pool: List[Tuple[Line, int]]) -> Optional[List[Tuple[int, Dict[str, Any]]]]:
    """每一轮都要从购物车里找到一份对得上的单品；返回 [(pool 下标, 选中的 choice)]，凑不满返回 None。"""
    remaining = [count for _, count in pool]
    picks: List[Tuple[int, Dict[str, Any]]] = []
    for r in detail.get("rounds") or []:
        hit = None
        for choice in r.get("choices") or []:
            for i, (line, _) in enumerate(pool):
                if remaining[i] > 0 and _same(choice, line):
                    hit = (i, choice)
                    break
            if hit:
                break
        if hit is None:
            return None
        remaining[hit[0]] -= 1
        picks.append(hit)
    return picks or None


def _squash(
    ctx: Any, api: McdApi, remote: Dict[str, Any], menu: Menu, lines: List[Line], combo_codes: Sequence[str], new_id: Any
) -> Optional[Candidate]:
    pool = [(line, int(line["qty"])) for line in lines if line["kind"] == "single" and not line.get("coupon")]
    others = [copy.deepcopy(line) for line in lines if not (line["kind"] == "single" and not line.get("coupon"))]
    counts = [count for _, count in pool]
    ops: List[Dict[str, Any]] = []
    made: List[Line] = []
    for code in combo_codes:
        detail = api.meal_detail(remote, code)
        while True:
            live = [(pool[i][0], counts[i]) for i in range(len(pool))]
            picks = _cover(detail, live)
            if not picks:
                break
            used = []
            no: List[str] = []
            with_: List[str] = []
            for i, choice in picks:
                counts[i] -= 1
                single = pool[i][0]
                used.append(single)
                no += [x for x in single.get("custom", {}).get("no", []) if x not in no]
                with_ += [x for x in single.get("custom", {}).get("with", []) if x not in with_]
            combo, _ = build_line(
                line_id=new_id(code),
                code=code,
                menu_item=menu.get(code),
                detail=detail,
                qty=1,
                no=no,
                with_=with_,
                choose=[str(choice.get("name")) for _, choice in picks],
            )
            twin = next((m for m in made if m["code"] == combo["code"] and m["rounds"] == combo["rounds"]), None)
            if twin:
                twin["qty"] += 1
            else:
                made.append(combo)
            for n, single in enumerate(used):
                ops.append({"op": "pick" if n == 0 else "squash", "id": single["id"], "name": summary(single, with_parts=False), "qty": 1, "into": combo["name"]})
    if not made:
        return None
    rest = []
    for (line, _), left in zip(pool, counts):
        if left > 0:
            kept = copy.deepcopy(line)
            kept["qty"] = left
            rest.append(kept)
    label = "squash → " + "、".join(m["name"] for m in made)
    return Candidate(rest + others + made, ops, label)


def _with_coupon(
    api: McdApi, remote: Dict[str, Any], menu: Menu, base: Candidate, coupon: Dict[str, Any], new_id: Any, detail_budget: List[int]
) -> Optional[Candidate]:
    product = (coupon.get("products") or [None])[0]
    if not product:
        return None
    for idx, line in enumerate(base.lines):
        if line.get("coupon") or not _same(product, line):
            continue
        single_unit = copy.deepcopy(line)
        single_unit["qty"] = 1
        if str(product["code"]) == str(line["code"]):
            couponed = copy.deepcopy(single_unit)
        else:
            # 券商品 code 和菜单不同（虚拟商品）：按券商品的 detail 重建，选项按名字对齐
            if detail_budget[0] <= 0:
                return None
            detail_budget[0] -= 1
            try:
                detail = api.meal_detail(remote, product["code"])
            except McdError:
                return None
            custom = line.get("custom") or {}
            choose = [c["name"] for r in line.get("rounds") or [] for c in r["items"]]
            couponed, _ = build_line(
                line_id=new_id(product["code"]),
                code=product["code"],
                menu_item={"name": line["name"], "price": line.get("unit")},
                detail=detail,
                qty=1,
                no=custom.get("no", []),
                with_=custom.get("with", []),
                choose=choose,
            )
        couponed["coupon"] = {"couponId": coupon["couponId"], "couponCode": coupon["couponCode"], "title": coupon["title"]}
        couponed["origin"] = single_unit
        lines = copy.deepcopy(base.lines)
        if int(line["qty"]) > 1:
            lines[idx]["qty"] = int(line["qty"]) - 1
            lines.insert(idx + 1, couponed)
        else:
            lines[idx] = couponed
        ops = copy.deepcopy(base.ops) + [{"op": "coupon", "id": line["id"], "name": line["name"], "qty": 1, "coupon": coupon["title"]}]
        return Candidate(lines, ops, f"{base.label} + 券「{coupon['title']}」".strip(" +"))
    return None


def plan(
    ctx: Any,
    api: McdApi,
    remote: Dict[str, Any],
    lines: List[Line],
    menu: Menu,
    coupons: List[Dict[str, Any]],
    *,
    budget: Optional[int] = None,
    new_id: Any,
) -> Dict[str, Any]:
    start_calls = api.calls["calculate-price"]

    def price(candidate: Candidate) -> bool:
        if api.calls["calculate-price"] >= api.max_price_calls:
            return False
        try:
            candidate.price = api.price(remote, items_of(candidate.lines))["price"]
        except McdError as err:
            candidate.error = err.message
        return True

    base = Candidate([strip_coupon(line) for line in lines], [], "原样")
    try:
        base.price = api.price(remote, items_of(base.lines))["price"]
    except McdError as err:
        raise GitMcdError("购物车本身就算不了价，先看看是不是有东西卖完了", remote=err.remote_lines()) from None

    singles = [line for line in base.lines if line["kind"] == "single"]
    combo_codes = _combo_codes(menu, singles)
    squashes: List[Candidate] = []
    seen = {base.key}
    for codes in ([combo_codes] if len(combo_codes) > 1 else []) + [[c] for c in combo_codes]:
        candidate = _squash(ctx, api, remote, menu, base.lines, codes, new_id)
        if candidate and candidate.key not in seen:
            seen.add(candidate.key)
            squashes.append(candidate)

    queue: List[Candidate] = list(squashes)
    detail_budget = [MAX_COUPON_DETAILS]
    for shape in squashes + [base]:
        for coupon in coupons:
            candidate = _with_coupon(api, remote, menu, shape, coupon, new_id, detail_budget)
            if candidate and candidate.key not in seen:
                seen.add(candidate.key)
                queue.append(candidate)

    tried = [base]
    capped = False
    for candidate in queue:
        if not price(candidate):
            capped = True
            break
        tried.append(candidate)

    valid = [c for c in tried if c.price is not None]
    best = min(valid, key=lambda c: (c.price, len(c.ops)))

    if budget is not None and best.price is not None and best.price > budget:
        current = Candidate(copy.deepcopy(best.lines), copy.deepcopy(best.ops), best.label)
        current.price = best.price
        while current.price is not None and current.price > budget and len(current.lines) > 1:
            victim = current.lines[-1]
            trial = Candidate(current.lines[:-1], current.ops + [{"op": "drop", "id": victim["id"], "name": summary(victim, with_parts=False), "qty": victim["qty"]}], current.label + " - drop")
            if not price(trial):
                capped = True
                break
            tried.append(trial)
            if trial.price is None:
                break
            current = trial
        best = current

    picked_ids = {op["id"] for op in best.ops if op["op"] in ("pick", "squash", "drop")}
    coupon_ids = {op["id"] for op in best.ops if op["op"] == "coupon"}
    todo: List[Dict[str, Any]] = []
    for line in base.lines:
        if line["id"] not in picked_ids and line["id"] not in coupon_ids:
            todo.append({"op": "pick", "id": line["id"], "name": summary(line, with_parts=False), "qty": line["qty"]})
    todo = [op for op in best.ops if op["op"] in ("pick", "squash")] + todo + [op for op in best.ops if op["op"] in ("coupon", "drop")]

    return {
        "before": base.price,
        "after": best.price,
        "saved": (base.price or 0) - (best.price or 0),
        "changed": best is not base and best.key != base.key,
        "lines": best.lines,
        "todo": todo,
        "label": best.label,
        "tried": len(tried),
        "calls": api.calls["calculate-price"] - start_calls,
        "capped": capped,
        "rejected": [{"label": c.label, "error": c.error} for c in tried if c.error],
        "overBudget": budget is not None and (best.price or 0) > budget,
    }
