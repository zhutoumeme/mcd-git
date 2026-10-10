"""吃麦统计的口径（card 和吃麦统计共用）：入参是本机账本的条目，全部纯函数。

只有「吃了」的单才计入：取消的和待支付（没付款）的不算。
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from .state import meal_branch, parse_time

SLOT_ORDER = ("breakfast", "lunch", "tea", "dinner", "night")
# 统计里的时段名；log 里的分支说明另用 state.BRANCH_LABELS（那边夜里的单写作「夜里」）
SLOT_LABELS = {"breakfast": "早餐", "lunch": "午饭", "tea": "下午茶", "dinner": "晚饭", "night": "夜宵"}


def slot_of(time: Any) -> Optional[str]:
    when = parse_time(time)
    return meal_branch(when) if when else None


def eaten(entry: Dict[str, Any]) -> bool:
    return entry.get("state") not in ("cancelled", "pending")


def _counted(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in entries if eaten(e)]


def top_items(entries: Iterable[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """按份数（qty）累加，最多的在前；一样多按名字排。"""
    counts: Counter = Counter()
    for entry in _counted(entries):
        for item in entry.get("items") or []:
            name = str(item.get("name") or "").strip()
            if name:
                counts[name] += max(1, int(item.get("qty") or 1))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": name, "count": count} for name, count in ranked[:n]]


def slot_counts(entries: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {slot: 0 for slot in SLOT_ORDER}
    for entry in _counted(entries):
        slot = slot_of(entry.get("time"))
        if slot:
            counts[slot] += 1
    return counts


def top_slot(entries: Iterable[Dict[str, Any]]) -> Optional[str]:
    """单数最多的时段；一样多按 早餐 → 午饭 → 下午茶 → 晚饭 → 夜宵 取前面的。"""
    counts = slot_counts(entries)
    best = max(SLOT_ORDER, key=lambda slot: (counts[slot], -SLOT_ORDER.index(slot)))
    return best if counts[best] else None


def money(entries: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """实付和用券省下的钱（分）。order-list 不给优惠明细，缺的单记在 savedUnknown 里，省下的钱只算已知部分。"""
    paid = saved = known = unknown = 0
    for entry in _counted(entries):
        if entry.get("paid") is not None:
            paid += int(entry["paid"])
        if entry.get("discount") is None:
            unknown += 1
        else:
            saved += int(entry["discount"])
            known += 1
    return {"paid": paid, "saved": saved, "savedKnown": known, "savedUnknown": unknown}
