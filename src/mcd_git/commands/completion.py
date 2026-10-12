"""git mcd completion <shell>：把 bash / zsh / fish 补全脚本打印到标准输出。"""
from __future__ import annotations

from typing import Any

from rich.console import Console

from ..context import Context, Result


def register(sub: Any, common: Any) -> None:
    p = sub.add_parser(
        "completion",
        parents=[common],
        help="打印命令补全脚本（bash / zsh / fish）",
        description="把 shell 补全脚本打印到标准输出，写进 shell 配置一次即可。详见 README「命令补全」。",
    )
    p.add_argument("shell", choices=("bash", "zsh", "fish"), metavar="<shell>", help="bash / zsh / fish")
    p.set_defaults(func=run)


def run(ctx: Context, args: Any) -> Result:
    from .. import completion

    script = completion.render(args.shell)

    def show(console: Console) -> None:
        # 原样写：console.print 会按终端宽度折行、吞方括号，脚本就坏了
        console.file.write(script)
        console.file.flush()

    return Result({"shell": args.shell, "script": script}, render=show)
