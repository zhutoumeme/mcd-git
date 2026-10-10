"""把本地 commit 和 order-list（最近 10 单）合成一条「吃麦提交历史」，供 log / show / cherry-pick 用。"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from .cart import summary
from .state import BRANCH_LABELS, head_commit, meal_branch, order_record, parse_time


def order_hash(order_id: str) -> str:
    """不是 mcd-git 下的单（App 里点的）没有 commit，按订单号派生一个稳定的哈希。"""
    return hashlib.sha1(f"mcd-order\0{order_id}".encode("utf-8")).hexdigest()


def _brief(names: List[str]) -> str:
    if not names:
        return "（无商品明细）"
    return names[0] + (f" 等 {len(names)} 样" if len(names) > 1 else "")


def build(state: Dict[str, Any], remote_orders: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    entries: Dict[str, Dict[str, Any]] = {}
    by_order: Dict[str, str] = {}
    for cid, commit in (state.get("commits") or {}).items():
        pushed = commit.get("pushed") or {}
        record = order_record(state, pushed.get("orderId")) if pushed.get("orderId") else None
        entry = {
            "hash": cid,
            "time": commit.get("time"),
            "branch": commit.get("branch"),
            "message": commit.get("message"),
            "items": [summary(line) for line in commit.get("lines") or []],
            "brief": _brief([line["name"] for line in commit.get("lines") or []]),
            "total": (record or {}).get("payable") if record else (commit.get("price") or {}).get("price"),
            "orderId": pushed.get("orderId"),
            "statusLabel": (record or {}).get("statusLabel") if pushed else "未 push",
            "state": (record or {}).get("state") if pushed else "local",
            "store": (commit.get("remote") or {}).get("storeName"),
            "mode": (commit.get("remote") or {}).get("modeLabel"),
            "local": True,
            "pushed": bool(pushed),
        }
        entries[cid] = entry
        if pushed.get("orderId"):
            by_order[pushed["orderId"]] = cid
    for order in remote_orders or []:
        oid = order.get("orderId") or ""
        if oid in by_order:
            entry = entries[by_order[oid]]
            entry["statusLabel"], entry["state"] = order["statusLabel"], order["state"]
            entry["total"] = order.get("total") if order.get("total") is not None else entry["total"]
            continue
        when = parse_time(order.get("createTime"))
        branch = meal_branch(when) if when else "lunch"
        names = [p["name"] for p in order.get("products") or []]
        entries[order_hash(oid)] = {
            "hash": order_hash(oid),
            "time": order.get("createTime"),
            "branch": branch,
            "message": BRANCH_LABELS.get(branch, branch),
            "items": [p["name"] + (f" x{p['qty']}" if p.get("qty", 1) > 1 else "") for p in order.get("products") or []],
            "brief": _brief(names),
            "total": order.get("total"),
            "orderId": oid,
            "statusLabel": order.get("statusLabel"),
            "state": order.get("state"),
            "store": order.get("storeName"),
            "mode": None,
            "local": False,
            "pushed": True,
            "order": order,
        }
    rows = sorted(entries.values(), key=lambda e: str(e.get("time") or ""), reverse=True)
    head = head_commit(state)
    head_hash = head["id"] if head else (rows[0]["hash"] if rows else None)
    latest_pushed = next((e for e in rows if e["pushed"]), None)
    seen_branches = set()
    for e in rows:
        decor = []
        if e["hash"] == head_hash:
            decor.append(f"HEAD -> {e['branch']}")
            seen_branches.add(e["branch"])
        if latest_pushed is not None and e["hash"] == latest_pushed["hash"]:
            decor.append(f"origin/{e['branch']}")
        if e["branch"] not in seen_branches:
            # 每个时段分支最新的一单挂上分支名，像 git log --decorate
            seen_branches.add(e["branch"])
            decor.append(e["branch"])
        e["decorations"] = decor
        e["head"] = e["hash"] == head_hash
    return rows


def find(rows: List[Dict[str, Any]], target: str) -> List[Dict[str, Any]]:
    text = target.strip().lower()
    if text in ("head", "@"):
        return [r for r in rows if r.get("head")][:1]
    if text.isdigit() and len(text) >= 12:
        return [r for r in rows if r.get("orderId") == text]
    return [r for r in rows if r["hash"].startswith(text)] if len(text) >= 4 else []
