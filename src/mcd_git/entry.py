"""console script 入口：`__complete` 走轻量热路径，其余命令才导入完整的 cli（rich、子命令、MCP 客户端）。

import cli 本身就要 ~110ms，每按一次 TAB 都付这笔开销太慢，所以先看第一个参数再决定导入哪个模块。"""
from __future__ import annotations

import os
import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["__complete"]:
        from .complete import run

        return run(argv[1:], env=os.environ, out=sys.stdout)
    from .cli import main as cli_main

    return cli_main()
