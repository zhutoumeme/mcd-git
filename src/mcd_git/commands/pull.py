"""git mcd pull：领券。像 git pull 一样 fetch + merge：先用 available-coupons 列出麦麦省里可领的券，
用户确认后调一次 auto-bind-coupons，把它们全部领进麦当劳账户。

auto-bind-coupons 没有参数：一次领取当前所有可领券，不能只领其中几张。确认闸和 push 一样（规则见 gate.py）：
交互终端先出摘要再问 y/N；非交互（Agent、管道、--json）只出摘要，用户同意后加 --confirm <指纹> 执行。
指纹由可领券列表算出；确认时重新拉一次列表，变了就拒绝。结果未知不重试。
"""
from __future__ import annotations

import argparse
import re
import shlex
from typing import Any, Dict, List

from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from .. import gate
from .. import render as R
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..state import fmt_time, parse_time

TOOL = "auto-bind-coupons"
SOURCE = "mcd.cn:麦麦省"
NOTE = "auto-bind-coupons 一次领取当前所有可领券，不能只领其中几张"
REJECTED_HINT = "什么都没领；处理完上面的问题后重新 git mcd pull 预览"
UNKNOWN_RESULT_HINT = "不确定券有没有领到：先运行 git mcd status 或 git mcd fetch 核对，不要直接重试"
REBASE_HINT = "下单前跑一次 git mcd rebase，会自动挑最省的那张券"
NO_NAME = "（接口没返回券名）"


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser(
        "pull",
        parents=[common],
        help="领券：一次领取全部可领券（会先确认）",
        description=(
            "像 git pull 一样 fetch + merge：先查麦麦省里可领的券（available-coupons），"
            "确认后调一次 auto-bind-coupons 把它们领进你的麦当劳账户。接口一次领取全部可领券，不能只领其中几张。"
            "终端里会先给摘要并问 y/N；非交互（Agent / 管道 / --json）只出摘要，用户明确同意后再加 --confirm <指纹> 执行。"
        ),
    )
    p.add_argument("--dry-run", action="store_true", help="只看有哪些券可领，不领")
    p.add_argument("--confirm", metavar="<指纹>", help="用户已同意：按预览里给的指纹领取")
    p.add_argument("-f", "--force", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=run)


# ---------------------------------------------------------------- 可领券


def _claimable(ctx: Context) -> List[Dict[str, Any]]:
    """available-coupons 里还能领的券（和 fetch 同一个口径）。查询本身失败时什么都没领。"""
    try:
        coupons = ctx.api().available_coupons()
    except McdError as err:
        raise GitMcdError(f"查可领券失败：{err.message}", remote=err.remote_lines(), hint="什么都没领") from None
    return [c for c in coupons if c.get("status") in (None, "CAN_GET")]


def _fingerprint(coupons: List[Dict[str, Any]]) -> str:
    """可领券没有 couponId，同名券还会重复：按（名称、状态、标签）的多重集合稳定排序后取指纹，同一份列表永远是同一个指纹。"""
    rows = sorted([str(c.get("name") or ""), str(c.get("status") or ""), str(c.get("label") or "")] for c in coupons)
    return gate.fingerprint({"tool": TOOL, "coupons": rows})


def _short_name(name: str) -> str:
    """coupons/<短名>：按「·」切开取最后一段，去掉空白，最多 16 格。"""
    parts = [re.sub(r"\s+", "", part) for part in str(name or "").split("·")]
    tail = next((part for part in reversed(parts) if part), "")
    return R.clip(tail, 16) if tail else "coupon"


def _rows(coupons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"name": c["name"], "short": _short_name(c["name"]), "label": c.get("label") or ""} for c in coupons]


def confirm_command(fp7: str, *, demo: bool = False, json_mode: bool = False) -> str:
    """确认后要运行的命令：demo / json 模式原样带上，照抄不会误领到真实账户。"""
    parts = ["git", "mcd"] + (["--demo"] if demo else []) + ["pull", "--confirm", fp7] + (["--json"] if json_mode else [])
    return " ".join(shlex.quote(p) for p in parts)


def render_summary(console: Console, coupons: List[Dict[str, Any]], *, demo: bool) -> None:
    names = Text()
    for i, c in enumerate(coupons):
        if i:
            names.append("\n")
        names.append(c["name"] or NO_NAME)
        if c.get("label") and c["label"] != "可领取":
            names.append(f"  {c['label']}", style=R.DIM)
    title = f"即将 pull：一次领取全部 {len(coupons)} 张可领券" + ("（demo）" if demo else "")
    body = Group(names, Text(""), Text(NOTE, style=R.DIM))
    console.print(Panel(body, title=Text(title), title_align="left", border_style="yellow", expand=False))


# ---------------------------------------------------------------- 领券


def _bind(ctx: Context) -> Result:
    approval = gate.issue(TOOL, {})
    ctx.state["pending_pull"] = None  # 先把预览消费掉：同一份确认只能用一次
    ctx.save()
    try:
        result = ctx.api().auto_bind_coupons(approval)
    except GitMcdError:
        raise  # 确认闸校验、调用上限：请求还没发出去
    except McdError as err:
        # 只有服务端明确拒绝（见 gate.rejected）才能断定没领；超时、30x、5xx、-32603 这类 JSON-RPC 错误、返回看不懂都当结果未知
        if gate.rejected(err):
            raise GitMcdError(f"pull 失败：{err.message}", remote=err.remote_lines(), hint=REJECTED_HINT) from None
        raise GitMcdError(f"pull 结果未知：{err.message}", remote=err.remote_lines(), hint=UNKNOWN_RESULT_HINT) from None
    except KeyboardInterrupt:
        raise GitMcdError("pull 结果未知：已中断，没等到领券结果", hint=UNKNOWN_RESULT_HINT, exit_code=130) from None
    except Exception as err:  # noqa: BLE001（请求可能已经发出去了，不能让 traceback 盖住「先核对」的提示）
        raise GitMcdError(f"pull 结果未知：没拿到领券结果（{err.__class__.__name__}）", hint=UNKNOWN_RESULT_HINT) from None

    claimed = [{"name": c["name"], "short": _short_name(c["name"])} for c in result["success"]]
    failed = [{"name": c["name"], "short": _short_name(c["name"]), "reason": c["reason"]} for c in result["failed"]]
    got, lost = result["successCount"], result["failedCount"]
    data: Dict[str, Any] = {"successCount": got, "failedCount": lost, "totalCount": result["totalCount"], "claimed": claimed, "failed": failed}

    if got == 0 and lost == 0:
        note = "服务端这次没有发券，可能刚刚已经领过"
        return Result(dict(data, status="up-to-date", note=note), lambda console: console.print(f"Already up to date.（{note}）"))
    if got == 0:
        # 一张都没领到：服务端明确说了失败，按 error 退出（JSON 里 ok 为 false），原因按 remote: 行列出来
        reasons = [f"{c['name'] or NO_NAME}：{c['reason'] or '没给原因'}" for c in failed]
        raise GitMcdError(
            f"pull 失败：{lost} 张券一张都没领到",
            remote=reasons,
            hint="git mcd fetch 看看现在还有哪些可领的券",
            detail=dict(data, code="nothing_claimed"),
        )

    width = min(max((cell_len(c["name"] or NO_NAME) for c in claimed + failed), default=0), 32)

    def draw(console: Console) -> None:
        console.print(f"From {SOURCE}")
        for c in claimed:
            console.print(Text.assemble(" * ", ("[new coupon]", R.ADD), "      ", R.pad(c["name"] or NO_NAME, width), " -> ", (f"coupons/{c['short']}", R.BRANCH)))
        if got > len(claimed):
            console.print(Text.assemble(" * ", ("[new coupon]", R.ADD), "      ", f"{NO_NAME}× {got - len(claimed)}"))
        for c in failed:
            reason = f"（{c['reason']}）" if c["reason"] else ""
            console.print(Text.assemble(" ! ", ("[rejected]", R.DEL), "        ", R.pad(c["name"] or NO_NAME, width), " -> ", (f"coupons/{c['short']}", R.BRANCH), reason))
        if lost > len(failed):
            console.print(Text.assemble(" ! ", ("[rejected]", R.DEL), "        ", f"{NO_NAME}× {lost - len(failed)}"))
        console.print("Fast-forward")
        console.print(f" 领到 {got} 张，失败 {lost} 张")
        R.hints(console, [REBASE_HINT])

    return Result(dict(data, status="pulled", next="下单前 git mcd rebase 会自动挑最省的券"), draw)


# ---------------------------------------------------------------- 入口


def run(ctx: Context, args: Any) -> Result:
    if args.force:
        raise GitMcdError("--force 不支持：券是领的，不是抢的", fatal=True, hint="auto-bind-coupons 本来就一次领全部，force 也拉不出更多券")
    if args.dry_run and args.confirm:
        raise GitMcdError("--dry-run 和 --confirm 不能一起用")
    state = ctx.state
    pending = state.get("pending_pull") or {}

    if args.confirm:
        confirm = args.confirm.strip().lower()
        if len(confirm) < 7 or not re.fullmatch(r"[0-9a-f]+", confirm):
            raise GitMcdError("--confirm 要填预览里给的指纹（至少 7 位）")
        if not pending.get("fingerprint"):
            raise GitMcdError(
                "还没有给用户看过这次领券的确认摘要",
                hint=["先不带 --confirm 运行一次 git mcd pull，把摘要给用户看", "用户明确同意后，再加 --confirm <指纹> 执行"],
            )
        if not str(pending["fingerprint"]).startswith(confirm):
            raise GitMcdError(f"--confirm {confirm} 和预览时给的指纹对不上", hint="照抄预览里给的命令；或者重新运行 git mcd pull 预览")
        previewed_at = parse_time(pending.get("time"))
        if previewed_at is None or ctx.now - previewed_at > gate.PENDING_TTL:
            state["pending_pull"] = None
            ctx.save()
            raise GitMcdError("确认已过期（预览超过 10 分钟）", hint="重新运行 git mcd pull 预览，再让用户确认一次")

    coupons = _claimable(ctx)  # 确认时也重新拉一次：列表变了就不能按旧的确认领
    if not coupons:
        if state.get("pending_pull") is not None:
            state["pending_pull"] = None  # 已经没有可领的券了，旧预览作废
            ctx.save()
        return Result({"status": "up-to-date", "claimable": 0}, lambda console: console.print("Already up to date.（没有可领的券）"))

    fingerprint = _fingerprint(coupons)
    count = len(coupons)
    summary = {"count": count, "coupons": _rows(coupons), "note": f"会一次领取全部 {count} 张券；{NOTE}"}

    if args.confirm:
        if fingerprint != pending.get("fingerprint"):
            state["pending_pull"] = None
            ctx.save()
            before = pending.get("count")
            change = f"预览时 {before} 张，现在 {count} 张" if before != count else f"还是 {count} 张，但券不一样了"
            raise GitMcdError(f"可领券列表变了（{change}），什么都没领", hint="重新运行 git mcd pull 预览，再让用户确认一次")
        return _bind(ctx)

    state["pending_pull"] = {"fingerprint": fingerprint, "count": count, "time": fmt_time(ctx.now)}
    ctx.save()
    command = confirm_command(fingerprint[:7], demo=ctx.demo, json_mode=ctx.json)
    tell = f"会把这 {count} 张券一次全部领进你的麦当劳账户（接口不支持只领其中几张）"

    if ctx.interactive and not args.dry_run:
        render_summary(ctx.console, coupons, demo=ctx.demo)
        answer = ctx.ask(f"确认领券？{tell}[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            state["pending_pull"] = None
            ctx.save()
            return Result({"status": "aborted", "summary": summary}, lambda console: console.print("没有领券。"))
        return _bind(ctx)

    def draw_preview(console: Console) -> None:
        render_summary(console, coupons, demo=ctx.demo)
        console.print(f"这一步{tell}。")
        console.print(Text.assemble("用户确认无误后运行：", (command, "bold")))

    return Result(
        {
            "status": "confirmation_required",
            "confirm": fingerprint[:7],
            "summary": summary,
            "next": command,
            "expiresInMinutes": int(gate.PENDING_TTL.total_seconds() // 60),
        },
        draw_preview,
    )
