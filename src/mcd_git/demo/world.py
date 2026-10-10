"""--demo 用的合成世界。

门店、地址、价格、订单号、券全是编的，只模仿真实 MCP 返回的结构（字段名、单位、可选字段缺失等）。
不含任何真实门店、账户、订单或价格快照，链接一律指向 example.com。
"""
from __future__ import annotations

import copy
import hashlib
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

from ..api import order_state
from ..money import yuan_to_fen

DEMO_LINK = "https://example.com/mcd-git-demo"

# ---------------------------------------------------------------- 门店

_RESERVATION_TODAY = "午餐(10:44至14:15)，下午茶(14:44至16:45)，晚餐(17:14至20:45)"
_RESERVATION_TOMORROW = "早餐(06:44至10:15)，午餐(10:44至14:15)"


def _store(code: str, name: str, address: str, distance: int, start: str, end: str) -> Dict[str, Any]:
    return {
        "storeCode": code,
        "storeName": name,
        "address": address,
        "distance": distance,
        "businessStatus": True,
        "businessStartTime": start,
        "businessEndTime": end,
        "longitude": 0.0,
        "latitude": 0.0,
        "reservation": True,
        "reservationTimeOptions": [
            {"date": "2026-10-24", "today": True, "reservationOptionText": _RESERVATION_TODAY},
            {"date": "2026-10-25", "today": False, "reservationOptionText": _RESERVATION_TOMORROW},
        ],
    }


PICKUP_STORES: List[Dict[str, Any]] = [
    _store("1000001", "演示·代码港店", "示例市码农路 1024 号", 150, "06:30", "23:00"),
    _store("1000002", "演示·提交大道店", "示例市提交大道 200 号", 420, "07:00", "22:00"),
    _store("1000003", "演示·合并路店", "示例市合并路 3 号", 880, "07:00", "22:00"),
]

DRIVE_STORES: List[Dict[str, Any]] = [
    dict(_store("1000004", "演示·流水线得来速", "示例市流水线高架出口", 3200, "06:00", "23:59"), beCode="100000441"),
]

# 收藏门店：实测没有营业字段，distance 恒为 0
FAVORITE_STORES: List[Dict[str, Any]] = [
    {k: v for k, v in dict(s, distance=0).items() if not k.startswith("business")} for s in PICKUP_STORES[:2]
]

ADDRESSES: List[Dict[str, Any]] = [
    {"addressId": "8000000000000000000000000001", "contactName": "示例", "phone": "138****0000", "fullAddress": "示例市示例区码农路 1024 号 1 栋"},
]

DELIVERY_STORES: List[Dict[str, Any]] = [
    {k: v for k, v in dict(PICKUP_STORES[0], beCode="100000102").items() if k not in ("address", "distance", "longitude", "latitude")},
]

TAKE_WAYS: Dict[str, List[Dict[str, str]]] = {
    "1000001": [
        {"code": "eat-in", "title": "堂食", "subtitle": "店内用餐"},
        {"code": "locker-out", "title": "外带", "subtitle": "店外取餐柜"},
    ],
    "1000002": [
        {"code": "eat-in", "title": "堂食", "subtitle": "店内用餐"},
        {"code": "take-in-store", "title": "外带", "subtitle": "店内自提"},
    ],
    "1000003": [
        {"code": "eat-in", "title": "堂食", "subtitle": "店内用餐"},
        {"code": "take-in-store", "title": "外带", "subtitle": "店内自提"},
    ],
    "1000004": [{"code": "take-dt-quick", "title": "得来速", "subtitle": "车道自提"}],
}

STORE_HOURS = {s["storeCode"]: (s["businessStartTime"], s["businessEndTime"]) for s in PICKUP_STORES + DRIVE_STORES}

# ---------------------------------------------------------------- 特制

_BURGER_MODS = {
    "items": [
        {
            "minValues": 0,
            "maxValues": 3,
            "values": [
                {"code": "M101", "name": "酸黄瓜", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 1, "selectedKey": "0-1", "unselectedKey": "0-0"},
                {"code": "M102", "name": "洋葱", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 1, "selectedKey": "0-1", "unselectedKey": "0-0"},
                {"code": "M103", "name": "生菜", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 1, "selectedKey": "0-1", "unselectedKey": "0-0"},
            ],
        }
    ]
}

_CHICKEN_MODS = {
    "items": [
        {
            "minValues": 0,
            "maxValues": 2,
            "values": [
                {"code": "M103", "name": "生菜", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 1, "selectedKey": "0-1", "unselectedKey": "0-0"},
                {"code": "M104", "name": "沙拉酱", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 1, "selectedKey": "0-1", "unselectedKey": "0-0"},
            ],
        }
    ]
}

# 冰量：单选档位，code 相同，靠 selectedKey 区分，没有 unselectedKey
_ICE_MODS = {
    "items": [
        {
            "minValues": 1,
            "maxValues": 1,
            "values": [
                {"code": "M301", "name": "标准冰", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 1, "selectedKey": "0-1"},
                {"code": "M301", "name": "少冰", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 0, "selectedKey": "1-1"},
                {"code": "M301", "name": "去冰", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 0, "selectedKey": "0-0"},
            ],
        }
    ]
}

# 付费加料：单位是「分」
_SHOT_MODS = {
    "items": [
        {
            "minValues": 1,
            "maxValues": 1,
            "values": [
                {"code": "M401", "name": "不加浓缩", "price": 0, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 1, "selectedKey": "0-0"},
                {"code": "M401", "name": "加一份浓缩", "price": 400, "minQuantity": 0, "maxQuantity": 1, "selectedQuantity": 0, "selectedKey": "0-1"},
            ],
        }
    ]
}

# ---------------------------------------------------------------- 菜单（到店）

# code: (名称, 现价分, 原价分, 分类, 特制, 优惠类型)
SINGLES: Dict[str, Dict[str, Any]] = {
    "1001": {"name": "巨无霸", "price": 2400, "original": 2400, "category": "汉堡", "mods": _BURGER_MODS},
    "1002": {"name": "麦辣鸡腿堡", "price": 2300, "original": 2300, "category": "汉堡", "mods": _CHICKEN_MODS},
    "1003": {"name": "双层吉士汉堡", "price": 1900, "original": 1900, "category": "汉堡", "mods": _BURGER_MODS},
    "1004": {"name": "板烧鸡腿堡", "price": 2200, "original": 2200, "category": "汉堡", "mods": _CHICKEN_MODS},
    "2001": {"name": "中薯条", "price": 1350, "original": 1350, "category": "小食"},
    "2002": {"name": "大薯条", "price": 1550, "original": 1550, "category": "小食"},
    "2101": {"name": "麦乐鸡（5块）", "price": 1300, "original": 1300, "category": "小食"},
    "2201": {"name": "菠萝派", "price": 800, "original": 800, "category": "甜品"},
    "3001": {"name": "中可乐", "price": 800, "original": 1000, "category": "饮品", "mods": _ICE_MODS, "discountType": "促销优惠"},
    "3002": {"name": "大可乐", "price": 1150, "original": 1150, "category": "饮品", "mods": _ICE_MODS},
    "3003": {"name": "中杯柠檬茶", "price": 1100, "original": 1100, "category": "饮品", "mods": _ICE_MODS},
    "4001": {"name": "热拿铁", "price": 1600, "original": 1600, "category": "咖啡", "mods": _SHOT_MODS},
    "4002": {"name": "美式咖啡", "price": 1300, "original": 1300, "category": "咖啡", "mods": _SHOT_MODS},
}

_SIDE_ROUND = [("2001", "+ ¥0", True), ("2002", "+ ¥2", False), ("2101", "+ ¥3", False)]
_DRINK_ROUND = [("3001", "+ ¥0", True), ("3003", "+ ¥1", False), ("3002", "+ ¥1.5", False), ("4001", "+ ¥6", False)]

COMBOS: Dict[str, Dict[str, Any]] = {
    "9900001001": {"name": "巨无霸套餐", "price": 3850, "original": 3850, "main": "1001"},
    "9900001002": {"name": "麦辣鸡腿堡套餐", "price": 3700, "original": 3700, "main": "1002"},
    "9900001003": {"name": "双层吉士汉堡套餐", "price": 3200, "original": 3200, "main": "1003"},
    "9900001004": {"name": "板烧鸡腿堡套餐", "price": 3600, "original": 3600, "main": "1004"},
}

CATEGORY_ORDER = ["超值\n套餐", "汉堡", "小食", "甜品", "饮品", "咖啡"]


def _delivery_code(code: str) -> str:
    return "991" + code[3:] if code.startswith("990") else "90" + code


def _delivery_price(amount: int) -> int:
    return int(round(amount * 1.15 / 50.0)) * 50


def _catalog(channel: str) -> Dict[str, Dict[str, Any]]:
    """channel = pickup / delivery。外送是另一套 code，价格也更高（实测如此）。"""
    tr = _delivery_code if channel == "delivery" else (lambda c: c)
    pr = _delivery_price if channel == "delivery" else (lambda p: p)
    catalog: Dict[str, Dict[str, Any]] = {}
    for code, item in SINGLES.items():
        entry = copy.deepcopy(item)
        entry.update(code=tr(code), price=pr(item["price"]), original=pr(item["original"]), kind="single")
        if channel == "delivery":
            entry.pop("discountType", None)
            entry["price"] = entry["original"]
        catalog[tr(code)] = entry
    for code, combo in COMBOS.items():
        rounds = [
            {"id": 1, "name": "主食", "choices": [(tr(combo["main"]), "+ ¥0", True)]},
            {"id": 2, "name": "小食", "choices": [(tr(c), d, x) for c, d, x in _SIDE_ROUND]},
            {"id": 3, "name": "饮料", "choices": [(tr(c), d, x) for c, d, x in _DRINK_ROUND]},
        ]
        catalog[tr(code)] = {
            "code": tr(code),
            "name": combo["name"],
            "price": pr(combo["price"]),
            "original": pr(combo["original"]),
            "category": "超值\n套餐",
            "kind": "combo",
            "rounds": rounds,
        }
    return catalog


CATALOG = {"pickup": _catalog("pickup"), "delivery": _catalog("delivery")}


def meals_payload(channel: str) -> Dict[str, Any]:
    catalog = CATALOG[channel]
    categories = []
    for cname in CATEGORY_ORDER:
        refs = [{"code": code, "tags": ["套餐" if item["kind"] == "combo" else "单品"]} for code, item in catalog.items() if item["category"] == cname]
        if refs:
            categories.append({"name": cname, "meals": refs})
    meals = {}
    for code, item in catalog.items():
        entry = {
            "name": item["name"],
            "image": "",
            "currentPrice": _yuan(item["price"]),
            "originalPrice": _yuan(item["original"]),
            "canWithOrder": False,
        }
        if item.get("discountType"):
            entry["discountType"] = item["discountType"]
        meals[code] = entry
    frequent_code = _delivery_code("9900001001") if channel == "delivery" else "9900001001"
    return {"categories": categories, "meals": meals, "frequent": {"code": frequent_code, "tags": ["我的常点"]}}


def _yuan(amount: int) -> str:
    text = f"{amount / 100:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def detail_payload(channel: str, code: str) -> Dict[str, Any]:
    catalog = CATALOG[channel]
    item = catalog[code]
    if item["kind"] == "single":
        mods = copy.deepcopy(item.get("mods"))
        return {"code": code, "name": item["name"], "image": "", "supportModify": bool(mods), "modification": mods, "rounds": []}
    rounds = []
    for r in item["rounds"]:
        choices = []
        for ccode, diff, default in r["choices"]:
            single = catalog[ccode]
            mods = copy.deepcopy(single.get("mods"))
            choice = {
                "code": ccode,
                "name": single["name"],
                "quantity": 1 if default else 0,
                "maxQuantity": -1,
                "isDefault": 1 if default else 0,
                "diffPrice": diff,
                "supportModify": bool(mods),
            }
            if mods:
                choice["modification"] = mods
            choices.append(choice)
        rounds.append({"id": r["id"], "name": r["name"], "quantity": 1, "minQuantity": 1, "maxQuantity": 1, "choices": choices})
    return {"code": code, "name": item["name"], "image": "", "supportModify": False, "modification": None, "rounds": rounds}


# ---------------------------------------------------------------- 券

_COUPON_COMBO = {
    "title": "示例·巨无霸套餐立减 5 元",
    "couponId": "c0ffee00000000000000000000000001",
    "couponCode": "DEMO0001",
    "tradeDateTime": "2026-10-01 00:00:00-2026-10-31 23:59:59",
    "products": [{"productCode": "9900001001", "productName": "巨无霸套餐"}],
    "promotionId": "900001",
    "rule": ["minus", 500],
}
_COUPON_FRIES = {
    "title": "示例·中薯条 6 元券",
    "couponId": "c0ffee00000000000000000000000002",
    "couponCode": "DEMO0002",
    "tradeDateTime": "2026-10-20 10:30:00-2026-10-24 23:59:59",
    "products": [{"productCode": "2001", "productName": "中薯条"}],
    "promotionId": "900002",
    "rule": ["fixed", 600],
}
_COUPON_LATTE = {
    "title": "示例·热拿铁 9.9 元券",
    "couponId": "c0ffee00000000000000000000000003",
    "couponCode": "DEMO0003",
    "tradeDateTime": "2026-10-01 00:00:00-2026-11-30 23:59:59",
    "products": [{"productCode": "4001", "productName": "热拿铁"}],
    "promotionId": "900003",
    "rule": ["fixed", 990],
}


def _delivery_coupon(coupon: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(coupon)
    out["products"] = [dict(p, productCode=_delivery_code(p["productCode"])) for p in out["products"]]
    return out


STORE_COUPONS: Dict[str, List[Dict[str, Any]]] = {
    "1000001:1": [_COUPON_COMBO, _COUPON_FRIES, _COUPON_LATTE],
    "1000002:1": [_COUPON_FRIES, _COUPON_LATTE],
    "1000003:1": [],
    "1000004:1": [_COUPON_COMBO],
    "1000001:2": [_delivery_coupon(_COUPON_COMBO)],
}

AVAILABLE_COUPONS = [
    {"couponName": "示例·新品尝鲜券", "couponImage": "", "couponStatus": "CAN_GET", "label": "可领取"},
    {"couponName": "示例·咖啡第二杯半价券", "couponImage": "", "couponStatus": "CAN_GET", "label": "可领取"},
    {"couponName": "示例·小食立减券", "couponImage": "", "couponStatus": "CAN_GET", "label": "可领取"},
]

CALENDAR = {
    "currentTime": "2026-10-24 11:50:00",
    "dailyList": [
        {
            "date": "2026-10-24",
            "dateText": "2026年10月24日 今日",
            "today": True,
            "events": [
                {
                    "activityCode": "demo1024",
                    "activityStage": 2,
                    "eventType": 4,
                    "showType": 1,
                    "articleDto": {"title": "示例·程序员节主题活动", "content": "（演示数据）", "imgList": [], "highlights": "演示用的活动", "buttonText": "去看看"},
                    "button": {"status": 0, "text": "", "color": "", "buttonStyle": 0},
                }
            ],
        },
        {
            "date": "2026-10-25",
            "dateText": "2026年10月25日",
            "today": False,
            "events": [
                {
                    "activityCode": "demo1025",
                    "activityStage": 1,
                    "eventType": 4,
                    "showType": 1,
                    "articleDto": {"title": "示例·周末早餐活动", "content": "（演示数据）", "imgList": [], "highlights": "演示用的活动", "buttonText": "去看看"},
                    "button": {"status": 0, "text": "", "color": "", "buttonStyle": 0},
                }
            ],
        },
    ],
    "subscribedEvents": [],
}

# ---------------------------------------------------------------- 历史订单（order-list / query-order 共用）


def _combo_items(*codes_names: Any) -> List[Dict[str, Any]]:
    return [{"productCode": c, "name": n, "quantity": 1} for c, n in codes_names]


HISTORY: List[Dict[str, Any]] = [
    {
        "orderId": "1024202610231231000000000004",
        "orderType": "1",
        "beType": "1",
        "beCode": "",
        "storeCode": "1000001",
        "storeName": "演示·代码港店",
        "storeAddress": "示例市码农路 1024 号",
        "createTime": "2026-10-23 12:31:00",
        "orderStatus": "订单已完成",
        "status": "40",
        "realTotalAmount": "33.5",
        "takeWay": "堂食",
        "pickupCode": "10230",
        "orderProductList": [
            {"productCode": "9900001001", "productName": "巨无霸套餐", "quantity": 1, "price": "38.5",
             "comboItemList": _combo_items(("1001", "巨无霸"), ("2001", "中薯条"), ("3001", "中可乐"))},
        ],
        "couponList": [{"couponName": "示例·巨无霸套餐立减 5 元", "discountAmount": "5"}],
    },
    {
        "orderId": "1024202610230812000000000003",
        "orderType": "1",
        "beType": "1",
        "beCode": "",
        "storeCode": "1000002",
        "storeName": "演示·提交大道店",
        "storeAddress": "示例市提交大道 200 号",
        "createTime": "2026-10-23 08:12:00",
        "orderStatus": "订单已完成",
        "status": "40",
        "realTotalAmount": "29",
        "takeWay": "外带",
        "pickupCode": "08120",
        "orderProductList": [
            {"productCode": "5001", "productName": "猪柳蛋麦满分", "quantity": 1, "price": "13", "comboItemList": []},
            {"productCode": "4001", "productName": "热拿铁", "quantity": 1, "price": "16", "comboItemList": []},
        ],
        "couponList": [],
    },
    {
        "orderId": "1024202610221205000000000002",
        "orderType": "1",
        "beType": "1",
        "beCode": "",
        "storeCode": "1000001",
        "storeName": "演示·代码港店",
        "storeAddress": "示例市码农路 1024 号",
        "createTime": "2026-10-22 12:05:00",
        "orderStatus": "订单已完成",
        "status": "40",
        "realTotalAmount": "40",
        "takeWay": "堂食",
        "pickupCode": "12050",
        "orderProductList": [
            {"productCode": "9900001002", "productName": "麦辣鸡腿堡套餐", "quantity": 1, "price": "40",
             "comboItemList": _combo_items(("1002", "麦辣鸡腿堡"), ("2002", "大薯条"), ("3003", "中杯柠檬茶"))},
        ],
        "couponList": [],
    },
    {
        "orderId": "1024202610211520000000000001",
        "orderType": "1",
        "beType": "1",
        "beCode": "",
        "storeCode": "1000003",
        "storeName": "演示·合并路店",
        "storeAddress": "示例市合并路 3 号",
        "createTime": "2026-10-21 15:20:00",
        "orderStatus": "订单已完成",
        "status": "40",
        "realTotalAmount": "19",
        "takeWay": "外带",
        "pickupCode": "15200",
        "orderProductList": [
            {"productCode": "3003", "productName": "中杯柠檬茶", "quantity": 1, "price": "11", "comboItemList": []},
            {"productCode": "2201", "productName": "菠萝派", "quantity": 1, "price": "8", "comboItemList": []},
        ],
        "couponList": [],
    },
    {
        "orderId": "1024202610201210000000000000",
        "orderType": "1",
        "beType": "1",
        "beCode": "",
        "storeCode": "1000001",
        "storeName": "演示·代码港店",
        "storeAddress": "示例市码农路 1024 号",
        "createTime": "2026-10-20 12:10:00",
        "orderStatus": "订单已取消",
        "status": "70",
        "realTotalAmount": "32",
        "takeWay": "堂食",
        "pickupCode": "",
        "orderProductList": [
            {"productCode": "9900001003", "productName": "双层吉士汉堡套餐", "quantity": 1, "price": "32",
             "comboItemList": _combo_items(("1003", "双层吉士汉堡"), ("2001", "中薯条"), ("3001", "中可乐"))},
        ],
        "couponList": [],
    },
]


def history_entries() -> List[Dict[str, Any]]:
    """HISTORY 那 5 单换成本机账本的样子（原价按商品价加总、优惠按券加总），demo 账本播种用。"""
    out = []
    for h in HISTORY:
        label, state = order_state(h.get("orderStatus"), h.get("status"))
        products = h.get("orderProductList") or []
        out.append(
            {
                "orderId": h["orderId"],
                "time": h["createTime"],
                "state": state,
                "status": label,
                "items": [{"name": p["productName"], "qty": int(p.get("quantity") or 1)} for p in products],
                "original": sum(yuan_to_fen(p.get("price")) or 0 for p in products),
                "discount": sum(yuan_to_fen(c.get("discountAmount")) or 0 for c in h.get("couponList") or []),
                "paid": yuan_to_fen(h.get("realTotalAmount")),
                "way": "到店自取 · " + h["takeWay"],
                "source": "demo",
            }
        )
    return out


# ---------------------------------------------------------------- 一年的合成订单（card 的格子用）

_YEAR_START = date(2025, 10, 20)  # DEMO_NOW 往前 52 周那周的周一；最后一周是 2026-10-12 那周，之后是 HISTORY
_YEAR_WEEKS = 52
_BREAK_WEEKS = ("2026-02-16", "2026-07-27")  # 春节、暑假各歇一周
_MUFFIN = ("猪柳蛋麦满分", 1300)  # 早餐单品：价格同 HISTORY 里那一单（演示菜单没有它）
_COMBO_WEIGHTS = (("9900001001", 0.40), ("9900001002", 0.25), ("9900001004", 0.20), ("9900001003", 0.15))
# (开始分钟, 时长分钟)：工作日早饭赶在上班前；周末只有零星的早餐和下午茶，时间也晚一些
_SLOT_WINDOWS = {"breakfast": (7 * 60 + 50, 60), "lunch": (11 * 60 + 40, 70), "tea": (15 * 60, 60), "dinner": (18 * 60, 90)}
_WEEKEND_WINDOWS = {"breakfast": (8 * 60 + 30, 70), "tea": (14 * 60 + 30, 90)}


def _roll(tag: str, key: str) -> float:
    """确定性的掷点 [0, 1)：sha1 跨 Python 版本稳定（random 的部分实现在 3.12 变过），贡献图快照才能逐字节一致。"""
    digest = hashlib.sha1(f"{tag}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2.0**64


def _item(name: str) -> Dict[str, Any]:
    return {"name": name, "qty": 1}


def _meal(slot: str, day: str) -> Tuple[List[Dict[str, Any]], int, int]:
    """一单吃什么：返回 (餐品, 原价, 用券优惠)，金额单位分。"""
    if slot == "breakfast":
        coffee = SINGLES["4001"] if _roll("coffee", day) < 0.6 else SINGLES["4002"]
        discount = 1600 - 990 if coffee["name"] == "热拿铁" and _roll("latte-coupon", day) < 0.3 else 0  # 热拿铁 9.9 元券
        return [_item(_MUFFIN[0]), _item(coffee["name"])], _MUFFIN[1] + coffee["price"], discount
    if slot == "tea":
        if _roll("tea", day) < 0.6:
            return [_item(SINGLES["3003"]["name"]), _item(SINGLES["2201"]["name"])], SINGLES["3003"]["price"] + SINGLES["2201"]["price"], 0
        return [_item(SINGLES["4001"]["name"])], SINGLES["4001"]["price"], 0
    roll, acc, code = _roll(f"combo-{slot}", day), 0.0, _COMBO_WEIGHTS[-1][0]
    for candidate, weight in _COMBO_WEIGHTS:
        acc += weight
        if roll < acc:
            code = candidate
            break
    combo = COMBOS[code]
    items, original = [_item(combo["name"])], combo["price"]
    discount = 500 if code == "9900001001" and _roll(f"bigmac-coupon-{slot}", day) < 0.5 else 0  # 巨无霸套餐立减 5 元
    if _roll(f"pie-{slot}", day) < 0.15:
        items.append(_item(SINGLES["2201"]["name"]))
        original += SINGLES["2201"]["price"]
    return items, original, discount


def _slot(day: str) -> str:
    roll = _roll("slot", day)
    if roll < 0.68:
        return "lunch"
    if roll < 0.80:
        return "breakfast"
    if roll < 0.88:
        return "tea"
    return "dinner"


def year_orders() -> List[Dict[str, Any]]:
    """2025-10-20 那周到 2026-10-12 那周的合成订单：每周 1～3 单（平均两单左右），以工作日午饭为主，偶尔同一天早饭加午饭；
    周末零星一顿早餐或下午茶（大约每月一两次）；不生成夜宵。返回本机账本的样子（带 orderId，播种时换成哈希）；
    价格、券、订单号全是编的。"""
    orders: List[Dict[str, Any]] = []
    seq = 100  # 订单号序号从 100 起，避开 HISTORY
    for week in range(_YEAR_WEEKS):
        monday = _YEAR_START + timedelta(weeks=week)
        if monday.isoformat() in _BREAK_WEEKS:
            continue
        roll = _roll("week", monday.isoformat())
        count = 1 if roll < 0.35 else (2 if roll < 0.80 else 3)
        weekdays = sorted((monday + timedelta(days=i) for i in range(5)), key=lambda d: _roll("day", d.isoformat()))
        meals: List[Tuple[date, str, bool]] = []  # (日期, 时段, 是不是周末)
        for day in sorted(weekdays[:count]):
            slot = _slot(day.isoformat())
            if count < 3 and slot == "lunch" and _roll("extra-breakfast", day.isoformat()) < 0.12:
                meals.append((day, "breakfast", False))  # 同一天早饭加午饭
            meals.append((day, slot, False))
        if len(meals) < 3 and _roll("weekend", monday.isoformat()) < 0.5:
            day = monday + timedelta(days=5 if _roll("weekend-day", monday.isoformat()) < 0.5 else 6)
            meals.append((day, "breakfast" if _roll("weekend-slot", day.isoformat()) < 0.5 else "tea", True))
        for day, slot, weekend in meals:  # 一周最多 3 单
            iso = day.isoformat()
            start, span = (_WEEKEND_WINDOWS if weekend else _SLOT_WINDOWS)[slot]
            minute = start + int(_roll(f"minute-{slot}", iso) * span)
            time = f"{iso} {minute // 60:02d}:{minute % 60:02d}:00"
            items, original, discount = _meal(slot, iso)
            cancelled = _roll("cancel", f"{iso} {slot}") < 0.03
            dine_in = (weekend or slot in ("lunch", "dinner")) and _roll("way", iso) < 0.7
            orders.append(
                {
                    "orderId": "1024" + iso.replace("-", "") + time[11:].replace(":", "") + f"{seq:010d}",
                    "time": time,
                    "state": "cancelled" if cancelled else "completed",
                    "status": "订单已取消" if cancelled else "订单已完成",
                    "items": items,
                    "original": original,
                    "discount": discount,
                    "paid": original - discount,
                    "way": "到店自取 · " + ("堂食" if dine_in else "外带"),
                    "source": "demo",
                }
            )
            seq += 1
    return orders
