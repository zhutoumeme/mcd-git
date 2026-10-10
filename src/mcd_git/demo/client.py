"""DemoClient：用合成世界模拟 MCP 的返回，包括失败形态（success=false、isError=false、没有 data）。

create-order / cancel-order / auto-bind-coupons 只改本地 demo 目录里的 server.json，不发任何网络请求。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..errors import GitMcdError
from ..mcp_client import WRITE_BLOCKED, write_blocked
from ..money import parse_diff_price
from . import world

SOLD_OUT = "商品已售罄或不可售，换一个商品或检查orderType/beType"
TIME_FMT = "%Y-%m-%d %H:%M:%S"


class _Fail(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _yuan(amount: int) -> str:
    text = f"{amount / 100:.2f}".rstrip("0").rstrip(".")
    return text or "0"


class DemoClient:
    def __init__(self, root: Path, *, now: datetime) -> None:
        self.root = Path(root)
        self.path = self.root / "server.json"
        self.now = now
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def __repr__(self) -> str:
        return "<DemoClient>"

    # ------------------------------------------------------------ 框架

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, *, allow_write: bool = False) -> Dict[str, Any]:
        if write_blocked(name, allow_write):  # 和真实客户端同一套拦截
            raise GitMcdError(WRITE_BLOCKED.format(name=name), fatal=True)
        args = copy.deepcopy(arguments or {})
        self.calls.append((name, args))
        trace = hashlib.md5(f"demo:{name}:{len(self.calls)}".encode("utf-8")).hexdigest()  # noqa: S324（只是演示用的 traceId）
        stamp = self.now.strftime(TIME_FMT)
        handler = getattr(self, "_t_" + name.replace("-", "_"), None)
        try:
            if handler is None:
                raise _Fail(404, f"demo 模式没有模拟 {name}")
            data = handler(args)
            env: Dict[str, Any] = {"success": True, "code": 200, "message": "请求成功", "datetime": stamp, "traceId": trace, "data": data}
        except _Fail as fail:
            env = {"success": False, "code": fail.code, "message": fail.message, "datetime": stamp, "traceId": trace}
        text = "# API Response Information\n\n（mcd-git demo：合成数据）\n\n## Original Response\n\n" + json.dumps(env, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": False, "structuredContent": env}

    def _load_db(self) -> Dict[str, Any]:
        if self.path.is_file():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"orders": [], "seq": 0}

    def _save_db(self, db: Dict[str, Any]) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".server-", dir=str(self.root))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(db, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def reset(self) -> None:
        if self.path.is_file():
            self.path.unlink()

    # ------------------------------------------------------------ 门店

    def _t_query_nearby_stores(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        be_type = int(args.get("beType") or 1)
        search = int(args.get("searchType") or 1)
        if be_type == 5:
            return copy.deepcopy(world.DRIVE_STORES)
        if search == 1:
            return copy.deepcopy(world.FAVORITE_STORES)
        keyword = str(args.get("keyword") or "")
        if not (args.get("city") or keyword) or "火星" in keyword:
            raise _Fail(600014, "没有查询到该地址，请检查配送地址是否正确")
        return copy.deepcopy(world.PICKUP_STORES)

    def _t_delivery_query_addresses(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"addresses": copy.deepcopy(world.ADDRESSES)}

    def _t_delivery_query_stores(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        if str(args.get("addressId")) not in {a["addressId"] for a in world.ADDRESSES}:
            raise _Fail(600014, "没有查询到该地址，请检查配送地址是否正确")
        return copy.deepcopy(world.DELIVERY_STORES)

    def _scope(self, args: Dict[str, Any]) -> Tuple[str, str]:
        """校验 storeCode / orderType / beType / beCode 组合，返回 (channel, storeCode)。"""
        store = str(args.get("storeCode") or "")
        order_type = int(args.get("orderType") or 0)
        be_type = int(args.get("beType") or 0)
        be_code = args.get("beCode")
        if order_type == 2:
            ok = be_type == 2 and any(s["storeCode"] == store and s["beCode"] == be_code for s in world.DELIVERY_STORES)
            channel = "delivery"
        elif be_type == 5:
            ok = any(s["storeCode"] == store and s["beCode"] == be_code for s in world.DRIVE_STORES)
            channel = "pickup"
        else:
            ok = be_type == 1 and order_type == 1 and not be_code and any(s["storeCode"] == store for s in world.PICKUP_STORES)
            channel = "pickup"
        if not ok:
            raise _Fail(60006, SOLD_OUT)
        return channel, store

    # ------------------------------------------------------------ 菜单

    def _t_query_meals(self, args: Dict[str, Any]) -> Dict[str, Any]:
        channel, _ = self._scope(args)
        return world.meals_payload(channel)

    def _t_query_meal_detail(self, args: Dict[str, Any]) -> Dict[str, Any]:
        channel, _ = self._scope(args)
        code = str(args.get("code") or "")
        if code not in world.CATALOG[channel]:
            raise _Fail(60006, SOLD_OUT)
        return world.detail_payload(channel, code)

    def _t_query_store_coupons(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        _, store = self._scope(args)
        rows = world.STORE_COUPONS.get(f"{store}:{int(args.get('orderType') or 1)}", [])
        return [{k: v for k, v in c.items() if k != "rule"} for c in copy.deepcopy(rows)]

    def _t_available_coupons(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self._load_db().get("claimed"):
            return []  # pull 领过之后就没有可领的了；--demo init 删掉 server.json 恢复
        return copy.deepcopy(world.AVAILABLE_COUPONS)

    def _t_auto_bind_coupons(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """一次领走全部可领券（形态按 outputSchema）；已经领过再调，返回全 0。"""
        db = self._load_db()
        if db.get("claimed"):
            return {"successCount": 0, "failedCount": 0, "totalCount": 0, "successCoupons": [], "failedCoupons": []}
        db["claimed"] = True
        self._save_db(db)
        claimed = [
            {"couponId": f"demo-claim-{i:04d}", "couponName": c["couponName"], "couponCode": f"DEMOPULL{i:04d}", "couponImage": ""}
            for i, c in enumerate(world.AVAILABLE_COUPONS, 1)
        ]
        return {"successCount": len(claimed), "failedCount": 0, "totalCount": len(claimed), "successCoupons": claimed, "failedCoupons": []}

    def _t_campaign_calendar(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return copy.deepcopy(world.CALENDAR)

    # ------------------------------------------------------------ 算价

    @staticmethod
    def _mods_extra(mods: Optional[Dict[str, Any]], modification: Optional[Dict[str, Any]]) -> int:
        if not mods or not modification:
            return 0
        extra = 0
        for value in modification.get("values") or []:
            if int(value.get("quantity") or 0) <= 0:
                continue
            for group in mods.get("items") or []:
                for option in group.get("values") or []:
                    if option["code"] == value.get("code") and option["selectedKey"] == value.get("key"):
                        extra += int(option.get("price") or 0)
        return extra

    def _combo_extra(self, product: Dict[str, Any], round_list: Any, catalog: Dict[str, Any]) -> int:
        if not round_list:
            return 0  # 不传 roundList 按默认搭配算价
        rounds = {str(r["id"]): r for r in product["rounds"]}
        extra = 0
        for entry in round_list:
            r = rounds.get(str(entry.get("round")))
            if r is None:
                raise _Fail(60006, SOLD_OUT)
            allowed = {code: parse_diff_price(diff) for code, diff, _ in r["choices"]}
            for item in entry.get("comboItemList") or []:
                code = str(item.get("code"))
                count = int(item.get("quantity") or 0)
                if code not in allowed:
                    raise _Fail(60006, SOLD_OUT)
                extra += (allowed[code] + self._mods_extra(catalog[code].get("mods"), item.get("modification"))) * count
        return extra

    def _check_reservation(self, store: str, text: str) -> None:
        try:
            when = datetime.strptime(str(text), "%Y-%m-%d %H:%M")
        except ValueError:
            raise _Fail(400, "预约时间格式应为 yyyy-MM-dd HH:mm") from None
        if when <= self.now.replace(tzinfo=None):
            raise _Fail(600030, "预约时间已经过了")
        start, end = world.STORE_HOURS.get(store, ("00:00", "23:59"))
        if not start <= when.strftime("%H:%M") <= end:
            raise _Fail(600031, "门店已打烊")

    def _price(self, args: Dict[str, Any]) -> Dict[str, Any]:
        channel, store = self._scope(args)
        catalog = world.CATALOG[channel]
        items = args.get("items") or []
        if not items:
            raise _Fail(60006, SOLD_OUT)
        if sum(1 for item in items if item.get("couponId") or item.get("couponCode")) > 1:
            raise _Fail(600022, "暂不支持多张券使用")
        if args.get("reservationDate"):
            self._check_reservation(store, args["reservationDate"])
        coupons = {c["couponId"]: c for c in world.STORE_COUPONS.get(f"{store}:{int(args.get('orderType') or 1)}", [])}
        products = []
        total = total_original = 0
        for item in items:
            code = str(item.get("productCode") or "")
            qty = int(item.get("quantity") or 0)
            product = catalog.get(code)
            if product is None or qty < 1:
                raise _Fail(60006, SOLD_OUT)
            if product["kind"] == "combo":
                extra = self._combo_extra(product, item.get("roundList"), catalog)
            else:
                extra = self._mods_extra(product.get("mods"), item.get("modification"))
            unit, unit_original = product["price"] + extra, product["original"] + extra
            if item.get("couponId"):
                coupon = coupons.get(item["couponId"])
                if not coupon or coupon["couponCode"] != item.get("couponCode") or qty != 1 or code not in [p["productCode"] for p in coupon["products"]]:
                    raise _Fail(600012, "促销规则不支持该商品")
                kind, value = coupon["rule"]
                unit = max(0, unit - value) if kind == "minus" else value
            products.append({"productCode": code, "productName": product["name"], "quantity": qty, "originalSubtotal": unit_original * qty, "subtotal": unit * qty})
            total += unit * qty
            total_original += unit_original * qty
        data: Dict[str, Any] = {
            "productOriginalPrice": total_original,
            "productPrice": total,
            "originalPrice": total_original,
            "discount": total_original - total,
            "price": total,
            "productList": products,
        }
        if channel == "delivery":
            data.update(deliveryOriginalPrice=900, deliveryPrice=600, packingOriginalPrice=200, packingPrice=200)
            data["originalPrice"] = total_original + 900 + 200
            data["price"] = total + 600 + 200
            data["discount"] = data["originalPrice"] - data["price"]
            products.append({"productCode": "9200001", "productName": "打包服务费", "quantity": 10, "originalSubtotal": 200, "subtotal": 0})
        else:
            data["takeWayList"] = copy.deepcopy(world.TAKE_WAYS.get(store, []))
        return data

    def _t_calculate_price(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._price(args)

    # ------------------------------------------------------------ 订单

    @staticmethod
    def _picked(product: Dict[str, Any], item: Dict[str, Any], catalog: Dict[str, Any]) -> List[Tuple[str, str, int]]:
        if product["kind"] != "combo":
            return []
        chosen = {str(e.get("round")): e.get("comboItemList") or [] for e in item.get("roundList") or []}
        out = []
        for r in product["rounds"]:
            entries = chosen.get(str(r["id"]))
            if entries:
                out.extend((str(e["code"]), catalog[str(e["code"])]["name"], int(e.get("quantity") or 1)) for e in entries)
            else:
                code = next(c for c, _, default in r["choices"] if default)
                out.append((code, catalog[code]["name"], 1))
        return out

    def _store_info(self, store: str) -> Dict[str, Any]:
        for s in world.PICKUP_STORES + world.DRIVE_STORES:
            if s["storeCode"] == store:
                return s
        return {"storeName": "演示门店", "address": ""}

    def _t_create_order(self, args: Dict[str, Any]) -> Dict[str, Any]:
        priced = self._price(args)
        channel, store = self._scope(args)
        catalog = world.CATALOG[channel]
        order_type = int(args.get("orderType") or 1)
        take_way = None
        if order_type == 1:
            code = args.get("takeWayCode")
            if not code:
                raise _Fail(600042, "请选择取餐方式")
            take_way = next((t for t in priced.get("takeWayList", []) if t["code"] == code), None)
            if take_way is None:
                raise _Fail(600042, "这家店不支持这种取餐方式")
        else:
            if not args.get("addressId"):
                raise _Fail(400, "缺少收货地址")
            if len(str(args.get("remark") or "")) > 50:
                raise _Fail(400, "备注最多 50 个字")
        db = self._load_db()
        db["seq"] = int(db.get("seq") or 0) + 1
        seq = db["seq"]
        created = self.now.replace(tzinfo=None)
        order_id = "1024" + created.strftime("%Y%m%d%H%M%S") + f"{seq:010d}"
        info = self._store_info(store)
        products = []
        for item, line in zip(args.get("items") or [], priced["productList"]):
            product = catalog[str(item["productCode"])]
            products.append(
                {
                    "productCode": str(item["productCode"]),
                    "productName": product["name"],
                    "quantity": int(item.get("quantity") or 1),
                    "price": _yuan(line["subtotal"]),
                    "comboItemList": [{"productCode": c, "name": n, "quantity": q} for c, n, q in self._picked(product, item, catalog)],
                }
            )
        coupons = [c for c in world.STORE_COUPONS.get(f"{store}:{order_type}", []) if any(i.get("couponId") == c["couponId"] for i in args.get("items") or [])]
        order: Dict[str, Any] = {
            "orderId": order_id,
            "orderType": str(order_type),
            "beType": str(args.get("beType")),
            "beCode": args.get("beCode") or "",
            "storeCode": store,
            "storeName": info["storeName"],
            "storeAddress": info.get("address", ""),
            "createTime": created.strftime(TIME_FMT),
            "expirePayTime": (created + timedelta(minutes=15)).strftime(TIME_FMT),
            "orderStatus": "待支付",
            "status": "1",
            "payId": f"{seq:012d}",
            "productPrice": _yuan(priced["productPrice"]),
            "deliveryPrice": _yuan(priced.get("deliveryOriginalPrice") or 0),
            "realDeliveryPrice": _yuan(priced.get("deliveryPrice") or 0),
            "realPackingFeeTotalPrice": _yuan(priced.get("packingPrice") or 0),
            "totalAmount": _yuan(priced["originalPrice"]),
            "totalDiscountAmount": _yuan(priced["discount"]),
            "realTotalAmount": _yuan(priced["price"]),
            "couponList": [{"couponName": c["title"], "discountAmount": _yuan(c["rule"][1] if c["rule"][0] == "minus" else 0)} for c in coupons],
            "pickupCode": "",
            "needTableware": bool(args.get("needTableware", True)),
            "remark": str(args.get("remark") or ""),
            "orderProductList": products,
            "_locker": bool(take_way and str(take_way["code"]).startswith("locker")),
        }
        if take_way:
            order["takeWay"] = take_way["title"]
        if order_type == 2:
            expect = datetime.strptime(args["reservationDate"], "%Y-%m-%d %H:%M") if args.get("reservationDate") else created + timedelta(minutes=35)
            order["deliveryInfo"] = {
                "deliveryType": "预约送出" if args.get("reservationDate") else "立即送出",
                "expectDeliveryTime": expect.strftime(TIME_FMT),
                "deliveryAddress": "示例市示例区码农路 1024 号",
                "addressDetail": "1 栋",
                "customerNickname": "示例",
                "mobilePhone": "138****0000",
            }
        db["orders"].insert(0, order)
        self._save_db(db)
        return {
            "orderId": order_id,
            "payId": order["payId"],
            "payH5Url": f"{world.DEMO_LINK}/pay?orderId={order_id}",
            "orderDetail": self._view(order),
        }

    @staticmethod
    def _view(order: Dict[str, Any]) -> Dict[str, Any]:
        """query-order 的形态：商品没有 productCode，子项叫 itemName / itemQuantity，可选字段直接缺失。"""
        view = {k: copy.deepcopy(v) for k, v in order.items() if not k.startswith("_") and k not in ("orderType", "beType", "beCode", "storeCode")}
        view["orderProductList"] = [
            {
                "productName": p["productName"],
                "productImage": "",
                "quantity": p["quantity"],
                "price": p.get("price", "0"),
                "comboItemList": [{"itemName": c["name"], "itemQuantity": c["quantity"]} for c in p.get("comboItemList") or []],
            }
            for p in order.get("orderProductList") or []
        ]
        if "totalAmount" not in view:  # 历史订单：按商品价和券现算一套金额字段
            product_sum = sum(int(round(float(p.get("price") or 0) * 100)) for p in order.get("orderProductList") or [])
            discount = sum(int(round(float(c.get("discountAmount") or 0) * 100)) for c in order.get("couponList") or [])
            view.update(
                productPrice=_yuan(product_sum),
                deliveryPrice="0",
                realDeliveryPrice="0",
                totalAmount=_yuan(product_sum),
                totalDiscountAmount=_yuan(discount),
            )
        if "expirePayTime" not in view and order.get("createTime"):
            created = datetime.strptime(order["createTime"], TIME_FMT)
            view["expirePayTime"] = (created + timedelta(minutes=15)).strftime(TIME_FMT)
        view.setdefault("needTableware", False)
        view.setdefault("remark", "")
        if "deliveryInfo" in view:
            view.pop("takeWay", None)
        return view

    def _find(self, db: Dict[str, Any], order_id: str) -> Optional[Dict[str, Any]]:
        return next((o for o in db["orders"] if o["orderId"] == order_id), None)

    def _t_query_order(self, args: Dict[str, Any]) -> Dict[str, Any]:
        order_id = str(args.get("orderId") or "")
        db = self._load_db()
        order = self._find(db, order_id)
        if order is not None:
            if order["orderStatus"] == "待支付":
                # demo 里假装用户已经扫码付过款：进入配餐，发取餐码（取餐柜单再给柜子密码）
                order["orderStatus"] = "配餐中"
                order["status"] = "2"
                order["pickupCode"] = f"1024{int(db['seq']) % 10}"
                order["_demoPaid"] = True
                if order.get("_locker"):
                    order["lockerCode"] = "2048"
                    order["lockerQrCode"] = f"{world.DEMO_LINK}/locker/{order_id}.png"
                self._save_db(db)
            return self._view(order)
        for history in world.HISTORY:
            if history["orderId"] == order_id:
                return self._view(history)
        raise _Fail(610303, "订单号不存在！")

    def _t_order_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        db = self._load_db()
        rows = []
        keys = ("orderId", "orderType", "beType", "beCode", "storeCode", "storeName", "createTime", "orderStatus", "realTotalAmount")
        for order in db["orders"] + copy.deepcopy(world.HISTORY):
            row = {k: order.get(k) for k in keys}
            row["orderProductList"] = [
                {
                    "productCode": p.get("productCode"),
                    "productName": p.get("productName"),
                    "quantity": p.get("quantity"),
                    "comboItemList": copy.deepcopy(p.get("comboItemList") or []),
                }
                for p in order.get("orderProductList") or []
            ]
            rows.append(row)
        rows.sort(key=lambda o: o["createTime"], reverse=True)
        return {"list": rows[:10]}  # 实测没有分页，只给最近 10 单

    def _t_cancel_order(self, args: Dict[str, Any]) -> Dict[str, Any]:
        order_id = str(args.get("orderId") or "")
        reason = args.get("cancelReasonCode")
        if reason in (None, ""):
            raise _Fail(400, "cancelReasonCode 不能为空")
        if str(reason) not in {"1", "2", "3", "4", "5", "-1"}:
            raise _Fail(400, "取消原因不在可选范围内")
        db = self._load_db()
        order = self._find(db, order_id)
        if order is None:
            if any(h["orderId"] == order_id for h in world.HISTORY):
                raise _Fail(610305, "订单当前状态不支持取消")
            raise _Fail(610303, "订单号不存在！")
        if order["orderStatus"] not in ("待支付", "配餐中"):
            raise _Fail(610305, "订单当前状态不支持取消")
        order["orderStatus"] = "订单已取消"
        order["status"] = "7"
        self._save_db(db)
        return {"cancelResult": True, "orderId": order_id}
