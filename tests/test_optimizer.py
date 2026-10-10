"""rebase 优化器：凑套餐、一单一券、不多塞东西、20 次算价上限、预算 drop。"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from mcd_git.api import McdApi, normalize_store
from mcd_git.cart import build_line
from mcd_git.context import make_remote
from mcd_git.demo import DemoClient, world
from mcd_git.optimizer import plan
from mcd_git.state import DEMO_NOW

REMOTE = make_remote(normalize_store(world.PICKUP_STORES[0]), "pickup")


def _lines(*codes: str) -> List[Dict[str, Any]]:
    out = []
    for i, code in enumerate(codes):
        line, _ = build_line(line_id=f"line{i:03d}", code=code, menu_item=world.CATALOG["pickup"][code], detail=world.detail_payload("pickup", code))
        out.append(line)
    return out


def _plan(tmp_path: Any, lines: List[Dict[str, Any]], *, coupons: Any = None, budget: Any = None, client: Any = None) -> Dict[str, Any]:
    api = McdApi(client or DemoClient(tmp_path, now=DEMO_NOW))
    menu = api.menu(REMOTE)
    if coupons is None:
        coupons = api.store_coupons(REMOTE)
    counter = iter(range(1000))
    return plan(None, api, REMOTE, copy.deepcopy(lines), menu, coupons, budget=budget, new_id=lambda code: f"new{next(counter):04d}")


def test_squash_then_best_coupon(tmp_path: Any) -> None:
    result = _plan(tmp_path, _lines("1001", "2001", "3001"))
    assert result["before"] == 2400 + 1350 + 800
    assert result["after"] == 3850 - 500
    assert result["changed"]
    names = [line["name"] for line in result["lines"]]
    assert names == ["巨无霸套餐"]
    assert result["lines"][0]["coupon"]["title"] == "示例·巨无霸套餐立减 5 元"
    assert [op["op"] for op in result["todo"]] == ["pick", "squash", "squash", "coupon"]
    assert result["calls"] <= 20


def test_never_adds_food_to_fill_a_combo(tmp_path: Any) -> None:
    # 没有饮料：凑不满套餐，绝不为了便宜多塞一杯饮料
    result = _plan(tmp_path, _lines("1001", "2001"))
    assert all(line["kind"] == "single" for line in result["lines"])
    assert sorted(line["name"] for line in result["lines"]) == ["中薯条", "巨无霸"]
    assert result["after"] == 2400 + 600  # 只换上了薯条券


def test_only_one_coupon_per_order(tmp_path: Any) -> None:
    result = _plan(tmp_path, _lines("2001", "4001"))
    assert sum(1 for line in result["lines"] if line.get("coupon")) == 1
    assert result["after"] == min(600 + 1600, 1350 + 990)


def test_up_to_date_when_nothing_better(tmp_path: Any) -> None:
    result = _plan(tmp_path, _lines("2201"))
    assert not result["changed"] and result["after"] == result["before"] == 800


class CountingClient(DemoClient):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.price_calls = 0

    def call_tool(self, name: str, arguments: Any = None, *, allow_write: bool = False) -> Dict[str, Any]:
        if name == "calculate-price":
            self.price_calls += 1
        return super().call_tool(name, arguments, allow_write=allow_write)


def test_price_calls_capped_at_20(tmp_path: Any) -> None:
    client = CountingClient(tmp_path, now=DEMO_NOW)
    base = world.STORE_COUPONS["1000001:1"][1]  # 中薯条券
    many = []
    for i in range(40):
        coupon = copy.deepcopy(base)
        coupon["couponId"] = f"{i:032x}"
        coupon["title"] = f"示例券 {i}"
        many.append({"title": coupon["title"], "couponId": coupon["couponId"], "couponCode": coupon["couponCode"], "start": None, "end": None, "products": [{"code": "2001", "name": "中薯条"}]})
    result = _plan(tmp_path, _lines("2001"), coupons=many, client=client)
    assert client.price_calls == 20
    assert result["capped"]


def test_budget_drops_from_the_end(tmp_path: Any) -> None:
    result = _plan(tmp_path, _lines("9900001001", "4001"), budget=3000)
    assert result["todo"][-1]["op"] == "drop"
    assert [line["name"] for line in result["lines"]] == ["巨无霸套餐"]
    assert result["overBudget"]  # 只剩一个套餐也还是超预算：如实告诉用户，不再往下删
