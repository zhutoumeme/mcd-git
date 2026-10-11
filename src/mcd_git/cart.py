"""购物车行：从 query-meal-detail 构造一行，以及把一行转成 calculate-price / create-order 的 item。

特制的传参规则（来自工具描述 + 实测）：
- 带 unselectedKey 的组（多选配料）：组内所有项都要传，选中的用 selectedKey，没选中的用 unselectedKey；
- 单选档位组（冰量、加浓缩）：只传选中的那一项；
- 服务端算价不校验特制 key，所以特制合法性只能在本地按 detail 校验（这里只接受 detail 里真有的选项）。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .money import fmt, parse_diff_price
from .textnorm import normalize

Line = Dict[str, Any]


def _qty(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _find(values: Sequence[Dict[str, Any]], name: str) -> Optional[int]:
    """先精确、再包含地按名称找选项下标。"""
    target = normalize(name)
    if not target:
        return None
    for i, value in enumerate(values):
        if normalize(str(value.get("name") or "")) == target:
            return i
    for i, value in enumerate(values):
        if target in normalize(str(value.get("name") or "")):
            return i
    return None


class _Mods:
    """一份 modification 的可变选择状态。"""

    def __init__(self, modification: Any) -> None:
        self.groups: List[Dict[str, Any]] = []
        for item in (modification or {}).get("items") or [] if isinstance(modification, dict) else []:
            values = [v for v in item.get("values") or [] if isinstance(v, dict)]
            multi = any(v.get("unselectedKey") for v in values)
            selected = {i for i, v in enumerate(values) if _qty(v.get("selectedQuantity")) > 0}
            if not multi and not selected and values:
                free = [i for i, v in enumerate(values) if not _qty(v.get("price"))]
                selected = {free[0] if free else 0}
            self.groups.append({"multi": multi, "values": values, "default": set(selected), "selected": set(selected), "touched": False})
        self.notes: List[str] = []

    def apply_no(self, name: str) -> bool:
        for group in self.groups:
            if not group["multi"]:
                continue
            idx = _find(group["values"], name)
            if idx is not None:
                group["selected"].discard(idx)
                group["touched"] = True
                self.notes.append("去" + str(group["values"][idx].get("name")))
                return True
        # 单选组里的「去冰」「不加…」
        for prefix in ("去", "不加", "不要"):
            for group in self.groups:
                if group["multi"]:
                    continue
                idx = _find(group["values"], prefix + name)
                if idx is not None:
                    group["selected"] = {idx}
                    group["touched"] = True
                    self.notes.append(str(group["values"][idx].get("name")))
                    return True
        return False

    def apply_with(self, name: str) -> bool:
        for group in self.groups:
            idx = _find(group["values"], name)
            if idx is None:
                continue
            label = str(group["values"][idx].get("name"))
            if group["multi"]:
                group["selected"].add(idx)
                if idx not in group["default"]:
                    self.notes.append(label if label[:1] in "加多少去" else "加" + label)
            else:
                group["selected"] = {idx}
                if idx not in group["default"]:
                    self.notes.append(label)
            group["touched"] = True
            return True
        return False

    def values(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for group in self.groups:
            if not group["touched"] or group["selected"] == group["default"]:
                continue
            for i, value in enumerate(group["values"]):
                if group["multi"]:
                    if i in group["selected"]:
                        out.append({"code": str(value.get("code")), "key": str(value.get("selectedKey")), "quantity": 1})
                    else:
                        out.append({"code": str(value.get("code")), "key": str(value.get("unselectedKey")), "quantity": 0})
                elif i in group["selected"]:
                    out.append({"code": str(value.get("code")), "key": str(value.get("selectedKey")), "quantity": 1})
        return out

    def extra_price(self) -> int:
        extra = 0
        for group in self.groups:
            for i in group["selected"] - group["default"]:
                extra += _qty(group["values"][i].get("price"))  # 特制价格单位是「分」
        return extra


def build_line(
    *,
    line_id: str,
    code: str,
    menu_item: Optional[Dict[str, Any]],
    detail: Dict[str, Any],
    qty: int = 1,
    no: Sequence[str] = (),
    with_: Sequence[str] = (),
    choose: Sequence[str] = (),
) -> Tuple[Line, List[str]]:
    """返回 (购物车行, 没能生效的定制说明)。"""
    detail = detail or {}
    menu_item = menu_item or {}
    unmatched: List[str] = []
    name = str(detail.get("name") or menu_item.get("name") or code)
    unit = int(menu_item.get("price") or 0)

    rounds: List[Dict[str, Any]] = []
    raw_rounds = [r for r in detail.get("rounds") or [] if isinstance(r, dict)]
    for r in raw_rounds:
        choices = [c for c in r.get("choices") or [] if isinstance(c, dict)]
        picked = [(c, _qty(c.get("quantity"))) for c in choices if _qty(c.get("quantity")) > 0]
        if not picked and choices:
            fallback = next((c for c in choices if _qty(c.get("isDefault")) == 1), choices[0])
            picked = [(fallback, max(1, _qty(r.get("quantity"), 1)))]
        rounds.append({"round": str(r.get("id")), "name": str(r.get("name") or ""), "quantity": max(1, _qty(r.get("quantity"), 1)), "choices": choices, "picked": picked})

    for want in choose:
        done = False
        for r in rounds:
            idx = _find(r["choices"], want)
            if idx is not None:
                r["picked"] = [(r["choices"][idx], r["quantity"])]
                done = True
                break
        if not done:
            unmatched.append(f"换成「{want}」")

    out_rounds: List[Dict[str, Any]] = []
    for r in rounds:
        items = []
        for choice, count in r["picked"]:
            unit += parse_diff_price(choice.get("diffPrice")) * count
            mods = _Mods(choice.get("modification"))
            items.append({"code": str(choice.get("code")), "name": str(choice.get("name") or ""), "qty": count, "mods_state": mods})
        out_rounds.append({"round": r["round"], "name": r["name"], "items": items})

    single_mods = _Mods(detail.get("modification")) if not out_rounds else None
    notes: List[str] = []
    for want in no:
        targets = [single_mods] if single_mods else [i["mods_state"] for r in out_rounds for i in r["items"]]
        if not any([m.apply_no(want) for m in targets]):
            unmatched.append(f"去「{want}」")
    for want in with_:
        targets = [single_mods] if single_mods else [i["mods_state"] for r in out_rounds for i in r["items"]]
        if not any(m.apply_with(want) for m in targets):
            unmatched.append(f"加「{want}」")

    rounds_final = []
    for r in out_rounds:
        items = []
        for item in r["items"]:
            state = item.pop("mods_state")
            unit += state.extra_price()
            notes.extend(state.notes)
            entry = {"code": item["code"], "name": item["name"], "qty": item["qty"]}
            values = state.values()
            if values:
                entry["mods"] = values
            items.append(entry)
        rounds_final.append({"round": r["round"], "name": r["name"], "items": items})

    line: Line = {
        "id": line_id,
        "code": str(code),
        "name": name,
        "qty": max(1, int(qty)),
        "kind": "combo" if rounds_final else "single",
        "unit": unit,
        "rounds": rounds_final,
        "mods": [],
        "notes": [],
        "custom": {"no": list(no), "with": list(with_), "choose": list(choose)},
        "coupon": None,
        "origin": None,
    }
    if single_mods is not None:
        unit_extra = single_mods.extra_price()
        line["unit"] = unit + unit_extra
        line["mods"] = single_mods.values()
        notes.extend(single_mods.notes)
    line["notes"] = notes
    offer = menu_item.get("offer")
    if offer:
        # 条件优惠（如「¥37.00 需随单购麦金卡」）：单价已按原价，这里只留说明；旧状态里没有这个键，读取一律 .get
        line["offerNote"] = offer["note"]
    return line, unmatched


def line_item(line: Line) -> Dict[str, Any]:
    """购物车行 → calculate-price / create-order 的 items[] 元素。套餐一律传完整 roundList。"""
    item: Dict[str, Any] = {"productCode": str(line["code"]), "quantity": int(line["qty"])}
    if line.get("rounds"):
        round_list = []
        for r in line["rounds"]:
            combo_items = []
            for c in r["items"]:
                entry: Dict[str, Any] = {"code": str(c["code"]), "quantity": int(c["qty"])}
                if c.get("mods"):
                    entry["modification"] = {"values": copy.deepcopy(c["mods"])}
                combo_items.append(entry)
            round_list.append({"round": str(r["round"]), "comboItemList": combo_items})
        item["roundList"] = round_list
    if line.get("mods"):
        item["modification"] = {"values": copy.deepcopy(line["mods"])}
    coupon = line.get("coupon")
    if coupon:
        item["couponId"] = coupon.get("couponId")
        item["couponCode"] = coupon.get("couponCode")
    return item


def items_of(lines: Sequence[Line]) -> List[Dict[str, Any]]:
    return [line_item(line) for line in lines]


def cart_fingerprint(lines: Sequence[Line]) -> str:
    """购物车指纹：算价 items + 每行用的券。rebase 结束时记下来，diff 据此判断要不要再提示 rebase。"""
    payload = [items_of(lines), [(line.get("coupon") or {}).get("couponId") for line in lines]]
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def strip_coupon(line: Line) -> Line:
    """去掉券：恢复成用券前的那一行。"""
    if line.get("origin"):
        return copy.deepcopy(line["origin"])
    plain = copy.deepcopy(line)
    plain["coupon"] = None
    return plain


def same_content(a: Line, b: Line) -> bool:
    keys = ("code", "rounds", "mods", "coupon")
    return all(a.get(k) == b.get(k) for k in keys)


def summary(line: Line, *, with_parts: bool = True) -> str:
    """「巨无霸套餐 [巨无霸 · 中薯条 · 中可乐] 去酸黄瓜」"""
    text = str(line.get("name") or line.get("code"))
    if with_parts and line.get("rounds"):
        parts = []
        for r in line["rounds"]:
            for c in r["items"]:
                parts.append(c["name"] + (f"×{c['qty']}" if int(c["qty"]) > 1 else ""))
        if parts and not (len(parts) == 1 and parts[0] == text):
            text += " [" + " · ".join(parts) + "]"
    if line.get("notes"):
        text += " " + " ".join(line["notes"])
    return text


def line_price_hint(line: Line) -> str:
    return fmt(int(line.get("unit") or 0) * int(line.get("qty") or 1))
