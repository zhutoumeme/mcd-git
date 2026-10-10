"""写操作确认闸。

规则由代码强制，不靠 Agent 自觉：
1. 写操作（create-order / cancel-order / auto-bind-coupons）只能拿着 Approval 调用，Approval 只在这里签发，
   而且只对签发时的那个 tool 有效（McdApi 发请求前会核对）；
2. 非交互（Agent、管道、--json）时，push / revert / pull 只出确认摘要并把预览记进本地状态，不动账户；
3. 用户同意后用 --confirm <commit 哈希 / 订单号 / 可领券指纹> 执行：必须先有 10 分钟内的同一份预览，
   确认时还要重新核对一遍（push 重新算价、比下单参数指纹；pull 重新拉可领券列表、比列表指纹），任何一项对不上就拒绝；
4. 预览在真正发请求前就被消费掉，同一份确认不能用两次；网络超时等结果未知的情况不自动重试。
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Dict

from .errors import GitMcdError, McdError

PENDING_TTL = timedelta(minutes=10)

# JSON-RPC 错误里只有这四个说明请求本身没被受理（解析失败、请求无效、方法不存在、参数无效）；
# -32603 Internal error、-32000～-32099 这类是服务端处理时出了错，写操作可能已经生效
RPC_NOT_ACCEPTED = (-32700, -32600, -32601, -32602)

_ISSUER = object()


def fingerprint(args: Dict[str, Any]) -> str:
    blob = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def rejected(err: McdError) -> bool:
    """服务端明确拒绝、可以断定写操作没生效：信封里的业务错误码、HTTP 4xx，或 JSON-RPC 层没受理这次请求。
    其余（超时、响应不完整、30x、5xx、别的 JSON-RPC 错误、返回看不懂）都只能当结果未知。"""
    if err.transport:
        return False
    if err.rpc:
        return err.code in RPC_NOT_ACCEPTED
    return err.code not in (None, "") or 400 <= (err.http_status or 0) < 500


class Approval:
    """一次性许可：只对指定 tool + 指定参数有效，用一次就作废。"""

    __slots__ = ("tool", "fingerprint", "used")

    def __init__(self, issuer: object, tool: str, args: Dict[str, Any]) -> None:
        if issuer is not _ISSUER:
            raise RuntimeError("Approval 只能由 gate.issue() 签发")
        self.tool = tool
        self.fingerprint = fingerprint(args)
        self.used = False

    def check(self, tool: str, args: Dict[str, Any]) -> None:
        if self.used:
            raise GitMcdError("这份确认已经用过了，不能再用一次", fatal=True)
        if tool != self.tool or fingerprint(args) != self.fingerprint:
            raise GitMcdError("确认闸校验失败：要发出的参数和确认时不一致", fatal=True)
        self.used = True


def issue(tool: str, args: Dict[str, Any]) -> Approval:
    return Approval(_ISSUER, tool, args)
