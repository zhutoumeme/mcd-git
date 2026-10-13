"""本机菜单缓存（给命令行补全用）：只在本来就调 query-meals 的命令里顺手写，补全只读它、绝不联网。

热路径模块：只 import 标准库 + textnorm + state，禁止 import api / context / render / rich / mcp_client，
否则一次 TAB 就要多花 100ms 去加载用不上的东西。

文件 <状态目录>/menu-cache.json（demo 在 demo 子目录），目录 700、文件 600，只存：
version、storeCode、scope{mode,beType,orderType,beCode}、writtenAt、meals[{code,name,type}]。
不存价格、addressId、addressHint、门店地址、Token。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import state
from .textnorm import normalize

CACHE_NAME = "menu-cache.json"
CACHE_VERSION = 1
TTL = timedelta(hours=24)
CLOCK_SKEW = timedelta(minutes=5)  # 写入时间比现在晚这么多以内算时钟偏差，再晚就当过期
SCOPE_KEYS = ("mode", "beType", "orderType", "beCode")
MEAL_TYPES = ("套餐", "单品")
PAYLOAD_KEYS = {"version", "storeCode", "scope", "writtenAt", "meals"}

HINT_REFRESH = "先运行 mcd-git ls-remote 刷新菜单"
HINT_INIT = "先运行 mcd-git init 选门店"
HINTS = {
    "missing": HINT_REFRESH,
    "expired": HINT_REFRESH,
    "store_mismatch": HINT_REFRESH,
    "corrupt": HINT_REFRESH,
    "no_remote": HINT_INIT,
}

_BASH_SAFE = set("-_./+%,^")


def cache_path(root: Path) -> Path:
    return Path(root) / CACHE_NAME


def clean_name(name: Any) -> str:
    """名称去首尾空白，\\t / \\n 换成空格（补全输出一行一个候选、用 \\t 分隔说明）。"""
    return str(name or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _scope(remote: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: remote.get(key) for key in SCOPE_KEYS}


def write(root: Path, remote: Mapping[str, Any], meals: Iterable[Mapping[str, Any]], now: datetime) -> None:
    """写缓存（写法同 StateStore.save：临时文件 + chmod 600 + rename）。meals 是 [{code, name, type}]。"""
    rows: List[Dict[str, str]] = []
    for meal in meals:
        kind = meal.get("type")
        if kind not in MEAL_TYPES:
            raise ValueError(f"餐品类型只能是套餐/单品：{kind!r}")
        name = clean_name(meal.get("name"))
        if not name:
            continue
        rows.append({"code": str(meal.get("code")), "name": name, "type": str(kind)})
    payload = {
        "version": CACHE_VERSION,
        "storeCode": str(remote["storeCode"]),
        "scope": _scope(remote),
        "writtenAt": state.fmt_time(now),
        "meals": rows,
    }
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    fd, tmp = tempfile.mkstemp(prefix=".menu-cache-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, cache_path(root))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _valid_meals(meals: Any) -> Optional[List[Dict[str, str]]]:
    if not isinstance(meals, list):
        return None
    out: List[Dict[str, str]] = []
    for meal in meals:
        if not isinstance(meal, dict):
            return None
        code, name, kind = meal.get("code"), meal.get("name"), meal.get("type")
        if not isinstance(code, str) or not isinstance(name, str) or kind not in MEAL_TYPES:
            return None
        out.append({"code": code, "name": name, "type": kind})
    return out


def load(root: Path, remote: Optional[Mapping[str, Any]], now: datetime) -> Tuple[Optional[List[Dict[str, str]]], str]:
    """读缓存，返回 (meals, 状态)。状态：ok / missing / corrupt / expired / store_mismatch / no_remote；不是 ok 时 meals 为 None。

    没选门店（remote 为空）优先报 no_remote：这时提示「先 init」比「先 ls-remote」有用。"""
    if not isinstance(remote, Mapping) or not remote.get("storeCode"):
        return None, "no_remote"
    path = cache_path(root)
    if not path.is_file():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "corrupt"
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return None, "corrupt"
    meals = _valid_meals(data.get("meals"))
    scope = data.get("scope")
    if meals is None or not isinstance(scope, dict) or not isinstance(data.get("storeCode"), str) or not isinstance(data.get("writtenAt"), str):
        return None, "corrupt"
    if data["storeCode"] != str(remote.get("storeCode")) or {k: scope.get(k) for k in SCOPE_KEYS} != _scope(remote):
        return None, "store_mismatch"
    written = state.parse_time(data["writtenAt"])
    if written is None:
        return None, "expired"
    age = now - written
    if age > TTL or age < -CLOCK_SKEW:
        return None, "expired"
    return meals, "ok"


# ---------------------------------------------------------------- 匹配


def _unshell(prefix: str) -> str:
    """把 shell 里还没闭合的引号 / 反斜杠转义去掉，还原成用户想输入的字。"""
    text = prefix or ""
    if text[:1] in ("'", '"'):
        quote, text = text[0], text[1:]
        if text.endswith(quote):
            text = text[:-1]
        if quote == "'":
            return text  # 单引号里反斜杠是字面量
    return re.sub(r"\\(.)", r"\1", text).rstrip("\\")


def _light(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def match(meals: Sequence[Mapping[str, Any]], prefix: str, *, limit: int = 50, codes: bool = False) -> List[Dict[str, Any]]:
    """按前缀找候选：前缀命中（菜单顺序）在前，包含命中在后，按名称去重，最多 limit 条。

    同时比「NFKC + 小写」和 textnorm.normalize：normalize 会去掉开头的「麦咖啡」，只用它的话
    输入「麦咖」补不出「麦咖啡™美式」。codes=True 且输入全是数字时，先给 code 以它开头的候选（kind='code'）。"""
    q = _unshell(prefix)
    items: List[Dict[str, Any]] = []
    if codes and q and q.isascii() and q.isdigit():
        items.extend(dict(m, kind="code") for m in meals if str(m.get("code", "")).startswith(q))
    if not q:
        named = list(meals)
    else:
        qn, qr = normalize(q), _light(q)
        head: List[Mapping[str, Any]] = []
        body: List[Mapping[str, Any]] = []
        for meal in meals:
            name = str(meal.get("name") or "")
            nn, nr = normalize(name), _light(name)
            if nr.startswith(qr) or (qn and nn.startswith(qn)):
                head.append(meal)
            elif qr in nr or (qn and qn in nn):
                body.append(meal)
        named = head + body
    seen = set()
    for meal in named:
        name = meal.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(dict(meal, kind="name"))
    return items[:limit]


# ---------------------------------------------------------------- 输出


def bash_escape(name: str) -> str:
    """bash 候选转义：ASCII 的非字母数字（除 -_./+%,^）前加反斜杠，中文、™、全角符号原样。

    不用 printf %q：macOS 自带 bash 3.2 会把中文转成 $'\\xxx'。"""
    out = []
    for ch in name:
        if ord(ch) < 128 and not ch.isalnum() and ch not in _BASH_SAFE:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _desc(item: Mapping[str, Any]) -> str:
    if item.get("desc"):
        return clean_name(item["desc"])
    if item.get("kind") == "code":
        return f"{item.get('name')} · {item.get('type')}"
    return f"{item.get('type')} · {item.get('code')}"


def format_lines(items: Sequence[Mapping[str, Any]], shell: Optional[str], hint: Optional[str] = None) -> List[str]:
    """按 shell 输出候选行。

    bash：每行一个转义后的名称（不给 code、不给提示）；
    zsh：每行 'value\\tdesc'，没有候选且有提示时输出一行 '\\t提示'（value 为空，zsh 脚本转成 _message）；
    fish / PowerShell / 未指定：每行 `value\tdesc`，不输出提示（shell 直接把说明作为候选描述）。"""
    if shell == "bash":
        lines: List[str] = []
        for item in items:
            if item.get("kind") == "code":
                continue
            value = bash_escape(clean_name(item.get("name")))
            if value and value not in lines:
                lines.append(value)
        return lines
    lines = []
    for item in items:
        value = clean_name(item.get("code") if item.get("kind") == "code" else item.get("name"))
        if value:
            lines.append(f"{value}\t{_desc(item)}")
    if not lines and hint and shell == "zsh":
        lines.append("\t" + hint)
    return lines
