"""本机订单账本：card（吃麦贡献图）和吃麦统计的数据来源。

order-list 只给最近 10 单，所以每次 log / show / cherry-pick / card 顺手把看到的订单合并进来，push 成功时也记一笔，
时间长了格子才会越来越全。文件在 ~/.config/mcd-git/ledger.json（demo 用 ~/.config/mcd-git/demo/ledger.json），
目录 700、文件 600，写入用临时文件 + rename。删掉这个文件就清空了，不影响购物车和订单。

按订单号的哈希合并（和 git mcd log 给 App 订单显示的哈希一样）：同一单再看到时新状态覆盖旧状态，
order-list 不带的原价 / 优惠不会被冲成 null。每单只存下单时间、状态、餐品名和份数、原价 / 优惠 / 实付、取餐方式，
以及 push 它的本地 commit；不存订单号原文、门店、地址、手机号、取餐码、支付链接。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .errors import GitMcdError
from .history import order_hash
from .state import TIME_FMT, display_path, parse_time

LEDGER_VERSION = 1
FIELDS = ("time", "state", "status", "items", "original", "discount", "paid", "way", "commit", "source")
WRITE_ONCE = ("commit", "source")
_TEXT_FIELDS = ("time", "state", "status", "way", "commit", "source")
_AMOUNT_FIELDS = ("original", "discount", "paid")


def key_of(order_id: Any) -> str:
    return order_hash(str(order_id))


# ---------------------------------------------------------------- 条目


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _amount(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _qty(value: Any) -> int:
    try:
        qty = int(value)
    except (TypeError, ValueError):
        return 1
    return qty if qty > 0 else 1


def _items(rows: Any) -> List[Dict[str, Any]]:
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("name") or "").strip():
            out.append({"name": str(row["name"]).strip(), "qty": _qty(row.get("qty"))})
    return out


def _time(value: Any) -> Optional[str]:
    when = parse_time(value)
    return when.strftime(TIME_FMT) if when else None


def clean(entry: Any) -> Dict[str, Any]:
    """只留白名单里的字段，类型不对的当成没有（手改过的文件也不会把统计弄崩）。"""
    entry = entry if isinstance(entry, dict) else {}
    out: Dict[str, Any] = {}
    for field in FIELDS:
        value = entry.get(field)
        if field == "items":
            out[field] = _items(value)
        elif field in _AMOUNT_FIELDS:
            out[field] = _amount(value)
        else:
            out[field] = str(value) if isinstance(value, str) and value else None
    return out


def merge(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    """逐字段合并：new 里有值（不是 None / 空串 / 空列表）就覆盖 old，最新的状态和实付会盖掉旧的；
    commit、source 只写一次；取餐方式只会越记越具体（「到店自取」不会盖掉「到店自取 · 外带 · 店外取餐柜」）。"""
    out = clean(old)
    fresh = clean(new)
    for field in FIELDS:
        value = fresh[field]
        if _empty(value):
            continue
        current = out[field]
        if field in WRITE_ONCE and not _empty(current):
            continue
        if field == "way" and isinstance(current, str) and current.startswith(value):
            continue
        out[field] = copy.deepcopy(value)
    return out


def _history_way(order_type: Any, be_type: Any) -> str:
    if order_type == 2:
        return "麦乐送"
    if be_type == 5:
        return "得来速"
    return "到店自取"


def from_history(order: Dict[str, Any]) -> Dict[str, Any]:
    """order-list 的一单（api.normalize_history 之后）。order-list 只给实付，没有原价和优惠。"""
    return {
        "key": key_of(order.get("orderId") or ""),
        "time": _time(order.get("createTime")),
        "state": order.get("state") or "unknown",
        "status": order.get("statusLabel"),
        "items": _items(order.get("products")),
        "original": None,
        "discount": None,
        "paid": _amount(order.get("total")),
        "way": _history_way(order.get("orderType"), order.get("beType")),
        "source": "order-list",
    }


def from_query(order_id: str, order: Dict[str, Any]) -> Dict[str, Any]:
    """query-order 的一单（api.normalize_order 之后）：show 顺手补上原价和优惠。"""
    take_way = str(order.get("takeWay") or "").strip()
    if order.get("delivery"):
        way: Optional[str] = "麦乐送"
    elif "得来速" in take_way:
        way = "得来速"
    else:
        way = f"到店自取 · {take_way}" if take_way else None
    return {
        "key": key_of(order_id),
        "time": _time(order.get("createTime")),
        "state": order.get("state"),
        "status": order.get("statusLabel"),
        "items": _items(order.get("products")),
        "original": _amount(order.get("total")),
        "discount": _amount(order.get("discount")),
        "paid": _amount(order.get("realTotal")),
        "way": way,
        "source": "query-order",
    }


def from_push(head: Dict[str, Any], summary: Dict[str, Any], record: Dict[str, Any], order_id: str) -> Dict[str, Any]:
    """push 下单成功：把这一单和 push 它的本地 commit 关联起来，金额取确认时的 calculate-price。"""
    mode = str((head.get("remote") or {}).get("modeLabel") or "")
    take_way = str(summary.get("takeWay") or "")
    if take_way and mode and not take_way.startswith(mode):
        way = f"{mode} · {take_way}"
    else:
        way = take_way or mode
    return {
        "key": key_of(order_id),
        "time": _time(record.get("time")),
        "state": record.get("state"),
        "status": record.get("statusLabel"),
        "items": _items(head.get("lines")),
        "original": _amount(summary.get("originalPrice")),
        "discount": _amount(summary.get("discount")),
        "paid": _amount(summary.get("payable")),
        "way": way or None,
        "commit": head.get("id"),
        "source": "push",
    }


# ---------------------------------------------------------------- 文件


class Ledger:
    def __init__(self, root: Path, *, demo: bool) -> None:
        self.root = Path(root)
        self.path = self.root / "ledger.json"
        self.demo = demo

    def _broken(self) -> GitMcdError:
        return GitMcdError(
            f"{display_path(self.path)} 损坏了，读不出来",
            fatal=True,
            hint="删掉它即可（只影响吃麦统计和贡献图，不影响购物车和订单）",
        )

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.is_file():
            return self._seed() if self.demo else {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise self._broken() from None
        orders = data.get("orders") if isinstance(data, dict) else None
        if not isinstance(orders, dict) or not all(isinstance(v, dict) for v in orders.values()):
            raise self._broken()
        return {str(key): clean(entry) for key, entry in orders.items()}

    def _seed(self) -> Dict[str, Dict[str, Any]]:
        """demo 第一次读账本时的初始内容（合成订单随 card 一起补上）。"""
        return {}

    def save(self, orders: Dict[str, Dict[str, Any]]) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps({"version": LEDGER_VERSION, "orders": orders}, ensure_ascii=False, indent=1, sort_keys=True)
        fd, tmp = tempfile.mkstemp(prefix=".ledger-", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def upsert(self, entries: Iterable[Dict[str, Any]], *, create: bool = True) -> int:
        """按 key 合并进账本，返回改动了几条；create=False 时只更新已有的条目（比如 revert 只改状态）。"""
        orders = self.load()
        changed = 0
        for entry in entries:
            key = str(entry.get("key") or "")
            if not key or (key not in orders and not create):
                continue
            merged = merge(orders.get(key), entry)
            if merged != orders.get(key):
                orders[key] = merged
                changed += 1
        if changed:
            self.save(orders)
        return changed

    def entries(self) -> List[Dict[str, Any]]:
        """全部条目（附上 key），按下单时间倒序；同一时间按 key 排，顺序稳定。"""
        rows = [dict(entry, key=key) for key, entry in self.load().items()]
        rows.sort(key=lambda e: e["key"])
        rows.sort(key=lambda e: str(e.get("time") or ""), reverse=True)
        return rows

    def reset(self) -> None:
        if self.path.is_file():
            self.path.unlink()
