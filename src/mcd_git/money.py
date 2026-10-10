"""金额换算。MCP 里金额单位混用：calculate-price 是「分」整数，菜单和订单是「元」字符串，
套餐换搭差价 diffPrice 是 "+ ¥2.5" 这样的展示串。内部一律用「分」整数。"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

_DIFF_RE = re.compile(r"^\s*([+-])?\s*¥?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:元)?\s*$")


def yuan_to_fen(value: Any) -> Optional[int]:
    """"30.5" / 30.5 / "0" → 3050 / 3050 / 0；空值或无法解析返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip().lstrip("¥"))
    except (InvalidOperation, ValueError):
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fen(value: Any) -> Optional[int]:
    """calculate-price 返回的「分」字段，容忍字符串和浮点。"""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def parse_diff_price(text: Any) -> int:
    """"+ ¥2.5" → 250，"- ¥0.5" → -50，"+ ¥0" → 0；解析不了按 0 处理。"""
    if text is None:
        return 0
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return yuan_to_fen(text) or 0
    match = _DIFF_RE.match(str(text))
    if not match:
        return 0
    sign, number = match.groups()
    value = yuan_to_fen(number) or 0
    return -value if sign == "-" else value


def fmt(amount: Optional[int], *, sign: bool = False) -> str:
    """3050 → "¥30.50"；sign=True 时带正负号（"-¥5.00" / "+¥2.00"）。"""
    if amount is None:
        return "¥—"
    prefix = ""
    if amount < 0:
        prefix = "-"
    elif sign and amount > 0:
        prefix = "+"
    value = Decimal(abs(int(amount))) / 100
    return f"{prefix}¥{value:.2f}"
