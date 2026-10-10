"""命令行入口：`git-mcd`（放进 PATH 后就是 `git mcd`）和别名 `mcd-git`。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional

from rich.console import Console
from rich.text import Text

from . import __version__
from . import render as R
from .commands import card, cherry_pick, commit, diff, fetch, init, log, push, rebase, revert, stage, status
from .commands import help as help_cmd
from .context import Context
from .errors import GitMcdError, McdError, UsageError

_TRUE = ("1", "true", "yes", "on")


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message, usage=self.format_usage())


def build_parser() -> Any:
    common = Parser(add_help=False)
    common.add_argument("--demo", action="store_true", default=argparse.SUPPRESS, help="用合成数据（不需要 Token，不会真的下单）")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="输出 JSON（给 Agent 和脚本用）")

    parser = Parser(prog="git mcd", add_help=False, description="用 git 命令点麦当劳（非官方）。")
    parser.add_argument("-h", "--help", action="store_true", dest="show_help")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", metavar="<command>", parser_class=Parser)
    p = sub.add_parser("help", parents=[common], help="git 命令 ↔ 点餐动作对照表")
    p.add_argument("topic", nargs="?", metavar="<command>")
    for module in (init, status, stage, diff, rebase, commit, push, log, cherry_pick, revert, fetch, card):
        module.register(sub, common)
    return parser, sub


def _width(env: Mapping[str, str], stream: Any) -> Optional[int]:
    """输出不是终端（管道、Agent 抓输出）时用固定宽度，别去量 stdin/stderr 的终端尺寸。"""
    try:
        if stream.isatty():
            return None
    except (AttributeError, ValueError):
        pass
    try:
        return max(60, int(env.get("COLUMNS") or 100))
    except ValueError:
        return 100


def _console(env: Mapping[str, str], *, stderr: bool = False) -> Console:
    stream = sys.stderr if stderr else sys.stdout
    return Console(stderr=stderr, width=_width(env, stream), highlight=False, markup=False, emoji=False)


def _emit_json(console: Console, payload: Dict[str, Any]) -> None:
    console.file.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    console.file.flush()


def _mcd_hints(err: McdError) -> List[str]:
    if err.http_status == 401:
        return ["检查 MCD_MCP_TOKEN 或 ~/.config/mcd-mcp/token（Token 在 open.mcd.cn 控制台申请）"]
    if err.http_status == 429:
        return ["稍等一会儿再试；mcd-git 不做批量和轮询"]
    if err.transport:
        return ["网络不通或超时，稍后再试"]
    return []


def _fail(err: GitMcdError, *, json_mode: bool, command: Optional[str], demo: bool, out: Console, err_out: Console) -> int:
    kind = "usage" if isinstance(err, UsageError) else ("fatal" if err.fatal else "error")
    if json_mode:
        _emit_json(
            out,
            {
                "ok": False,
                "command": command,
                "demo": demo,
                "error": {"type": kind, "message": err.message, "hints": err.hints, "remote": err.remote, "detail": err.detail, "exitCode": err.exit_code},
            },
        )
        return err.exit_code
    if isinstance(err, UsageError) and err.usage:
        err_out.print(err.usage.rstrip())
    for line in err.remote:
        err_out.print(Text.assemble(("remote: ", R.DIM), line))
    label = "fatal: " if kind in ("fatal", "usage") else "error: "
    err_out.print(Text.assemble((label, "bold red"), err.message))
    R.hints(err_out, err.hints)
    return err.exit_code


def main(
    argv: Optional[List[str]] = None,
    *,
    console: Optional[Console] = None,
    err_console: Optional[Console] = None,
    env: Optional[Mapping[str, str]] = None,
    client_factory: Optional[Callable[[Context], Any]] = None,
    input_fn: Optional[Callable[[str], str]] = None,
    interactive: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    out = console or _console(env)
    err_out = err_console or (console if console is not None else _console(env, stderr=True))
    head = argv[: argv.index("--")] if "--" in argv else argv
    json_mode = "--json" in head
    demo = "--demo" in head or str(env.get("MCD_GIT_DEMO", "")).lower() in _TRUE

    parser, sub = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as err:
        return _fail(err, json_mode=json_mode, command=None, demo=demo, out=out, err_out=err_out)
    except SystemExit as exc:  # 子命令的 -h
        return int(exc.code or 0)

    json_mode = bool(getattr(args, "json", False))
    demo = bool(getattr(args, "demo", False)) or str(env.get("MCD_GIT_DEMO", "")).lower() in _TRUE
    if getattr(args, "version", False):
        out.print(f"mcd-git {__version__}")
        return 0
    if getattr(args, "show_help", False) or args.command in (None, "help"):
        topic = getattr(args, "topic", None)
        if topic:
            target = sub.choices.get(topic)
            if target is None:
                return _fail(GitMcdError(f"'{topic}' is not a git mcd command", hint="git mcd help 看全部命令"), json_mode=json_mode, command="help", demo=demo, out=out, err_out=err_out)
            out.file.write(target.format_help())
            return 0
        result = help_cmd.run(None, args)
        if json_mode:
            _emit_json(out, {"ok": True, "command": "help", "demo": demo, "data": result.data})
        else:
            help_cmd.render_table(out)
        return 0

    if interactive is None:
        interactive = (not json_mode) and sys.stdin.isatty() and sys.stdout.isatty()
    ctx = Context(
        demo=demo,
        json_mode=json_mode,
        console=out,
        env=env,
        interactive=bool(interactive),
        input_fn=input_fn or input,
        client_factory=client_factory,
        now=now,
    )
    try:
        result = args.func(ctx, args)
    except GitMcdError as err:
        return _fail(err, json_mode=json_mode, command=args.command, demo=demo, out=out, err_out=err_out)
    except McdError as err:
        wrapped = GitMcdError(err.message, remote=[f"code {err.code}" + (f" · trace {err.trace_id}" if err.trace_id else "")] if err.code is not None else [], hint=_mcd_hints(err))
        return _fail(wrapped, json_mode=json_mode, command=args.command, demo=demo, out=out, err_out=err_out)
    except KeyboardInterrupt:
        err_out.print("\n已中断，什么都没改。")
        return 130
    if json_mode:
        _emit_json(out, {"ok": True, "command": args.command, "demo": demo, "data": result.data, "mcpCalls": ctx.mcp_calls})
    elif result.render is not None:
        result.render(out)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
