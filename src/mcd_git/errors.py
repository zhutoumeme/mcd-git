"""两类异常：CLI 层的可预期错误（按 git 风格输出），和 MCP 返回的业务 / 传输错误。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union


class GitMcdError(Exception):
    """可预期的错误。fatal=True 输出 `fatal:`（退出码 128），否则输出 `error:`（退出码 1）。"""

    def __init__(
        self,
        message: str,
        *,
        hint: Union[str, List[str], None] = None,
        fatal: bool = False,
        exit_code: Optional[int] = None,
        remote: Optional[List[str]] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if isinstance(hint, str):
            hint = [hint]
        self.hints: List[str] = list(hint or [])
        self.fatal = fatal
        self.exit_code = exit_code if exit_code is not None else (128 if fatal else 1)
        self.remote: List[str] = list(remote or [])
        self.detail: Dict[str, Any] = dict(detail or {})


class UsageError(GitMcdError):
    """参数错误（git 用 129 表示用法错误）。"""

    def __init__(self, message: str, usage: str = "") -> None:
        super().__init__(message, exit_code=129)
        self.usage = usage


class McdError(Exception):
    """MCP 调用失败：业务失败（success=false / code≠200）、HTTP 错误、JSON-RPC 错误或网络错误。

    transport=True 表示请求结果未知（超时、断网），写操作遇到这种情况绝不自动重试。
    rpc=True 表示 JSON-RPC 层的错误：code 是 JSON-RPC 错误码（如 -32603），不是信封里的业务错误码。
    """

    def __init__(
        self,
        message: str,
        *,
        code: Any = None,
        trace_id: Optional[str] = None,
        http_status: Optional[int] = None,
        transport: bool = False,
        rpc: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.trace_id = trace_id
        self.http_status = http_status
        self.transport = transport
        self.rpc = rpc

    def remote_lines(self) -> List[str]:
        """按 git 的 `remote:` 行展示服务端原话、错误码和 traceId。"""
        tail = []
        if self.code not in (None, ""):
            tail.append(f"code {self.code}")
        if self.http_status:
            tail.append(f"HTTP {self.http_status}")
        if self.trace_id:
            tail.append(f"trace {self.trace_id}")
        suffix = f"（{'，'.join(tail)}）" if tail else ""
        return [f"{self.message}{suffix}"]
