"""隐藏的补全入口：`mcd-git __complete {meals,staged} [--demo] [--shell bash|zsh|fish] -- <前缀>`。

补全脚本每按一次 TAB 调一次，所以它必须又快又安静：只读本机文件（state.json、menu-cache.json），
绝不调 MCP、绝不联网；只 import 标准库 + state + menucache + textnorm（真实模式）；任何异常都吞掉、
返回 0、不往 stderr 打字——补全出错也不能打断用户的命令行。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import menucache, state

KINDS = ("meals", "staged")
SHELLS = ("bash", "zsh", "fish", "powershell")
_TRUE = ("1", "true", "yes", "on")


def _parse(argv: Sequence[str]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {"kind": None, "demo": False, "shell": None, "prefix": ""}
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            opts["prefix"] = args[i + 1] if i + 1 < len(args) else ""
            break
        if arg == "--demo":
            opts["demo"] = True
        elif arg == "--shell":
            opts["shell"] = args[i + 1] if i + 1 < len(args) else None
            i += 1
        elif arg.startswith("--shell="):
            opts["shell"] = arg.split("=", 1)[1]
        elif opts["kind"] is None and not arg.startswith("-"):
            opts["kind"] = arg
        i += 1
    if opts["shell"] not in SHELLS:
        opts["shell"] = None
    return opts


def _read_state(root: Path) -> Dict[str, Any]:
    """直接读 state.json（不走 StateStore，坏了它会抛 GitMcdError）；读不出来当空。"""
    try:
        data = json.loads((root / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _demo_meals(remote: Optional[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """demo 没有有效缓存时，从合成世界现造候选（第一次 `--demo add <TAB>` 就能补，不用先 ls-remote）。"""
    from .demo import world  # 惰性导入：只有 demo 回退才付这份加载开销

    channel = "delivery" if isinstance(remote, Mapping) and remote.get("mode") == "delivery" else "pickup"
    return [{"code": str(code), "name": item["name"], "type": "套餐" if item.get("kind") == "combo" else "单品"} for code, item in world.CATALOG[channel].items()]


def _meal_items(opts: Mapping[str, Any], root: Path, data: Mapping[str, Any], now: datetime) -> Any:
    remote = data.get("remote") if isinstance(data.get("remote"), dict) else None
    meals, status = menucache.load(root, remote, now)
    if meals is None and opts["demo"]:
        meals, status = _demo_meals(remote), "ok"
    if meals is None:
        return [], menucache.HINTS.get(status)
    return menucache.match(meals, opts["prefix"], codes=opts["shell"] != "bash"), None


def _staged_items(opts: Mapping[str, Any], data: Mapping[str, Any]) -> Any:
    lines = [x for x in data.get("index") or [] if isinstance(x, dict) and x.get("name")]
    meals = [{"code": str(x.get("code") or ""), "name": menucache.clean_name(x["name"]), "type": "", "desc": f"x{x.get('qty') or 1} · {x.get('id') or ''}"} for x in lines]
    items = menucache.match(meals, opts["prefix"])
    return items, None


def run(argv: Sequence[str], *, env: Mapping[str, str], out: Any, now: Optional[datetime] = None) -> int:
    try:
        opts = _parse(argv)
        if opts["kind"] not in KINDS:
            return 0
        opts["demo"] = opts["demo"] or str(env.get("MCD_GIT_DEMO", "")).lower() in _TRUE
        root = state.state_root(opts["demo"], env)
        data = _read_state(root)
        if opts["kind"] == "meals":
            items, hint = _meal_items(opts, root, data, now or state.now(opts["demo"], env))
        else:
            items, hint = _staged_items(opts, data)
        lines = menucache.format_lines(items, opts["shell"], hint)
        if lines:
            out.write("\n".join(lines) + "\n")
            out.flush()
    except Exception:  # noqa: BLE001 —— 补全绝不能把异常甩到用户的命令行上
        pass
    return 0
