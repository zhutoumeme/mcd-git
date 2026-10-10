"""写操作确认闸。

规则由代码强制，不靠 Agent 自觉：
1. 写操作（create-order / cancel-order）只能拿着 Approval 调用，Approval 只在这里签发；
2. 非交互（Agent、管道、--json）时，push / revert 只出确认摘要并把预览记进本地状态，不下单；
3. 用户同意后用 --confirm <commit 哈希 / 订单号> 执行：必须先有同一 commit、同一参数、10 分钟内的预览，
   而且重新算价后应付金额、下单参数的指纹都要和预览时一模一样，任何一项对不上就拒绝；
4. 预览在真正发请求前就被消费掉，同一份确认不能用两次；网络超时等结果未知的情况不自动重试。
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Dict

from .errors import GitMcdError

PENDING_TTL = timedelta(minutes=10)

_ISSUER = object()


def fingerprint(args: Dict[str, Any]) -> str:
    blob = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
            raise GitMcdError("这份确认已经用过了，不能再下一次单", fatal=True)
        if tool != self.tool or fingerprint(args) != self.fingerprint:
            raise GitMcdError("确认闸校验失败：要发出的参数和确认时不一致", fatal=True)
        self.used = True


def issue(tool: str, args: Dict[str, Any]) -> Approval:
    return Approval(_ISSUER, tool, args)
