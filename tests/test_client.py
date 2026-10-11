"""MCP 传输层：Token 读取、JSON / SSE 响应、401 / 429、写操作拦截、不跟随重定向、Token 不外泄。"""
from __future__ import annotations

import http.client
import http.server
import io
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

from mcd_git.errors import GitMcdError, McdError
from mcd_git.mcp_client import DEFAULT_URL, McdMcpClient, _default_opener, load_token, token_file

from .conftest import allow_loopback

TOKEN = "unit-test-token-0000"


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __iter__(self):  # type: ignore[override]
        return iter(self.getvalue().splitlines(keepends=True))


class TruncatedResponse(FakeResponse):
    """响应头正常，读响应体时连接断了。"""

    def read(self, *args: Any) -> bytes:  # type: ignore[override]
        raise http.client.IncompleteRead(b'{"jsonrpc": "2.0", "id"', 200)


class FakeServer:
    """按顺序回放响应；记录每次请求的地址、method 和请求头。"""

    def __init__(self, tool_responses: List[Any]) -> None:
        self.tool_responses = list(tool_responses)
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        payload = json.loads(request.data.decode("utf-8"))
        self.requests.append({"url": request.full_url, "method": payload.get("method"), "headers": dict(request.header_items()), "payload": payload})
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


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_token_env_first_then_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # conftest 的 offline fixture 已把 HOME/USERPROFILE 指到 tmp，所以 ~/.config 落在临时目录，
    # 哪个系统上都碰不到开发者真实的 Token。
    path = Path.home() / ".config" / "mcd-mcp" / "token"
    _write(path, "file-token\n")
    assert load_token({"MCD_MCP_TOKEN": " env-token "}) == "env-token"  # env 永远最高优先级
    assert load_token({}) == "file-token"
    os.unlink(path)
    with pytest.raises(GitMcdError) as err:
        load_token({})
    assert err.value.fatal
    assert "file-token" not in err.value.message


def test_token_file_linux_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_posix(monkeypatch)
    expected = Path.home() / ".config" / "mcd-mcp" / "token"
    assert token_file({}) == expected
    _write(expected, "linux-token\n")
    assert load_token({}) == "linux-token"


def test_token_xdg_config_home_wins_over_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_posix(monkeypatch)
    xdg = tmp_path / "xdg"
    _write(xdg / "mcd-mcp" / "token", "xdg-token\n")
    _write(Path.home() / ".config" / "mcd-mcp" / "token", "legacy-token\n")
    assert load_token({"XDG_CONFIG_HOME": str(xdg)}) == "xdg-token"


def _fake_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    # 模拟 Windows：patch 平台判断函数 _is_windows，而不是改全局 os.name
    # （改 os.name 会连累 pathlib 在非 Windows 机器上无法实例化 WindowsPath）。
    monkeypatch.setattr("mcd_git.state._is_windows", lambda: True)


def _fake_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mcd_git.state._is_windows", lambda: False)


def test_token_file_windows_appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_windows(monkeypatch)
    appdata = tmp_path / "AppData"
    env = {"APPDATA": str(appdata)}
    assert token_file(env) == appdata / "mcd-mcp" / "token"
    _write(appdata / "mcd-mcp" / "token", "win-token\n")
    assert load_token(env) == "win-token"
    # 没有 APPDATA 时 token_file() 退回 ~/.config/mcd-mcp/token
    assert token_file({}) == Path.home() / ".config" / "mcd-mcp" / "token"


def test_token_env_wins_even_with_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_windows(monkeypatch)
    appdata = tmp_path / "AppData"
    xdg = tmp_path / "xdg"
    _write(appdata / "mcd-mcp" / "token", "win-token\n")
    _write(xdg / "mcd-mcp" / "token", "xdg-token\n")
    _write(Path.home() / ".config" / "mcd-mcp" / "token", "legacy-token\n")
    assert load_token({"MCD_MCP_TOKEN": "env-token", "APPDATA": str(appdata), "XDG_CONFIG_HOME": str(xdg)}) == "env-token"


def test_token_missing_hint_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_windows(monkeypatch)
    appdata = tmp_path / "AppData"
    env = {"APPDATA": str(appdata)}
    with pytest.raises(GitMcdError) as err:
        load_token(env)
    hint = "\n".join(err.value.hints)
    assert "setx" in hint and "$env:" in hint
    assert "export" not in hint and "chmod" not in hint
    assert "MCD_MCP_TOKEN" in hint and "open.mcd.cn" in hint
    assert "--demo" in hint
    assert str(appdata / "mcd-mcp" / "token") in hint  # 路径用 token_file() 实际解析出的 Windows 路径


def test_token_missing_hint_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_posix(monkeypatch)
    with pytest.raises(GitMcdError) as err:
        load_token({})
    hint = "\n".join(err.value.hints)
    assert "export" in hint and "chmod" in hint
    assert "setx" not in hint and "$env:" not in hint
    assert "MCD_MCP_TOKEN" in hint and "open.mcd.cn" in hint
    assert "--demo" in hint
    assert "~/.config/mcd-mcp/token" in hint  # display_path 把 home 前缀收成 ~


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


NEVER_TOOLS = ("mall-create-order", "draw-lottery", "party-order-create", "delivery-create-address")


def test_never_tools_blocked_even_with_allow_write() -> None:
    """积分兑换、抽奖、派对订单、新建地址：不在确认闸能放行的三个里，带着 allow_write 也调不出去。"""
    server = FakeServer([])
    client = McdMcpClient(TOKEN, opener=server, min_interval=0)
    for tool in NEVER_TOOLS:
        with pytest.raises(GitMcdError) as err:
            client.call_tool(tool, {}, allow_write=True)
        assert err.value.fatal and "确认闸" in err.value.message
    assert server.requests == []


def test_gated_tool_needs_allow_write() -> None:
    """auto-bind-coupons 只有 pull 的确认闸放行（allow_write）才发得出去；发出去也只发一次，429 不重试。"""
    server = FakeServer([_http_error(429), RESULT])
    client = McdMcpClient(TOKEN, opener=server, sleep=lambda s: None, min_interval=0)
    with pytest.raises(GitMcdError):
        client.call_tool("auto-bind-coupons", {})
    assert server.requests == []
    with pytest.raises(McdError) as err:
        client.call_tool("auto-bind-coupons", {}, allow_write=True)
    assert err.value.http_status == 429
    assert [r["method"] for r in server.requests].count("tools/call") == 1


def test_network_error_is_transport() -> None:
    client = McdMcpClient(TOKEN, opener=FakeServer([urllib.error.URLError("timed out")]), min_interval=0)
    with pytest.raises(McdError) as err:
        client.call_tool("order-list")
    assert err.value.transport


def test_truncated_response_is_transport_and_not_retried() -> None:
    server = FakeServer([lambda payload: TruncatedResponse(b"")])
    client = McdMcpClient(TOKEN, opener=server, min_interval=0)
    with pytest.raises(McdError) as err:
        client.call_tool("create-order", {"storeCode": "1"}, allow_write=True)
    assert err.value.transport and "IncompleteRead" in err.value.message
    assert [r["method"] for r in server.requests].count("tools/call") == 1


def test_mcp_url_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCD_MCP_URL", "https://attacker.example/mcp")
    server = FakeServer([RESULT])
    client = McdMcpClient(TOKEN, opener=server, min_interval=0)
    client.call_tool("now-time-info")
    assert client.url == DEFAULT_URL
    assert {r["url"] for r in server.requests} == {DEFAULT_URL}  # 带 Token 的请求一个都没发去别的主机


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example",
        "http://mcp.mcd.cn",
        "https://mcp.mcd.cn.attacker.example",
        "https://mcp.mcd.cn@attacker.example",
        "https://user:pw@mcp.mcd.cn",
        "https://mcp.mcd.cn:8443",
        "https://mcp.mcd.cn:bad",
    ],
)
def test_unofficial_url_is_refused_before_any_request(url: str) -> None:
    server = FakeServer([RESULT])
    with pytest.raises(GitMcdError) as err:
        McdMcpClient(TOKEN, url, opener=server, min_interval=0)
    assert err.value.fatal and TOKEN not in err.value.message
    assert server.requests == []


def test_official_url_variants_are_accepted() -> None:
    for url in ("https://mcp.mcd.cn", "https://mcp.mcd.cn/", "https://MCP.mcd.cn:443"):
        server = FakeServer([RESULT])
        McdMcpClient(TOKEN, url, opener=server, min_interval=0).call_tool("now-time-info")
        assert len(server.requests) == 3


# ---------------------------------------------------------------- 重定向（本地回环 http.server，不出本机）


class _Redirector(http.server.BaseHTTPRequestHandler):
    """/mcp 回 30x，Location 指到同一个本地服务的 /elsewhere；每个请求都记下路径、方法和请求头。"""

    def do_POST(self) -> None:
        self._handle()

    def do_GET(self) -> None:
        self._handle()

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        server: Any = self.server
        server.seen.append({"path": self.path, "method": self.command, "headers": {k.lower(): v for k, v in self.headers.items()}})
        if self.path == "/mcp":
            self.send_response(server.status)
            self.send_header("Location", f"http://127.0.0.1:{server.server_port}/elsewhere")
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: Any) -> None:  # 不往 stderr 打访问日志
        pass


@pytest.fixture
def redirector(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    server: Any = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
    server.seen, server.status = [], 302
    allow_loopback(monkeypatch, server.server_port)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _local_url(server: Any) -> str:
    return f"http://127.0.0.1:{server.server_port}/mcp"


def _post(server: Any) -> urllib.request.Request:
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    return urllib.request.Request(_local_url(server), data=b"{}", headers=headers, method="POST")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_default_opener_does_not_follow_redirects(redirector: Any, status: int) -> None:
    redirector.status = status
    with pytest.raises(urllib.error.HTTPError) as err:
        _default_opener(_post(redirector), 5)
    err.value.close()
    assert err.value.code == status
    assert [r["path"] for r in redirector.seen] == ["/mcp"]  # 没跟去 /elsewhere，Token 也就没被带过去


def test_stock_urllib_would_carry_the_token_along(redirector: Any) -> None:
    """对照：换成 urllib 默认的 handler 链，POST 的 302 会被改成 GET 跟过去，Authorization 原样带上。"""
    with urllib.request.build_opener().open(_post(redirector), timeout=5) as resp:
        resp.read()
    assert [(r["path"], r["method"]) for r in redirector.seen] == [("/mcp", "POST"), ("/elsewhere", "GET")]
    assert redirector.seen[1]["headers"]["authorization"] == f"Bearer {TOKEN}"


def test_redirect_reaches_the_client_as_http_error(redirector: Any) -> None:
    """经 McdMcpClient 发出：30x 变成 McdError(http_status=30x)，不算 transport；create-order 遇到它按结果未知处理。"""

    def to_local(request: urllib.request.Request, timeout: float) -> Any:
        # 客户端地址只能是 https://mcp.mcd.cn：把同一个请求（请求头、body 都不变）改发到本地服务，仍走默认 opener
        local = urllib.request.Request(_local_url(redirector), data=request.data, headers=dict(request.header_items()), method="POST")
        return _default_opener(local, timeout)

    client = McdMcpClient(TOKEN, opener=to_local, min_interval=0)
    with pytest.raises(McdError) as err:
        client.call_tool("now-time-info")
    assert err.value.http_status == 302 and not err.value.transport
    assert TOKEN not in err.value.message
    assert [(r["path"], r["headers"]["authorization"]) for r in redirector.seen] == [("/mcp", f"Bearer {TOKEN}")]
