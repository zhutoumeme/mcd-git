"""麦当劳 MCP Server 客户端：Streamable HTTP + JSON-RPC，只用标准库。

- Token 读取顺序：环境变量 MCD_MCP_TOKEN（所有平台最高优先级）→ 配置文件（择一存在且非空）：
  $XDG_CONFIG_HOME/mcd-mcp/token（若设）→ Windows 的 %APPDATA%/mcd-mcp/token（若设）→
  ~/.config/mcd-mcp/token（所有平台保留，向后兼容）。Token 只放进请求头，
  不打印、不写日志、不出现在异常信息和 repr 里。
- 地址写死为 https://mcp.mcd.cn，不读任何环境变量；别的协议或主机在发请求前就拒绝，也不跟随重定向，Token 不会被带去别处。
- 写操作 tool（下单、取消、领券……）默认拒绝；只有 push / revert / pull 的确认闸会带 allow_write=True，
  而且只放行 GATED_WRITE_TOOLS 里的三个，其余写操作带着 allow_write 也调不出去。
- 返回解包优先用 structuredContent，缺失时才从 Markdown 文本里找 JSON 信封（三种布局都兼容）。
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

from . import __version__
from .errors import GitMcdError, McdError

DEFAULT_URL = "https://mcp.mcd.cn"
OFFICIAL_HOST = "mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"

# 会改变账户状态的 tool。mcd-git 只会经确认闸调用前三个，其余永远不调。
WRITE_TOOLS = frozenset(
    {
        "create-order",
        "cancel-order",
        "auto-bind-coupons",
        "mall-create-order",
        "draw-lottery",
        "party-order-create",
        "delivery-create-address",
    }
)
# 确认闸能放行的写操作：push → create-order、revert → cancel-order、pull → auto-bind-coupons
GATED_WRITE_TOOLS = frozenset({"create-order", "cancel-order", "auto-bind-coupons"})


def write_blocked(name: str, allow_write: bool) -> bool:
    """写操作没有确认闸放行（allow_write）就拒绝；不在 GATED_WRITE_TOOLS 里的写操作一律拒绝。"""
    return name in WRITE_TOOLS and (not allow_write or name not in GATED_WRITE_TOOLS)


WRITE_BLOCKED = "拒绝调用 {name}：写操作只能经过 push / revert / pull 的确认闸"

_ENVELOPE_RE = re.compile(r'\{\s*"success"\s*:')
_MARKER = "## Original Response"


def token_file(env: Optional[Dict[str, str]] = None) -> Path:
    """提示里给用户「该把 Token 写到哪」的单一推荐路径（跨平台）。

    Windows 且设了 %APPDATA% → %APPDATA%/mcd-mcp/token；没有 APPDATA 退回 ~/.config/mcd-mcp/token。
    其它平台 → ~/.config/mcd-mcp/token。
    """
    from .state import _is_windows

    env = os.environ if env is None else env
    if _is_windows():
        appdata = (env.get("APPDATA") or "").strip()
        if appdata:
            return Path(appdata) / "mcd-mcp" / "token"
    return Path.home() / ".config" / "mcd-mcp" / "token"


def _token_candidates(env: Dict[str, str]) -> List[Path]:
    """按优先级给出要尝试的 Token 文件：$XDG_CONFIG_HOME → (Windows)%APPDATA% → ~/.config，去重保序。"""
    from .state import _is_windows

    candidates: List[Path] = []
    xdg = (env.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        candidates.append(Path(xdg).expanduser() / "mcd-mcp" / "token")
    if _is_windows():
        appdata = (env.get("APPDATA") or "").strip()
        if appdata:
            candidates.append(Path(appdata) / "mcd-mcp" / "token")
    candidates.append(Path.home() / ".config" / "mcd-mcp" / "token")
    seen = set()
    unique: List[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _token_hint(env: Dict[str, str]) -> List[str]:
    """Token 缺失时的报错 hint，按平台给命令；写文件路径用 token_file() 实际解析出的路径。"""
    from .state import _is_windows

    path = token_file(env)
    hint = ["在 open.mcd.cn 控制台申请 Token，然后二选一："]
    if _is_windows():
        hint.append('  setx MCD_MCP_TOKEN "你的Token"      （新开终端生效）')
        hint.append('  当前终端：$env:MCD_MCP_TOKEN="你的Token"')
        hint.append("  或把 Token 写进 %s" % path)
    else:
        hint.append("  export MCD_MCP_TOKEN=...            （只在当前终端有效）")
        from .state import display_path

        hint.append("  写进 %s 并 chmod 600" % display_path(path))
    hint.append("只是想试试？加 --demo，用合成数据，不需要 Token")
    return hint


def load_token(env: Optional[Dict[str, str]] = None) -> str:
    """按 MCD_MCP_TOKEN → 配置文件（$XDG_CONFIG_HOME → (Windows)%APPDATA% → ~/.config）的顺序读取，
    「存在且非空即用」命中第一个；找不到就报 fatal（不回显任何内容）。"""
    env = os.environ if env is None else env
    token = (env.get("MCD_MCP_TOKEN") or "").strip()
    if token:
        return token
    for path in _token_candidates(env):
        try:
            if path.is_file():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    return content
        except OSError:
            continue
    raise GitMcdError(
        "没有找到麦当劳 MCP Token",
        fatal=True,
        hint=_token_hint(env),
    )


# ---------------------------------------------------------------- 返回解包


def _result_text(result: Dict[str, Any]) -> str:
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def _envelope_from_text(text: str) -> Optional[Dict[str, Any]]:
    """从 Markdown 里找第一个 {"success": ...} 信封；找不到再试 `## Original Response` 后的第一个 JSON。"""
    decoder = json.JSONDecoder()
    for match in _ENVELOPE_RE.finditer(text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict) and "success" in value:
            return value
    marker = text.find(_MARKER)
    if marker >= 0:
        start = text.find("{", marker)
        while start >= 0:
            try:
                value, _ = decoder.raw_decode(text, start)
            except ValueError:
                start = text.find("{", start + 1)
                continue
            if isinstance(value, dict):
                return value
            break
    return None


def parse_envelope(result: Any) -> Dict[str, Any]:
    """tools/call 的 result → 校验过的信封 dict；业务失败抛 McdError（isError 恒为 false，要看 success/code）。"""
    if not isinstance(result, dict):
        raise McdError("无法解析 MCP 返回")
    env = result.get("structuredContent")
    if not isinstance(env, dict):
        env = _envelope_from_text(_result_text(result))
    if not isinstance(env, dict):
        if result.get("isError"):
            first = _result_text(result).strip().splitlines()
            raise McdError((first[0][:200] if first else "") or "MCP 调用失败")
        # 不把整段文本带进异常，避免把个人信息打进日志
        raise McdError("无法解析 MCP 返回（没有 structuredContent，文本里也没有 JSON）")
    code = env.get("code")
    if env.get("success") is False or code not in (200, "200", None):
        raise McdError(str(env.get("message") or "请求失败"), code=code, trace_id=env.get("traceId"))
    return env


def unwrap(result: Any) -> Any:
    """只要 data。"""
    return parse_envelope(result).get("data")


# ---------------------------------------------------------------- 传输层


def iter_sse(lines: Iterable[bytes]) -> Iterator[Dict[str, Any]]:
    """SSE 字节行 → JSON 消息（支持多行 data）。"""
    data = []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                yield json.loads("\n".join(data))
                data = []
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        yield json.loads("\n".join(data))


def check_url(url: str) -> str:
    """请求头里带着 Token，所以只允许 https://mcp.mcd.cn（默认端口、不带用户名密码），否则直接 fatal。"""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        parts, port = None, -1
    if (
        parts is None
        or parts.scheme != "https"
        or (parts.hostname or "").lower() != OFFICIAL_HOST
        or port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
    ):
        raise GitMcdError(f"拒绝连接非官方的 MCP 地址：Token 只发给 {DEFAULT_URL}", fatal=True)
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向。urllib 默认会把 POST 的 301/302/303 改成 GET 跟去任意主机，Authorization 头也原样带过去；
    redirect_request 返回 None，30x 就落到默认的错误处理，以 HTTPError 抛出（再由 _http_error 转成 McdError）。"""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


# 默认 handler 链（代理、HTTPS 证书校验都和 urlopen 一样），只把重定向处理换成 _NoRedirect
_OPENER = urllib.request.build_opener(_NoRedirect)


def _default_opener(request: urllib.request.Request, timeout: float):
    return _OPENER.open(request, timeout=timeout)  # noqa: S310（地址经 check_url 校验，只会是 https://mcp.mcd.cn；不跟随重定向）


class McdMcpClient:
    """一个进程 initialize 一次；服务端无状态（实测不返回 Mcp-Session-Id），但仍兼容会话头和 SSE。"""

    def __init__(
        self,
        token: str,
        url: Optional[str] = None,
        *,
        timeout: float = 30.0,
        opener: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
        retries_429: int = 2,
        min_interval: float = 0.1,
    ) -> None:
        if not token:
            raise ValueError("token 为空")
        self.__token = token
        # 只有构造参数能改地址（留给测试），而且同样要过 check_url；不读环境变量
        self.url = check_url(url or DEFAULT_URL)
        self.timeout = timeout
        self._opener = opener or _default_opener
        self._sleep = sleep
        self._retries_429 = retries_429
        self._min_interval = min_interval
        self._last_sent = 0.0
        self._session_id: Optional[str] = None
        self._protocol: Optional[str] = None
        self._initialized = False
        self._next_id = 0

    def __repr__(self) -> str:  # 不暴露 token
        return f"<McdMcpClient {self.url}>"

    # -- HTTP

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.__token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"mcd-git/{__version__}",
        }
        if self._protocol:
            headers["MCP-Protocol-Version"] = self._protocol
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _scrub(self, text: str) -> str:
        return text.replace(self.__token, "***") if self.__token else text

    def _http_error(self, err: urllib.error.HTTPError) -> McdError:
        status = err.code
        if status == 401:
            return McdError("Token 无效或已过期", http_status=401)
        if status == 429:
            return McdError("请求太频繁，被限流了，过一会儿再试", http_status=429)
        try:
            detail = err.read(300).decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            detail = ""
        detail = self._scrub(detail.splitlines()[0][:120]) if detail else ""
        return McdError(f"服务端返回 HTTP {status}" + (f"：{detail}" if detail else ""), http_status=status)

    def _send(self, payload: Dict[str, Any], *, retry_429: bool) -> Optional[Dict[str, Any]]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        attempt = 0
        while True:
            wait = self._min_interval - (time.monotonic() - self._last_sent)
            if wait > 0:
                self._sleep(wait)
            self._last_sent = time.monotonic()
            request = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
            try:
                resp = self._opener(request, self.timeout)
                with resp:
                    return self._read_message(resp, payload.get("id"))
            except urllib.error.HTTPError as err:
                if err.code == 429 and retry_429 and attempt < self._retries_429:
                    self._sleep(float(2**attempt))  # 指数退避：1s、2s
                    attempt += 1
                    continue
                raise self._http_error(err) from None
            except McdError:
                raise
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as err:
                reason = getattr(err, "reason", None) or err.__class__.__name__
                raise McdError(f"网络错误：{self._scrub(str(reason))}", transport=True) from None
            except http.client.HTTPException as err:
                # 响应被截断（IncompleteRead）、BadStatusLine、LineTooLong……请求可能已经送达，结果未知
                raise McdError(f"网络错误：响应不完整（{err.__class__.__name__}）", transport=True) from None
            except ValueError:
                raise McdError("服务端返回的不是合法 JSON", transport=True) from None

    def _read_message(self, resp: Any, rid: Any) -> Optional[Dict[str, Any]]:
        headers = getattr(resp, "headers", None) or {}
        session_id = headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        if rid is None:  # notification：服务端回 202 或空体
            resp.read()
            return None
        content_type = headers.get("Content-Type") or ""
        if "text/event-stream" in content_type:
            # 拿到对应 id 的消息就停，避免服务端不关流时卡住
            for message in iter_sse(resp):
                if isinstance(message, dict) and message.get("id") == rid:
                    return message
            raise McdError("服务端没有返回响应", transport=True)
        raw = resp.read().decode("utf-8")
        if not raw.strip():
            raise McdError("服务端返回了空响应", transport=True)
        message = json.loads(raw)
        if isinstance(message, list):
            message = next((m for m in message if isinstance(m, dict) and m.get("id") == rid), None)
        if not isinstance(message, dict):
            raise McdError("服务端没有返回响应", transport=True)
        return message

    # -- JSON-RPC

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, *, retry_429: bool = True) -> Dict[str, Any]:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        message = self._send(payload, retry_429=retry_429) or {}
        if "error" in message:
            err = message.get("error") or {}
            raise McdError(f"{method}：{err.get('message') or '调用失败'}", code=err.get("code"), rpc=True)
        return message.get("result") or {}

    def initialize(self) -> Dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-git", "version": __version__},
            },
        )
        self._protocol = result.get("protocolVersion") or PROTOCOL_VERSION
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, retry_429=True)
        self._initialized = True
        return result

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, *, allow_write: bool = False) -> Dict[str, Any]:
        if write_blocked(name, allow_write):
            raise GitMcdError(WRITE_BLOCKED.format(name=name), fatal=True)
        if not self._initialized:
            self.initialize()
        # 写操作不做任何自动重试，防止重复下单 / 重复领券
        return self.request("tools/call", {"name": name, "arguments": arguments or {}}, retry_429=name not in WRITE_TOOLS)
