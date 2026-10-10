"""本地状态：只放在 ~/.config/mcd-git/（demo 用 ~/.config/mcd-git/demo/），不碰任何代码仓库的 .git。

一个 state.json 存全部：远端（门店）、暂存区（购物车）、commit 快照、HEAD、push 过的订单、
待确认的 push / revert 预览。写入用临时文件 + rename，目录 700、文件 600。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

CN_TZ = timezone(timedelta(hours=8))
DEMO_NOW = datetime(2026, 10, 24, 11, 50, 0, tzinfo=CN_TZ)  # 程序员节的午饭时间
TIME_FMT = "%Y-%m-%d %H:%M:%S"

STATE_VERSION = 1
HASHED_FIELDS = ("parent", "branch", "message", "time", "remote", "lines", "price")


def config_home(env: Mapping[str, str]) -> Path:
    override = env.get("MCD_GIT_HOME")
    return Path(override).expanduser() if override else Path.home() / ".config" / "mcd-git"


def state_root(demo: bool, env: Mapping[str, str]) -> Path:
    base = config_home(env)
    return base / "demo" if demo else base


def display_path(path: Path) -> str:
    """展示路径时把家目录换成 ~，截图里不出现本机用户名。"""
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if home and text.startswith(home) else text


def now(demo: bool, env: Mapping[str, str]) -> datetime:
    """真实模式只认系统时钟：确认有效期、--at 校验和订单记录都靠它，不给环境变量改写的口子。
    demo 默认停在 DEMO_NOW，录演示时可以用 MCD_GIT_NOW 换个时段。测试通过 main(now=...) 注入时钟。"""
    if not demo:
        return datetime.now(CN_TZ)
    override = env.get("MCD_GIT_NOW")
    if override:
        return datetime.strptime(override, TIME_FMT).replace(tzinfo=CN_TZ)
    return DEMO_NOW


def fmt_time(dt: datetime) -> str:
    return dt.astimezone(CN_TZ).strftime(TIME_FMT)


def parse_time(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    for pattern in (TIME_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(text)[:26], pattern).replace(tzinfo=CN_TZ)
        except ValueError:
            continue
    return None


def meal_branch(dt: datetime) -> str:
    """按时段起分支名：breakfast / lunch / tea / dinner / night。"""
    minutes = dt.hour * 60 + dt.minute
    if 5 * 60 <= minutes < 10 * 60 + 30:
        return "breakfast"
    if minutes < 14 * 60 and minutes >= 10 * 60 + 30:
        return "lunch"
    if 14 * 60 <= minutes < 17 * 60:
        return "tea"
    if 17 * 60 <= minutes < 21 * 60:
        return "dinner"
    return "night"


BRANCH_LABELS = {"breakfast": "早餐", "lunch": "午饭", "tea": "下午茶", "dinner": "晚饭", "night": "夜里"}


def empty_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "remote": None,
        "candidates": None,
        "index": [],
        "orig_index": None,
        "commits": {},
        "head": None,
        "orders": [],
        "pending": None,
        "seq": 0,
    }


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "state.json"

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return empty_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            from .errors import GitMcdError

            raise GitMcdError(f"{display_path(self.path)} 损坏了，读不出来", fatal=True, hint="删掉它重新 git mcd init 即可（只影响本地购物车和记录）")
        state = empty_state()
        state.update(data if isinstance(data, dict) else {})
        return state

    def save(self, state: Dict[str, Any]) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True)
        fd, tmp = tempfile.mkstemp(prefix=".state-", dir=str(self.root))
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

    def reset(self) -> None:
        if self.path.is_file():
            self.path.unlink()


# ---------------------------------------------------------------- commit 对象


def commit_hash(commit: Mapping[str, Any]) -> str:
    """像 git 一样对快照内容取 sha1（不含 pushed 等可变字段），改了内容哈希就对不上。"""
    payload = {key: commit.get(key) for key in HASHED_FIELDS}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(("mcd-commit\0" + blob).encode("utf-8")).hexdigest()


def make_commit(
    *,
    parent: Optional[str],
    branch: str,
    message: str,
    time: str,
    remote: Dict[str, Any],
    lines: List[Dict[str, Any]],
    price: Dict[str, Any],
) -> Dict[str, Any]:
    commit: Dict[str, Any] = {
        "parent": parent,
        "branch": branch,
        "message": message,
        "time": time,
        "remote": copy.deepcopy(remote),
        "lines": copy.deepcopy(lines),
        "price": copy.deepcopy(price),
        "pushed": None,
    }
    commit["id"] = commit_hash(commit)
    return commit


def verify_commit(commit: Mapping[str, Any]) -> bool:
    return commit_hash(commit) == commit.get("id")


def head_commit(state: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    head = state.get("head")
    return state.get("commits", {}).get(head) if head else None


def find_commits(state: Mapping[str, Any], prefix: str) -> List[Dict[str, Any]]:
    prefix = prefix.lower()
    return [c for cid, c in state.get("commits", {}).items() if cid.startswith(prefix)]


def order_record(state: Mapping[str, Any], order_id: str) -> Optional[Dict[str, Any]]:
    for record in state.get("orders", []):
        if record.get("orderId") == order_id:
            return record
    return None


def last_order(state: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    orders = state.get("orders") or []
    return orders[-1] if orders else None


def next_line_id(state: Dict[str, Any], seed: str) -> str:
    state["seq"] = int(state.get("seq") or 0) + 1
    return hashlib.sha1(f"{seed}\0{state['seq']}".encode("utf-8")).hexdigest()[:7]
