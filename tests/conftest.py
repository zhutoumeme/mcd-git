"""测试全部离线：禁掉真实网络，状态目录指到临时目录，Token 环境清空。"""
from __future__ import annotations

import io
import json
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import pytest
from rich.console import Console

from mcd_git.cli import main
from mcd_git.demo import DemoClient


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("测试里不允许真实网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MCD_GIT_HOME", str(tmp_path / "mcd-git"))
    for key in ("MCD_MCP_TOKEN", "MCD_GIT_DEMO", "MCD_GIT_NOW", "MCD_MCP_URL"):
        monkeypatch.delenv(key, raising=False)


class RecordingClient(DemoClient):
    """「真实模式」测试用的 mock MCP：复用合成世界，但走 demo=False 的代码路径，并记录每次调用。

    hooks[tool] 可以返回一个 MCP result 来替换正常返回，或者抛异常模拟网络错误。
    """

    def __init__(self, root: Any, *, now: Any) -> None:
        super().__init__(root, now=now)
        self.hooks: Dict[str, Any] = {}

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, *, allow_write: bool = False) -> Dict[str, Any]:
        hook = self.hooks.get(name)
        if hook is not None:
            self.calls.append((name, dict(arguments or {})))
            return hook(arguments or {}, allow_write)
        return super().call_tool(name, arguments, allow_write=allow_write)

    def count(self, tool: str) -> int:
        return sum(1 for name, _ in self.calls if name == tool)

    def args_of(self, tool: str) -> List[Dict[str, Any]]:
        return [args for name, args in self.calls if name == tool]


class Runner:
    """在进程内跑 git mcd，返回 (退出码, 输出文本)；--json 时可直接拿解析好的 JSON。"""

    def __init__(self, tmp_path: Any) -> None:
        self.tmp_path = tmp_path
        self.clients: List[RecordingClient] = []
        self.hooks: Dict[str, Any] = {}
        self.env_overrides: Dict[str, str] = {}

    def factory(self, ctx: Any) -> RecordingClient:
        client = RecordingClient(ctx.store.root, now=ctx.now)
        client.hooks = self.hooks
        self.clients.append(client)
        return client

    @property
    def calls(self) -> List[Tuple[str, Dict[str, Any]]]:
        return [call for client in self.clients for call in client.calls]

    def count(self, tool: str) -> int:
        return sum(client.count(tool) for client in self.clients)

    def run(self, *argv: str, mock: bool = False, interactive: bool = False, answers: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
        import os

        buf = io.StringIO()
        console = Console(file=buf, width=120, highlight=False, markup=False, emoji=False, color_system=None)
        feed = list(answers or [])
        merged = dict(os.environ)
        merged.setdefault("MCD_GIT_NOW", "2026-10-24 11:50:00")  # 固定时钟，预约时间才稳定
        merged.update(self.env_overrides)
        merged.update(env or {})
        code = main(
            list(argv),
            console=console,
            env=merged,
            client_factory=self.factory if mock else None,
            input_fn=lambda prompt: feed.pop(0) if feed else "",
            interactive=interactive,
        )
        return code, buf.getvalue()

    def json(self, *argv: str, **kwargs: Any) -> Tuple[int, Dict[str, Any]]:
        code, out = self.run(*argv, "--json", **kwargs)
        return code, json.loads(out)


@pytest.fixture
def runner(tmp_path: Any) -> Runner:
    return Runner(tmp_path)


def mcp_result(envelope: Optional[Dict[str, Any]] = None, *, text: str = "", structured: bool = True, is_error: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured and envelope is not None:
        result["structuredContent"] = envelope
    return result
