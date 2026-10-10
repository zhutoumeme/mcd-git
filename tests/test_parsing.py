"""返回解包（structuredContent 优先、三种 Markdown 布局兜底）、金额、订单状态等解析。"""
from __future__ import annotations

import json

import pytest

from mcd_git.api import Menu, normalize_order, normalize_price, order_state, parse_reservation
from mcd_git.errors import McdError
from mcd_git.mcp_client import parse_envelope, unwrap
from mcd_git.money import fmt, parse_diff_price, yuan_to_fen

from .conftest import mcp_result

ENV_OK = {"success": True, "code": 200, "message": "请求成功", "datetime": "2026-01-01 12:00:00", "traceId": "t" * 32, "data": {"x": 1}}


def test_structured_content_wins_over_text() -> None:
    result = mcp_result(ENV_OK, text="# 说明\n## Original Response\n" + json.dumps(dict(ENV_OK, data={"x": 2})))
    assert unwrap(result) == {"x": 1}


def test_layout_a_marker_with_tip_and_tail() -> None:
    text = (
        "# API Response Information\n\n字段说明……\n\n## Original Response\n"
        "lockerQrCode 请用 ![alt](url) 渲染\n"
        + json.dumps(ENV_OK, ensure_ascii=False)
        + "\n\n## 数据传参规则\n带 {unselectedKey} 的组要全传"
    )
    assert unwrap(mcp_result(ENV_OK, text=text, structured=False)) == {"x": 1}


def test_layout_b_without_marker() -> None:
    text = "请按下面的表格展示门店 {storeName}：\n\n| 门店 | 地址 |\n" + json.dumps(ENV_OK) + "\n\n## 展示结果时注意……"
    assert unwrap(mcp_result(ENV_OK, text=text, structured=False)) == {"x": 1}


def test_layout_c_pure_markdown_needs_structured_content() -> None:
    text = "## 你的优惠券\n- 示例券 A\n- 示例券 B\n"
    assert unwrap(mcp_result(ENV_OK, text=text)) == {"x": 1}
    with pytest.raises(McdError) as err:
        unwrap(mcp_result(None, text=text, structured=False))
    assert "无法解析" in err.value.message
    assert "示例券" not in err.value.message  # 不把正文带进异常


def test_business_failure_is_not_is_error() -> None:
    env = {"success": False, "code": 60006, "message": "商品已售罄或不可售", "datetime": "2026-01-01 12:00:00", "traceId": "abc123"}
    with pytest.raises(McdError) as err:
        parse_envelope(mcp_result(env))
    assert err.value.code == 60006
    assert err.value.trace_id == "abc123"
    assert "code 60006" in err.value.remote_lines()[0]


def test_is_error_without_envelope() -> None:
    with pytest.raises(McdError) as err:
        unwrap(mcp_result(None, text="Tool not found: foo\nmore", structured=False, is_error=True))
    assert err.value.message == "Tool not found: foo"


@pytest.mark.parametrize(
    "value, expected",
    [("30.5", 3050), ("40", 4000), ("0", 0), (30.5, 3050), ("¥8", 800), (None, None), ("", None), ("abc", None)],
)
def test_yuan_to_fen(value: object, expected: object) -> None:
    assert yuan_to_fen(value) == expected


@pytest.mark.parametrize("text, expected", [("+ ¥0", 0), ("+ ¥2.5", 250), ("- ¥0.5", -50), ("+¥3", 300), (None, 0), ("奇怪", 0)])
def test_diff_price(text: object, expected: int) -> None:
    assert parse_diff_price(text) == expected


def test_fmt() -> None:
    assert fmt(3050) == "¥30.50"
    assert fmt(-500) == "-¥5.00"
    assert fmt(200, sign=True) == "+¥2.00"
    assert fmt(None) == "¥—"


@pytest.mark.parametrize(
    "order_status, status, state",
    [
        ("订单已完成", "40", "completed"),
        ("待支付", None, "pending"),
        ("配餐中-已支付", None, "preparing"),
        ("订单已取消", "70", "cancelled"),
        ("1", None, "pending"),
        (None, "7", "cancelled"),
        ("10", None, "preparing"),
        ("神秘状态", "40", "unknown"),
    ],
)
def test_order_state(order_status: object, status: object, state: str) -> None:
    label, got = order_state(order_status, status)
    assert got == state
    if order_status == "神秘状态":
        assert label == "神秘状态"


def test_reservation_text() -> None:
    days = parse_reservation([{"date": "2026-01-01", "today": True, "reservationOptionText": "宵夜(00:00至04:45,22:14至23:59)，早餐(05:14至10:15)"}])
    assert days[0]["slots"] == [
        {"name": "宵夜", "ranges": [["00:00", "04:45"], ["22:14", "23:59"]]},
        {"name": "早餐", "ranges": [["05:14", "10:15"]]},
    ]


def test_price_drops_packing_pseudo_line() -> None:
    priced = normalize_price(
        {
            "price": 2600,
            "originalPrice": 2600,
            "deliveryPrice": 500,
            "packingPrice": 100,
            "productList": [
                {"productCode": "901001", "productName": "示例汉堡", "quantity": 1, "originalSubtotal": 2000, "subtotal": 2000},
                {"productCode": "9200001", "productName": "打包服务费", "quantity": 10, "originalSubtotal": 100, "subtotal": 0},
            ],
        }
    )
    assert [p["name"] for p in priced["products"]] == ["示例汉堡"]
    assert priced["packingPrice"] == 100
    assert priced["takeWays"] == []


def test_menu_cleans_category_names_and_frequent() -> None:
    menu = Menu(
        {
            "categories": [{"name": "人气\n热卖", "meals": [{"code": "9900000001", "tags": ["套餐"]}]}],
            "meals": {"9900000001": {"name": "示例三件套", "currentPrice": "30.5", "originalPrice": "40"}},
            "frequent": {"code": "9900000001", "tags": ["我的常点"]},
        }
    )
    item = menu.get("9900000001")
    assert item["price"] == 3050 and item["original"] == 4000
    assert item["categories"] == ["人气热卖"]
    assert menu.is_combo("9900000001")
    assert menu.frequent == "9900000001"


def test_order_drops_personal_delivery_fields() -> None:
    order = normalize_order(
        {
            "orderId": "1" * 28,
            "orderStatus": "配送中",
            "realTotalAmount": "26",
            "deliveryInfo": {"deliveryType": "立即送出", "expectDeliveryTime": "2026-01-01 12:45:00", "deliveryAddress": "某路 1 号", "mobilePhone": "13800000000"},
        }
    )
    assert order["delivery"] == {"type": "立即送出", "expect": "2026-01-01 12:45:00"}
    assert "13800000000" not in json.dumps(order, ensure_ascii=False)
    assert order["lockerCode"] is None and order["state"] == "delivering"
