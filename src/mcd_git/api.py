"""MCP tool 的薄封装：组参数、解包、把返回整理成内部结构，并做调用计数与缓存。

一条命令对应一个 McdApi 实例：同一次命令内相同参数只调一次；calculate-price 每条命令最多 20 次。
个人信息（收货地址全文、手机号、联系人、骑手信息）在这里就丢掉，不往上层传。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .errors import GitMcdError, McdError
from .mcp_client import parse_envelope
from .money import fen, yuan_to_fen
from .textnorm import normalize, rank_matches

MODES: Dict[str, Dict[str, Any]] = {
    "pickup": {"beType": 1, "orderType": 1, "label": "到店自取"},
    "drive": {"beType": 5, "orderType": 1, "label": "得来速"},
    "delivery": {"beType": 2, "orderType": 2, "label": "麦乐送"},
}

MAX_PRICE_CALLS = 20
MAX_CALLS_PER_COMMAND = 60

_SLOT_RE = re.compile(r"([^，,(（]+)[(（]([^)）]*)[)）]")


# ---------------------------------------------------------------- 订单状态


_STATUS_KEYWORDS = (
    ("待支付", "pending"),
    ("待付款", "pending"),
    ("取消", "cancelled"),
    ("配送中", "delivering"),
    ("配餐中", "preparing"),
    ("制作中", "preparing"),
    ("已评价", "completed"),
    ("完成", "completed"),
)
_SCHEMA_STATUS = {"1": "pending", "2": "preparing", "4": "delivering", "6": "completed", "7": "cancelled", "8": "completed", "10": "preparing"}
STATE_LABELS = {
    "pending": "待支付",
    "preparing": "配餐中",
    "delivering": "配送中",
    "completed": "已完成",
    "cancelled": "已取消",
    "unknown": "未知状态",
}


def order_state(order_status: Any, status: Any = None) -> Tuple[str, str]:
    """返回 (展示用标签, 内部状态)。实测 orderStatus 是中文（「订单已完成」），status 是 "40" 这类数字串，
    都和 schema 的 1/2/4/6/7/8/10 对不上，所以：中文关键词 → schema 数字码 → 原样显示。"""
    label = ""
    for raw in (order_status, status):
        if raw is None or str(raw).strip() == "":
            continue
        text = str(raw).strip()
        if not label and not text.isdigit():
            label = text
        for keyword, state in _STATUS_KEYWORDS:
            if keyword in text:
                return (label or text, state)
        if text in _SCHEMA_STATUS:
            state = _SCHEMA_STATUS[text]
            return (label or STATE_LABELS[state], state)
    fallback = label or (str(order_status) if order_status not in (None, "") else (str(status) if status not in (None, "") else ""))
    return (fallback or STATE_LABELS["unknown"], "unknown")


# ---------------------------------------------------------------- 归一化


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_reservation(options: Any) -> List[Dict[str, Any]]:
    """reservationOptionText「午餐(10:44至14:15)，宵夜(00:00至04:45,22:14至23:59)」→ 结构化时段。"""
    days = []
    for option in options or []:
        if not isinstance(option, dict):
            continue
        slots = []
        for name, body in _SLOT_RE.findall(str(option.get("reservationOptionText") or "")):
            ranges = []
            for part in body.split(","):
                if "至" in part:
                    start, end = part.split("至", 1)
                    ranges.append([start.strip(), end.strip()])
            slots.append({"name": name.strip("，, "), "ranges": ranges})
        days.append({"date": option.get("date"), "today": bool(option.get("today")), "slots": slots})
    return days


def normalize_store(raw: Dict[str, Any]) -> Dict[str, Any]:
    start, end = raw.get("businessStartTime"), raw.get("businessEndTime")
    status = raw.get("businessStatus")
    return {
        "storeCode": str(raw.get("storeCode") or ""),
        "storeName": str(raw.get("storeName") or ""),
        "address": str(raw.get("address") or ""),
        "distance": raw.get("distance"),
        "open": status if isinstance(status, bool) else None,
        "hours": f"{start}-{end}" if start and end else None,
        "beCode": str(raw.get("beCode")) if raw.get("beCode") else None,
        "reservation": parse_reservation(raw.get("reservationTimeOptions")),
    }


def mask_address(raw: Dict[str, Any]) -> Dict[str, Any]:
    """收货地址只留 addressId（本地用）和打码后的提示：地址前 6 个字 + addressId 末 4 位。"""
    address_id = str(raw.get("addressId") or "")
    full = str(raw.get("fullAddress") or raw.get("address") or "")
    head = full[:6] + ("…" if len(full) > 6 else "")
    return {"addressId": address_id, "hint": f"{head}（地址尾号 {address_id[-4:]}）" if address_id else head}


class Menu:
    """query-meals 的 data：meals 是 code → 详情的 map，categories 只有 code 和 tags。价格是「元」字符串。"""

    def __init__(self, data: Dict[str, Any]) -> None:
        data = data or {}
        self.items: Dict[str, Dict[str, Any]] = {}
        for code, meal in (data.get("meals") or {}).items():
            if not isinstance(meal, dict):
                continue
            self.items[str(code)] = {
                "code": str(code),
                "name": str(meal.get("name") or "").replace("\n", ""),
                "price": yuan_to_fen(meal.get("currentPrice")),
                "original": yuan_to_fen(meal.get("originalPrice")),
                "discountType": meal.get("discountType"),
                "tags": [],
                "categories": [],
            }
        for category in data.get("categories") or []:
            cname = str(category.get("name") or "").replace("\n", "")
            for ref in category.get("meals") or []:
                item = self.items.get(str(ref.get("code")))
                if not item:
                    continue
                for tag in ref.get("tags") or []:
                    if tag not in item["tags"]:
                        item["tags"].append(tag)
                if cname and cname not in item["categories"]:
                    item["categories"].append(cname)
        frequent = data.get("frequent")
        self.frequent = str(frequent.get("code")) if isinstance(frequent, dict) and frequent.get("code") else None

    def get(self, code: str) -> Optional[Dict[str, Any]]:
        return self.items.get(str(code))

    def is_combo(self, code: str) -> bool:
        item = self.items.get(str(code)) or {}
        return "套餐" in item.get("tags", []) or "套餐" in item.get("name", "")

    def search(self, query: str) -> Tuple[List[str], List[str]]:
        query = query.strip()
        if query in self.items:
            return [query], []
        return rank_matches(query, [(code, item["name"]) for code, item in self.items.items()])

    def by_name(self, name: str) -> Optional[str]:
        target = normalize(name)
        for code, item in self.items.items():
            if normalize(item["name"]) == target:
                return code
        return None

    def names(self) -> List[str]:
        return [item["name"] for item in self.items.values()]


def normalize_price(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data or {}
    products = []
    for p in data.get("productList") or []:
        name = str(p.get("productName") or "")
        if "打包" in name and "费" in name:
            continue  # 外送的「打包服务费」伪商品行，金额已经在 packingPrice 里
        products.append(
            {
                "code": str(p.get("productCode") or ""),
                "name": name,
                "qty": _int(p.get("quantity"), 1),
                "subtotal": fen(p.get("subtotal")),
                "original": fen(p.get("originalSubtotal")),
            }
        )
    enjoyed = data.get("enjoyed") if isinstance(data.get("enjoyed"), dict) else None
    return {
        "price": fen(data.get("price")),
        "originalPrice": fen(data.get("originalPrice")),
        "discount": fen(data.get("discount")) or 0,
        "productPrice": fen(data.get("productPrice")),
        "deliveryPrice": fen(data.get("deliveryPrice")),
        "deliveryOriginalPrice": fen(data.get("deliveryOriginalPrice")),
        "packingPrice": fen(data.get("packingPrice")),
        "products": products,
        "takeWays": [
            {"code": str(t.get("code")), "title": t.get("title") or "", "subtitle": t.get("subtitle") or ""}
            for t in data.get("takeWayList") or []
            if isinstance(t, dict) and t.get("code")
        ],
        "enjoyed": {"promotionName": enjoyed.get("promotionName")} if enjoyed and enjoyed.get("promotionName") else None,
    }


def normalize_coupon(raw: Dict[str, Any]) -> Dict[str, Any]:
    trade = str(raw.get("tradeDateTime") or "")
    start, end = (trade[:19], trade[20:39]) if len(trade) >= 39 else (None, None)
    return {
        "title": str(raw.get("title") or ""),
        "couponId": raw.get("couponId"),
        "couponCode": raw.get("couponCode"),
        "start": start,
        "end": end,
        "products": [
            {"code": str(p.get("productCode") or ""), "name": str(p.get("productName") or "")}
            for p in raw.get("products") or []
            if isinstance(p, dict)
        ],
    }


def normalize_history(raw: Dict[str, Any]) -> Dict[str, Any]:
    """order-list 的一单。orderType/beType 是字符串，子项字段叫 name。"""
    label, state = order_state(raw.get("orderStatus"), raw.get("status"))
    products = []
    for p in raw.get("orderProductList") or []:
        products.append(
            {
                "code": str(p.get("productCode") or ""),
                "name": str(p.get("productName") or ""),
                "qty": _int(p.get("quantity"), 1) or 1,
                "items": [
                    {
                        "code": str(c.get("productCode") or ""),
                        "name": str(c.get("name") or c.get("productName") or ""),
                        "qty": _int(c.get("quantity"), 1) or 1,
                    }
                    for c in p.get("comboItemList") or []
                ],
            }
        )
    return {
        "orderId": str(raw.get("orderId") or ""),
        "storeCode": str(raw.get("storeCode") or ""),
        "storeName": str(raw.get("storeName") or ""),
        "createTime": raw.get("createTime"),
        "orderType": _int(raw.get("orderType")),
        "beType": _int(raw.get("beType")),
        "statusLabel": label,
        "state": state,
        "total": yuan_to_fen(raw.get("realTotalAmount")),
        "products": products,
    }


def normalize_order(raw: Dict[str, Any]) -> Dict[str, Any]:
    """query-order 的 data / create-order 的 orderDetail。金额是「元」字符串，可选字段常常直接缺失。"""
    raw = raw or {}
    label, state = order_state(raw.get("orderStatus"), raw.get("status"))
    delivery = raw.get("deliveryInfo") if isinstance(raw.get("deliveryInfo"), dict) else None
    return {
        "orderId": str(raw.get("orderId") or ""),
        "statusLabel": label,
        "state": state,
        "storeName": str(raw.get("storeName") or ""),
        "createTime": raw.get("createTime"),
        "expirePayTime": raw.get("expirePayTime"),
        "products": [
            {
                "name": str(p.get("productName") or ""),
                "qty": _int(p.get("quantity"), 1) or 1,
                "price": yuan_to_fen(p.get("price")),
                "items": [
                    {"name": str(c.get("itemName") or c.get("name") or ""), "qty": _int(c.get("itemQuantity") or c.get("quantity"), 1)}
                    for c in p.get("comboItemList") or []
                ],
            }
            for p in raw.get("orderProductList") or []
        ],
        "productPrice": yuan_to_fen(raw.get("productPrice")),
        "deliveryPrice": yuan_to_fen(raw.get("realDeliveryPrice") if raw.get("realDeliveryPrice") is not None else raw.get("deliveryPrice")),
        "packingPrice": yuan_to_fen(raw.get("realPackingFeeTotalPrice")),
        "total": yuan_to_fen(raw.get("totalAmount")),
        "discount": yuan_to_fen(raw.get("totalDiscountAmount")),
        "realTotal": yuan_to_fen(raw.get("realTotalAmount")),
        "coupons": [
            {"name": str(c.get("couponName") or ""), "amount": yuan_to_fen(c.get("discountAmount"))}
            for c in raw.get("couponList") or []
            if isinstance(c, dict)
        ],
        "takeWay": raw.get("takeWay"),
        "pickupCode": raw.get("pickupCode") or None,
        "lockerCode": raw.get("lockerCode") or None,
        "lockerQrCode": raw.get("lockerQrCode") or None,
        "needTableware": raw.get("needTableware"),
        "remark": raw.get("remark") or "",
        # deliveryInfo 里还有地址、手机号、昵称、骑手手机号，一律不往上传
        "delivery": {"type": delivery.get("deliveryType"), "expect": delivery.get("expectDeliveryTime")} if delivery else None,
    }


def scope_args(remote: Dict[str, Any], reservation: Optional[str] = None) -> Dict[str, Any]:
    """storeCode / orderType / beType，得来速和外送再带 beCode（到店自取不能带，传了会报错）。"""
    args: Dict[str, Any] = {
        "storeCode": remote["storeCode"],
        "orderType": int(remote["orderType"]),
        "beType": int(remote["beType"]),
    }
    if int(remote["beType"]) != 1 and remote.get("beCode"):
        args["beCode"] = remote["beCode"]
    if reservation:
        args["reservationDate"] = reservation
    return args


# ---------------------------------------------------------------- API


class McdApi:
    def __init__(self, client: Any, *, max_price_calls: int = MAX_PRICE_CALLS, max_calls: int = MAX_CALLS_PER_COMMAND) -> None:
        self.client = client
        self.max_price_calls = max_price_calls
        self.max_calls = max_calls
        self.calls: Counter = Counter()
        self.server_time: Optional[str] = None
        self._cache: Dict[Tuple[str, str], Any] = {}

    def _call(self, tool: str, args: Optional[Dict[str, Any]] = None, *, cache: bool = True, approval: Any = None) -> Any:
        args = args or {}
        key = (tool, json.dumps(args, sort_keys=True, ensure_ascii=False))
        if cache and key in self._cache:
            return self._cache[key]
        if tool == "calculate-price" and self.calls[tool] >= self.max_price_calls:
            raise GitMcdError(f"这条命令的 calculate-price 已经调了 {self.max_price_calls} 次，到上限了", hint="mcd-git 不做批量算价；精简一下购物车再试")
        if sum(self.calls.values()) >= self.max_calls:
            raise GitMcdError(f"这条命令已经调了 {self.max_calls} 次 MCP，到上限了")
        self.calls[tool] += 1
        result = self.client.call_tool(tool, args, allow_write=approval is not None)
        env = parse_envelope(result)
        if env.get("datetime"):
            self.server_time = str(env["datetime"])
        data = env.get("data")
        if cache:
            self._cache[key] = data
        return data

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    # -- 门店

    def nearby_stores(self, be_type: int, *, city: Optional[str] = None, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        args: Dict[str, Any] = {"beType": be_type, "searchType": 1}
        if city or keyword:
            args["searchType"] = 2
            if city:
                args["city"] = city
            if keyword:
                args["keyword"] = keyword
        try:
            data = self._call("query-nearby-stores", args)
        except McdError as err:
            if str(err.code) == "600014":  # 搜不到时不是空数组，而是这个错误码
                return []
            raise
        return [normalize_store(s) for s in data or [] if isinstance(s, dict)]

    def addresses(self) -> List[Dict[str, Any]]:
        data = self._call("delivery-query-addresses") or {}
        rows = data.get("addresses") if isinstance(data, dict) else data
        return [mask_address(a) for a in rows or [] if isinstance(a, dict)]

    def delivery_stores(self, address_id: str, be_type: int = 2) -> List[Dict[str, Any]]:
        data = self._call("delivery-query-stores", {"addressId": address_id, "beType": be_type})
        return [normalize_store(s) for s in data or [] if isinstance(s, dict)]

    # -- 菜单与算价

    def menu(self, remote: Dict[str, Any], reservation: Optional[str] = None) -> Menu:
        return Menu(self._call("query-meals", scope_args(remote, reservation)) or {})

    def meal_detail(self, remote: Dict[str, Any], code: str, reservation: Optional[str] = None) -> Dict[str, Any]:
        args = scope_args(remote, reservation)
        args["code"] = str(code)
        return self._call("query-meal-detail", args) or {}

    def price(
        self,
        remote: Dict[str, Any],
        items: List[Dict[str, Any]],
        *,
        reservation: Optional[str] = None,
        need_tableware: Optional[bool] = None,
    ) -> Dict[str, Any]:
        args = scope_args(remote, reservation)
        args["items"] = items
        if need_tableware is not None:
            args["needTableware"] = need_tableware
        return normalize_price(self._call("calculate-price", args) or {})

    def store_coupons(self, remote: Dict[str, Any], reservation: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._call("query-store-coupons", scope_args(remote, reservation))
        rows = data.get("coupons") if isinstance(data, dict) else data
        return [normalize_coupon(c) for c in rows or [] if isinstance(c, dict)]

    def available_coupons(self) -> List[Dict[str, Any]]:
        data = self._call("available-coupons")
        rows = data.get("coupons") if isinstance(data, dict) else data
        return [
            {"name": str(c.get("couponName") or ""), "status": c.get("couponStatus"), "label": c.get("label") or ""}
            for c in rows or []
            if isinstance(c, dict)
        ]

    def campaign_calendar(self, date: Optional[str] = None) -> Dict[str, Any]:
        return self._call("campaign-calendar", {"specifiedDate": date} if date else {}) or {}

    # -- 订单

    def order_list(self) -> List[Dict[str, Any]]:
        data = self._call("order-list") or {}
        rows = data.get("list") if isinstance(data, dict) else data
        return [normalize_history(o) for o in rows or [] if isinstance(o, dict)]

    def query_order(self, order_id: str) -> Dict[str, Any]:
        return normalize_order(self._call("query-order", {"orderId": str(order_id)}, cache=False) or {})

    # -- 写操作：只接受确认闸签发的 Approval（见 gate.py）

    def create_order(self, approval: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        approval.check("create-order", args)
        data = self._call("create-order", args, cache=False, approval=approval) or {}
        detail = data.get("orderDetail") if isinstance(data.get("orderDetail"), dict) else {}
        order = normalize_order(detail) if detail else None
        order_id = str(data.get("orderId") or (detail or {}).get("orderId") or "")
        if order is not None and not order["orderId"]:
            order["orderId"] = order_id
        return {"orderId": order_id, "payId": data.get("payId"), "payH5Url": data.get("payH5Url"), "order": order}

    def cancel_order(self, approval: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        approval.check("cancel-order", args)
        data = self._call("cancel-order", args, cache=False, approval=approval) or {}
        return {"cancelResult": data.get("cancelResult"), "orderId": str(data.get("orderId") or args.get("orderId"))}
