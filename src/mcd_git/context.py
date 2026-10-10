"""一条命令的运行上下文：输出、状态、时钟、MCP 客户端（按需创建，不需要的命令不读 Token）。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from rich.console import Console

from .api import MODES, McdApi, normalize_store
from .errors import GitMcdError
from .state import BRANCH_LABELS, StateStore, meal_branch, now, state_root


@dataclass
class Result:
    data: Dict[str, Any]
    render: Optional[Callable[[Console], None]] = None
    exit_code: int = 0


class Context:
    def __init__(
        self,
        *,
        demo: bool,
        json_mode: bool,
        console: Console,
        env: Mapping[str, str],
        interactive: bool,
        input_fn: Callable[[str], str] = input,
        client_factory: Optional[Callable[["Context"], Any]] = None,
    ) -> None:
        self.demo = demo
        self.json = json_mode
        self.console = console
        self.env = env
        self.interactive = interactive
        self._input = input_fn
        self._client_factory = client_factory
        self.store = StateStore(state_root(demo, env))
        self.state: Dict[str, Any] = self.store.load()
        self.now = now(demo, env)
        self._api: Optional[McdApi] = None
        self._client: Any = None

    # -- MCP

    @property
    def client(self) -> Any:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory(self)
            elif self.demo:
                from .demo import DemoClient

                self._client = DemoClient(self.store.root, now=self.now)
            else:
                from .mcp_client import McdMcpClient, load_token

                self._client = McdMcpClient(load_token(self.env))
        return self._client

    def api(self) -> McdApi:
        if self._api is None:
            self._api = McdApi(self.client)
        return self._api

    @property
    def mcp_calls(self) -> Dict[str, int]:
        return dict(self._api.calls) if self._api is not None else {}

    # -- 状态

    def save(self) -> None:
        self.store.save(self.state)

    @property
    def branch(self) -> str:
        return meal_branch(self.now)

    @property
    def branch_label(self) -> str:
        return BRANCH_LABELS.get(self.branch, self.branch)

    def require_remote(self) -> Dict[str, Any]:
        remote = self.state.get("remote")
        if remote:
            return remote
        if self.demo:
            # demo 第一次用：直接连上演示门店，30 秒试玩不用先 init
            from .demo import world

            self.state["remote"] = make_remote(normalize_store(world.PICKUP_STORES[0]), "pickup")
            self.save()
            return self.state["remote"]
        raise GitMcdError(
            "not a mcd repository：还没选门店",
            fatal=True,
            hint=["先运行 git mcd init（收藏门店）", "或 git mcd init --city 上海 --keyword 人民广场（按地标搜）"],
        )

    def ask(self, prompt: str) -> str:
        try:
            return self._input(prompt)
        except EOFError:
            return ""


def make_remote(store: Dict[str, Any], mode: str, *, address: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    spec = MODES[mode]
    remote = {
        "storeCode": store["storeCode"],
        "storeName": store["storeName"],
        "address": store.get("address") or "",
        "mode": mode,
        "modeLabel": spec["label"],
        "beType": spec["beType"],
        "orderType": spec["orderType"],
        "beCode": store.get("beCode") if spec["beType"] != 1 else None,
        "open": store.get("open"),
        "hours": store.get("hours"),
        "reservation": copy.deepcopy(store.get("reservation") or []),
        "addressId": None,
        "addressHint": None,
    }
    if address:
        remote["addressId"] = address["addressId"]
        remote["addressHint"] = address["hint"]
    return remote


def remote_brief(remote: Dict[str, Any]) -> Dict[str, Any]:
    """写进 commit / JSON 输出的门店信息（不含 addressId）。"""
    return {
        "storeCode": remote["storeCode"],
        "storeName": remote["storeName"],
        "mode": remote["mode"],
        "modeLabel": remote["modeLabel"],
        "hours": remote.get("hours"),
        "open": remote.get("open"),
        "addressHint": remote.get("addressHint"),
    }
