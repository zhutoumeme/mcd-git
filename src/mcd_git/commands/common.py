"""子命令共用的小工具。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..api import Menu, McdApi
from ..cart import Line, build_line, summary
from ..errors import GitMcdError
from ..state import next_line_id
from ..textnorm import similar

SCOPE_KEYS = ("storeCode", "storeName", "mode", "modeLabel", "beType", "orderType", "beCode", "addressId", "addressHint", "hours")


def order_scope(remote: Dict[str, Any]) -> Dict[str, Any]:
    """commit 里记录的下单目的地（门店、渠道、beCode、地址 ID）。"""
    return {k: remote.get(k) for k in SCOPE_KEYS}


def same_scope(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    if not a or not b:
        return False
    return all(a.get(k) == b.get(k) for k in ("storeCode", "beType", "orderType", "beCode"))


def remote_url(remote: Dict[str, Any]) -> str:
    return f"mcd.cn:{remote.get('storeName') or remote.get('storeCode')}"


def remote_desc(remote: Dict[str, Any]) -> str:
    parts = [str(remote.get("modeLabel") or "")]
    if remote.get("addressHint"):
        parts.append(f"送到 {remote['addressHint']}")
    if remote.get("open") is True and remote.get("hours"):
        parts.append(f"营业中 {remote['hours']}")
    elif remote.get("open") is False:
        parts.append("已打烊")
    elif remote.get("hours"):
        parts.append(f"营业 {remote['hours']}")
    return " · ".join(p for p in parts if p)


def line_json(line: Line) -> Dict[str, Any]:
    out = {
        "id": line["id"],
        "code": line["code"],
        "name": line["name"],
        "summary": summary(line),
        "qty": line["qty"],
        "kind": line["kind"],
        "unitPrice": line.get("unit"),
        "notes": list(line.get("notes") or []),
    }
    if line.get("rounds"):
        out["choices"] = [{"round": r["name"], "items": [c["name"] for c in r["items"]]} for r in line["rounds"]]
    if line.get("coupon"):
        out["coupon"] = line["coupon"].get("title")
    return out


def resolve(menu: Menu, query: str) -> str:
    """按 code → 规范化精确名 → 唯一的包含匹配 找餐品；找不到或有歧义就报 git 风格的 pathspec 错误。"""
    exact, partial = menu.search(query)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        candidates = exact
    elif len(partial) == 1:
        return partial[0]
    else:
        candidates = partial
    if candidates:
        names = [f"{menu.items[c]['name']}（{c}）" for c in candidates[:8]]
        raise GitMcdError(
            f"pathspec '{query}' matched {len(candidates)} meals",
            hint=["说具体一点，或者直接用 code："] + ["  " + n for n in names],
            detail={"candidates": [{"code": c, "name": menu.items[c]["name"], "price": menu.items[c]["price"]} for c in candidates[:8]]},
        )
    close = similar(query, menu.names())
    hint = [f"你是不是想找：{'、'.join(close)}"] if close else ["用 git mcd ls-remote <关键词> 看看本店菜单"]
    raise GitMcdError(f"pathspec '{query}' did not match any meals", hint=hint, detail={"suggestions": close})


def make_line(
    ctx: Any,
    api: McdApi,
    remote: Dict[str, Any],
    menu: Menu,
    code: str,
    qty: int,
    *,
    no: Sequence[str] = (),
    with_: Sequence[str] = (),
    choose: Sequence[str] = (),
) -> Tuple[Line, List[str]]:
    detail = api.meal_detail(remote, code)
    return build_line(
        line_id=next_line_id(ctx.state, f"{remote['storeCode']}:{code}"),
        code=code,
        menu_item=menu.get(code),
        detail=detail,
        qty=qty,
        no=no,
        with_=with_,
        choose=choose,
    )


def index_total_hint(lines: Sequence[Line]) -> int:
    return sum(int(line.get("unit") or 0) * int(line.get("qty") or 1) for line in lines)
