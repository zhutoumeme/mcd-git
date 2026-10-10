"""MCP 传输层：Token 读取、JSON / SSE 响应、401 / 429、写操作拦截、Token 不外泄。"""
from __future__ import annotations

import io
import json
import os
import urllib.error
from typing import Any, Dict, List

import pytest

from mcd_git.errors import GitMcdError, McdError
from mcd_git.mcp_client import McdMcpClient, load_token, token_file

TOKEN = "unit-test-token-0000"


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __iter__(self):  # type: ignore[override]
        return iter(self.getvalue().splitlines(keepends=True))


class FakeServer:
    """按顺序回放响应；记录每次请求的 method 和请求头。"""

    def __init__(self, tool_responses: List[Any]) -> None:
        self.tool_responses = list(tool_responses)
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        payload = json.loads(request.data.decode("utf-8"))
        self.requests.append({"method": payload.get("method"), "headers": dict(request.header_items()), "payload": payload})
        if payload.get("method") == "initialize":
            return FakeResponse(json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}}}).encode())
        if payload.get("method") == "notifications/initialized":
            return FakeResponse(b"")
        item = self.tool_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(payload)
        return FakeResponse(json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": item}).encode())


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", code, "x", {}, io.BytesIO(body))  # type: ignore[arg-type]


RESULT = {"content": [{"type": "text", "text": "ok"}], "isError": False, "structuredContent": {"success": True, "code": 200, "data": {"ok": 1}}}


def test_token_env_first_then_file(monkeypatch: pytest.MonkeyPatch) -> None:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("file-token\n", encoding="utf-8")
    assert load_token({"MCD_MCP_TOKEN": " env-token "}) == "env-token"
    assert load_token({}) == "file-token"
    os.unlink(path)
    with pytest.raises(GitMcdError) as err:
        load_token({})
    assert err.value.fatal
    assert "file-token" not in err.value.message


def test_json_call_and_headers() -> None:
    server = FakeServer([RESULT])
    client = McdMcpClient(TOKEN, opener=server, min_interval=0)
    assert client.call_tool("now-time-info") == RESULT
    methods = [r["method"] for r in server.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    tool_headers = {k.lower(): v for k, v in server.requests[-1]["headers"].items()}
    assert tool_headers["authorization"] == f"Bearer {TOKEN}"
    assert tool_headers["mcp-protocol-version"] == "2025-06-18"
    assert TOKEN not in repr(client)


def test_sse_response() -> None:
    def sse(payload: Dict[str, Any]) -> FakeResponse:
        other = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
        mine = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": RESULT})
        return FakeResponse(f"event: message\ndata: {other}\n\ndata: {mine}\n\n".encode(), "text/event-stream")

    client = McdMcpClient(TOKEN, opener=FakeServer([sse]), min_interval=0)
    assert client.call_tool("now-time-info")["structuredContent"]["data"] == {"ok": 1}


def test_401_does_not_leak_token() -> None:
    client = McdMcpClient(TOKEN, opener=FakeServer([_http_error(401, TOKEN.encode())]), min_interval=0)
    with pytest.raises(McdError) as err:
        client.call_tool("now-time-info")
    assert err.value.http_status == 401
    assert TOKEN not in str(err.value)


def test_500_body_is_scrubbed() -> None:
    client = McdMcpClient(TOKEN, opener=FakeServer([_http_error(500, f"boom {TOKEN}".encode())]), min_interval=0)
    with pytest.raises(McdError) as err:
        client.call_tool("now-time-info")
    assert TOKEN not in err.value.message and "***" in err.value.message


def test_429_backoff_for_reads_only() -> None:
    sleeps: List[float] = []
    server = FakeServer([_http_error(429), _http_error(429), RESULT])
    client = McdMcpClient(TOKEN, opener=server, sleep=sleeps.append, min_interval=0)
    assert client.call_tool("query-meals", {"storeCode": "1"}) == RESULT
    assert sleeps == [1.0, 2.0]

    writes = FakeServer([_http_error(429), RESULT])
    writer = McdMcpClient(TOKEN, opener=writes, sleep=sleeps.append, min_interval=0)
    with pytest.raises(McdError):
        writer.call_tool("create-order", {"storeCode": "1"}, allow_write=True)
    assert [r["method"] for r in writes.requests].count("tools/call") == 1  # 写操作不重试


def test_write_tools_blocked_without_gate() -> None:
    server = FakeServer([])
    client = McdMcpClient(TOKEN, opener=server, min_interval=0)
    for tool in ("create-order", "cancel-order", "auto-bind-coupons", "mall-create-order", "draw-lottery", "party-order-create", "delivery-create-address"):
        with pytest.raises(GitMcdError):
            client.call_tool(tool, {})
    assert server.requests == []  # 连 initialize 都没发


def test_network_error_is_transport() -> None:
    client = McdMcpClient(TOKEN, opener=FakeServer([urllib.error.URLError("timed out")]), min_interval=0)
    with pytest.raises(McdError) as err:
        client.call_tool("order-list")
    assert err.value.transport
