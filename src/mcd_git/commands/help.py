"""git mcd help / git mcd -h / 不带参数：git 命令 ↔ 点餐动作对照表。

注意：`git mcd --help` 会被 git 自己拦截成 `git help mcd` 去找 man 手册，到不了这里，
所以对照表放在 `git mcd help`、`git mcd -h` 和不带参数的 `git mcd` 里。
"""
from __future__ import annotations

from typing import Any, List, Tuple

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .. import __version__
from .. import render as R
from ..context import Result

COMMANDS: List[Tuple[str, str, bool]] = [
    ("init", "选门店和取餐方式（= 设置远端）", False),
    ("status", "看购物车、可用券、待支付订单", False),
    ("ls-remote [关键词]", "看本店菜单", False),
    ("add <餐品>", "放进购物车，--no-pickle 去酸黄瓜", False),
    ("restore --staged <餐品>", "从购物车拿出来", False),
    ("reset [ORIG_HEAD | --soft HEAD~1]", "清空购物车 / 撤销 rebase / 撤回 commit", False),
    ("diff", "原价 vs 应付，逐行看差价", False),
    ("rebase", "自动凑套餐、挑最省的券", False),
    ("commit -m <说明>", "锁定这一单（本地快照 + 核价）", False),
    ("push", "真正下单，出支付二维码", True),
    ("log --graph", "吃麦提交历史", False),
    ("log --stat", "吃麦统计：餐品、实付、省了多少、按时段分布", False),
    ("show [HEAD]", "订单详情、取餐码", False),
    ("cherry-pick HEAD", "再来一单（拣回购物车）", False),
    ("revert", "取消订单", True),
    ("fetch", "今日活动、可领券（只读）", False),
    ("pull", "领券：一次领全部可领券", True),
    ("card [--style 样式]", "1024 吃麦贡献图（天际线 / 小票 / 格子）", False),
]


def render_table(console: Console) -> None:
    console.print(Text.assemble("usage: ", ("git mcd", "bold"), " [--demo] [--json] <command> [<args>]"))
    console.print()
    table = Table(box=None, show_header=True, header_style="bold", padding=(0, 2, 0, 0))
    table.add_column("git mcd", style=R.BRANCH, no_wrap=True)
    table.add_column("点餐动作")
    table.add_column("动账户？", no_wrap=True)
    for command, action, writes in COMMANDS:
        table.add_row(command, action, Text("✎ 会，先确认", style="bold yellow") if writes else Text("否", style=R.DIM))
    console.print(table)
    console.print()
    console.print("全局选项：--demo 用合成数据（不需要 Token，不会真的下单）；--json 输出 JSON（给 Agent 和脚本用）", style=R.DIM)
    console.print("子命令帮助：git mcd <command> -h　·　对照表：git mcd help", style=R.DIM)
    console.print(Text.assemble(("试试：", R.DIM), ("git mcd --demo status", "bold"), (f"　　mcd-git {__version__} · 非麦当劳官方产品", R.DIM)))


def table_data() -> dict:
    return {
        "usage": "git mcd [--demo] [--json] <command> [<args>]",
        "commands": [{"command": c, "action": a, "writesAccount": w} for c, a, w in COMMANDS],
        "version": __version__,
    }


def run(ctx: Any, args: Any) -> Result:
    return Result(table_data(), render_table)
