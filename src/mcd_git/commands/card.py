"""git mcd card：1024 吃麦贡献图。终端里预览，同时在本机写一个自包含的 SVG。

只读：不改动账户。真实模式调一次 order-list（最近 10 单）刷新本机账本再出图，不轮询；格子来自本机账本，记得越久越全。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console
from rich.text import Text

from .. import render as R
from ..card import build, render_svg, render_terminal
from ..context import Context, Result
from ..errors import GitMcdError, McdError
from ..state import display_path

DEFAULT_OUTPUT = "mcd-contributions.svg"


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser(
        "card",
        parents=[common],
        help="1024 吃麦贡献图：终端预览 + 本机 SVG",
        description=(
            "把最近 53 周的吃麦记录画成 GitHub 风格的贡献图：终端里预览，同时在本机写一个 SVG。"
            "只读，不改动账户；会在本机写一个 SVG 文件；真实模式调一次 order-list 刷新本机账本。"
        ),
    )
    p.add_argument("-o", "--output", default=DEFAULT_OUTPUT, metavar="<文件>", help=f"SVG 写到哪里（默认当前目录的 {DEFAULT_OUTPUT}，同名直接覆盖）")
    p.add_argument("--with-savings", action="store_true", help="卡片上加一项「用券省下 ¥X」（默认不出现任何金额）")
    p.set_defaults(func=run)


def _stats(model: Dict[str, Any], with_savings: bool) -> Dict[str, Any]:
    out = {
        "total": model["total"],
        "activeDays": model["activeDays"],
        "topItem": model["topItem"],
        "topSlot": model["topSlot"],
        "earliest": model["earliest"],
        "window": model["window"],
    }
    if with_savings:
        out["savings"] = model["savings"]
        out["savingsKnownOrders"] = model["savingsKnownOrders"]  # order-list 不给优惠明细：只有 push / show 过的单知道省了多少
    return out


def run(ctx: Context, args: Any) -> Result:
    warning: Optional[str] = None
    failure: Optional[McdError] = None
    try:
        ctx.order_list()  # 刷一次本机账本；只调这一次，不轮询
    except McdError as err:
        failure, warning = err, f"order-list 查询失败，只用本机记录：{err.message}"
    model = build(ctx.ledger.entries(), today=ctx.now.date(), with_savings=args.with_savings, demo=ctx.demo)
    stats = _stats(model, args.with_savings)

    if model["total"] == 0:
        if failure is not None:
            raise GitMcdError(
                f"order-list 查询失败，本机也还没有吃麦记录：{failure.message}",
                remote=failure.remote_lines(),
                hint=["稍后再试", "先试试 git mcd --demo card 看看效果"],
            )

        def draw_empty(console: Console) -> None:
            console.print("还没有吃麦记录：最近 10 单里没有，本机也没记过")
            R.hints(console, ["先试试 git mcd --demo card 看看效果"])

        return Result({"status": "empty", "path": None, "bytes": 0, "stats": stats, "days": {}, "warning": warning}, draw_empty)

    raw = str(args.output)
    path = Path(raw).expanduser()
    shown = display_path(path) if path.is_absolute() else raw
    if not path.parent.is_dir():
        raise GitMcdError(f"写不了 {shown}：目录不存在", fatal=True, hint="先建好目录，或者用 -o 换个地方")
    payload = render_svg(model).encode("utf-8")
    try:
        path.write_bytes(payload)
    except OSError as err:
        raise GitMcdError(f"写不了 {shown}：{err.strerror or err.__class__.__name__}", fatal=True) from None
    size = len(payload)

    def draw(console: Console) -> None:
        if warning:
            console.print(Text.assemble(("warning: ", "yellow"), warning))
        render_terminal(console, model)
        console.print()
        note = f"{size / 1024:.1f} KB" + ("，demo 合成数据" if ctx.demo else "")
        console.print(Text.assemble("已写入 ", (shown, "bold"), f"（{note}）"))
        R.hints(
            console,
            [
                "贴到 GitHub 个人主页：本机生成后手动提交到 <用户名>/<用户名> 仓库，见 README「1024 特别版」",
                "别放进 GitHub Actions 定时跑：Token 不能进 CI，MCP 规则也不允许自动化调用",
            ],
        )

    return Result({"status": "written", "path": shown, "bytes": size, "stats": stats, "days": model["days"], "warning": warning}, draw)
