"""餐品名规范化与匹配（菜单名、历史订单名、券商品名之间对齐用）。"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Iterable, List, Sequence, Tuple

_STRIP_RE = re.compile(r"[\s\"'“”‘’【】\[\]「」·・•]")


def normalize(name: str) -> str:
    """去 ™®（要在 NFKC 之前去，否则 ™ 会变成 TM）→ NFKC → 去空白引号括号符号 → 常见别名。"""
    text = (name or "").replace("™", "").replace("®", "")
    text = unicodedata.normalize("NFKC", text)
    text = _STRIP_RE.sub("", text)
    text = text.replace("(盒装)", "").replace("可口可乐", "可乐")
    if text.startswith("麦咖啡") and len(text) > 3:
        text = text[3:]
    return text.lower()


def rank_matches(query: str, names: Sequence[Tuple[str, str]]) -> Tuple[List[str], List[str]]:
    """在 [(key, 名称)] 里找 query。返回 (精确命中的 key, 包含命中的 key)。"""
    q = normalize(query)
    exact: List[str] = []
    partial: List[str] = []
    if not q:
        return exact, partial
    for key, name in names:
        n = normalize(name)
        if n == q:
            exact.append(key)
        elif q in n:
            partial.append(key)
    return exact, partial


def similar(query: str, names: Iterable[str], limit: int = 3) -> List[str]:
    """"你是不是想找"：按规范化后的名字做近似匹配。"""
    pool = {}
    for name in names:
        pool.setdefault(normalize(name), name)
    hits = difflib.get_close_matches(normalize(query), list(pool), n=limit, cutoff=0.4)
    return [pool[h] for h in hits]
