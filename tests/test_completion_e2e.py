"""0.2.4 补全端到端（opt-in：MCD_GIT_SHELL_E2E=1）：用 pty 起真实交互 shell，真按 TAB，读屏看命令行上插入了什么。

- bash 用 macOS 自带 /bin/bash（3.2）；zsh 用 /bin/zsh -f；fish 本机没装，不测。
- PATH 最前是调本仓库源码的 wrapper，先断言 PATH 里的 mcd-git 就是它（版本号 = 源码里的 __version__、
  有 completion 子命令），不会误用 ~/.local/bin 里已发布的旧版本。
- 真实模式只看插入文本，绝不让 mcd-git 真跑：wrapper 只放行 --demo / --version / completion / __complete，
  其余调用直接退出 97；验证「插入文本 → shell 解析出的参数」时把行首改成 printf（Ctrl-A），回车跑的是 printf。
- 设 MCD_GIT_E2E_LOG=<文件> 会把每一步剥掉 ANSI 后的屏幕片段写进去（completion-e2e.md 的依据）。
"""
from __future__ import annotations

import json
import os
import pty
import re
import select
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from mcd_git import __version__, menucache
from mcd_git.api import Menu
from mcd_git.commands.common import resolve
from mcd_git.state import CN_TZ

pytestmark = pytest.mark.skipif(os.environ.get("MCD_GIT_SHELL_E2E") != "1" or os.name == "nt", reason="pty 端到端是 opt-in：MCD_GIT_SHELL_E2E=1")

ROOT = Path(__file__).resolve().parents[1]
STEP = 5.0  # 每一步最多等几秒
REMOTE = {"storeCode": "1450001", "storeName": "演示店", "mode": "pickup", "beType": 1, "orderType": 1, "beCode": None}
# 前缀各自唯一，TAB 一次就整词插入；名字里有空格、ASCII 括号、单双引号、™、全角括号
ODD = [
    ("堡堡", {"code": "7001", "name": "堡堡 双层(大)", "type": "套餐"}),
    ("汤姆", {"code": "7002", "name": "汤姆's \"Best\" 堡", "type": "单品"}),
    ("麦咖", {"code": "7003", "name": "麦咖啡™（中）美式", "type": "单品"}),
]
GIT_COMPLETION = [
    Path("/Library/Developer/CommandLineTools/usr/share/git-core/git-completion.bash"),
    Path("/opt/homebrew/etc/bash_completion.d/git-completion.bash"),
]

_ANSI = re.compile(r"\x1b\[[0-9;?<=>]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>78DEHMc]")
_LOG: List[Tuple[str, str]] = []

GUARD = """#!/bin/sh
# E2E wrapper：只放行不会碰真实账户的调用
ok=0
for a in "$@"; do
  case "$a" in --demo|--version|completion|__complete) ok=1 ;; esac
done
[ "$MCD_GIT_DEMO" = 1 ] && ok=1
if [ "$ok" = 0 ]; then
  echo "E2E-BLOCKED: $*" >&2
  exit 97
fi
exec "{python}" -m mcd_git "$@"
"""


def strip(raw: bytes) -> str:
    text = _ANSI.sub("", raw.decode("utf-8", "replace"))
    return re.sub(r"[\r\x07\x08\x00]", "", text)


class Session:
    """一个跑在 pty 里的交互 shell。"""

    def __init__(self, argv: List[str], env: Dict[str, str], cwd: Path, prompt: str) -> None:
        self.prompt = prompt
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - 子进程
            try:
                import fcntl
                import struct
                import termios

                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 220, 0, 0))
                os.chdir(str(cwd))
                os.execve(argv[0], argv, env)
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd
        self.raw = b""
        self.pos = 0

    def _pump(self, timeout: float) -> None:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if ready:
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                chunk = b""
            self.raw += chunk

    @property
    def text(self) -> str:
        return strip(self.raw)

    def send(self, keys: str) -> None:
        self.mark = self.pos
        os.write(self.fd, keys.encode("utf-8"))

    def expect(self, *needles: str, timeout: float = STEP) -> str:
        """等到 needles 依次出现在上次位置之后；返回这一段屏幕文本。"""
        start = self.pos
        deadline = time.monotonic() + timeout
        while True:
            text = self.text
            pos, ok = self.pos, True
            for needle in needles:
                idx = text.find(needle, pos)
                if idx < 0:
                    ok = False
                    break
                pos = idx + len(needle)
            if ok:
                self.pos = pos
                return text[start:pos]
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"等不到 {needles!r}；屏幕尾部：\n{text[start:][-1500:]}")
            self._pump(min(left, 0.2))

    def quiet(self, seconds: float) -> str:
        """读一段时间，返回这段时间新出现的屏幕文本（用来断言「什么都没补出来」）。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._pump(0.1)
        text = self.text
        out, self.pos = text[self.pos :], len(text)
        return out

    def run(self, command: str) -> str:
        """跑一条命令，返回它的输出（到下一个提示符为止）。"""
        self.send(command + '; echo "__D""ONE__"\r')
        return self.expect("__DONE__", self.prompt)

    def cancel(self) -> None:
        self.send("\x03")
        self.expect(self.prompt)

    def close(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except OSError:
            pass
        os.close(self.fd)


def log(title: str, snippet: str) -> None:
    _LOG.append((title, snippet.strip("\n")))


@pytest.fixture(scope="module", autouse=True)
def write_log() -> Iterator[None]:
    yield
    target = os.environ.get("MCD_GIT_E2E_LOG")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            for title, snippet in _LOG:
                fh.write(f"### {title}\n\n```text\n{snippet}\n```\n\n")


@pytest.fixture
def world(tmp_path: Path) -> Dict[str, Any]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("mcd-git", "git-mcd"):
        wrapper = bindir / name
        wrapper.write_text(GUARD.replace("{python}", sys.executable), encoding="utf-8")
        wrapper.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cfg = tmp_path / "cfg"
    inputrc = tmp_path / "inputrc"
    inputrc.write_text("set bell-style none\nset completion-query-items 1000\nset page-completions off\n", encoding="utf-8")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "ZDOTDIR": str(home),
        "MCD_GIT_HOME": str(cfg),
        "PYTHONPATH": str(ROOT / "src"),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TERM": "xterm",
        "INPUTRC": str(inputrc),
        "COLUMNS": "220",
        "LINES": "50",
    }
    # 真实模式的有效缓存（只给补全读；mcd-git 本身被 wrapper 拦住，不会调 MCP）
    cfg.mkdir()
    (cfg / "state.json").write_text(json.dumps({"version": 1, "remote": REMOTE, "index": []}, ensure_ascii=False), encoding="utf-8")
    menucache.write(cfg, REMOTE, [m for _, m in ODD] + [{"code": "1001", "name": "巨无霸", "type": "单品"}], datetime.now(CN_TZ))
    return {"env": env, "bin": bindir, "tmp": tmp_path}


def _check_binary(sh: Session, world: Dict[str, Any], label: str) -> None:
    out = sh.run("command -v mcd-git; mcd-git --version; mcd-git completion bash >/dev/null && echo HAS_COMP\"\"LETION")
    log(f"{label}：PATH 里的 mcd-git", out)
    assert str(world["bin"] / "mcd-git") in out
    assert "/.local/bin/" not in out
    assert f"mcd-git {__version__}" in out  # FEAT-004 升版本后即 0.2.4
    assert "HAS_COMPLETION" in out  # 已发布的 0.2.3 没有 completion 子命令


def _roundtrip(sh: Session, prompt: str, first: str = "mcd-git") -> str:
    """命令行上已经是补全插入后的文本：行首改成 printf，回车看 shell 实际解析出的参数。"""
    sh.send("\x01printf '<%s>\\n' \r")
    return sh.expect(f"<{first}>", prompt)


def _menu() -> Menu:
    meals = {m["code"]: {"name": m["name"], "currentPrice": "10"} for _, m in ODD}
    meals["1001"] = {"name": "巨无霸", "currentPrice": "24"}
    return Menu({"meals": meals})


def _special_names(sh: Session, label: str, prompt: str) -> None:
    menu = _menu()
    for typed, meal in ODD:
        sh.send(f"mcd-git add {typed}\t")
        tail = meal["name"][-1]
        screen = sh.expect(tail)
        log(f"{label}：真实模式 mcd-git add {typed}<TAB>（不回车）", screen)
        out = _roundtrip(sh, prompt)
        log(f"{label}：行首改成 printf 后 shell 解析出的参数", out)
        assert f"<{meal['name']}>" in out
        assert "E2E-BLOCKED" not in out
        assert resolve(menu, meal["name"]) == meal["code"]


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="没有 /bin/bash")
def test_bash_pty(world: Dict[str, Any]) -> None:
    script = world["tmp"] / "mcd-git.bash"
    env = dict(world["env"], PS1="BP> ")
    sh = Session(["/bin/bash", "--norc", "--noprofile", "-i"], env, world["tmp"], "BP> ")
    try:
        sh.expect("BP> ")
        out = sh.run(f"mcd-git completion bash > {script}; source {script}; echo $BASH_VERSION")
        assert "3.2" in out or sys.platform != "darwin"
        log("bash：版本", out)
        _check_binary(sh, world, "bash")

        # demo：唯一候选整词插入，回车真的执行（demo 不碰账户）
        sh.send("mcd-git --demo add 巨无霸套\t")
        screen = sh.expect("巨无霸套餐")
        log("bash：mcd-git --demo add 巨无霸套<TAB>", screen)
        sh.send("\r")
        out = sh.expect("add '巨无霸套餐'", "BP> ")
        log("bash：回车执行", out)

        # --qr fancy=b：readline 只替换 = 之后的部分，插入后是 fancy=burger
        sh.send("mcd-git push --qr fancy=b\t")
        log("bash：mcd-git push --qr fancy=b<TAB>", sh.expect("fancy=burger"))
        out = _roundtrip(sh, "BP> ")
        assert "<fancy=burger>" in out and "fancy=fancy" not in out

        # 真实模式 + 特殊字符名：只看插入文本，printf 验证 shell 解析结果
        _special_names(sh, "bash", "BP> ")

        # 包含匹配多个候选：只列出，不吞掉已输入的字
        sh.send("mcd-git --demo add 鸡腿\t\t")
        screen = sh.expect("麦辣鸡腿堡", "板烧鸡腿堡")
        log("bash：mcd-git --demo add 鸡腿<TAB><TAB>（包含匹配）", screen)
        out = _roundtrip(sh, "BP> ")
        assert "<鸡腿>" in out

        # 没加载 git-completion.bash：git mcd 补全不生效（bash 默认退回文件名）
        sh.send("git mcd --demo add 巨\t")
        screen = sh.quiet(1.5)
        log("bash：未加载 git-completion.bash 时 git mcd --demo add 巨<TAB>", screen)
        assert "巨无霸" not in screen
        sh.cancel()

        gitc = next((p for p in GIT_COMPLETION if p.is_file()), None)
        if gitc is None:
            pytest.skip("找不到 git-completion.bash")
        sh.run(f"source {gitc}; source {script}")
        # 第一下 TAB 插入公共前缀「麦辣鸡腿堡」；readline 规则：上一次补全改了命令行，下一次不列表，所以第三下才列出
        sh.send("git mcd --demo add 麦辣\t\t\t")
        screen = sh.expect("麦辣鸡腿堡套餐")
        log(f"bash：source {gitc} 之后 git mcd --demo add 麦辣<TAB><TAB><TAB>", screen)
        sh.cancel()
        sh.send("git mcd --demo add 巨无霸套\t")
        screen = sh.expect("巨无霸套餐")
        log("bash：加载 git-completion.bash 后 git mcd --demo add 巨无霸套<TAB>", screen)
        out = _roundtrip(sh, "BP> ", first="git")
        assert "<巨无霸套餐>" in out
        sh.send("git mcd push --qr fancy=c\t")
        log("bash：加载后 git mcd push --qr fancy=c<TAB>", sh.expect("fancy=cone"))
        sh.cancel()
    finally:
        sh.close()


@pytest.mark.skipif(shutil.which("zsh") is None, reason="没有 zsh")
def test_zsh_pty(world: Dict[str, Any]) -> None:
    env = dict(world["env"])
    sh = Session([shutil.which("zsh") or "/bin/zsh", "-f", "-i"], env, world["tmp"], "ZP> ")
    try:
        sh.send("PS1='Z''P> '; RPROMPT=''; unsetopt PROMPT_SP\r")
        sh.expect("ZP> ")
        out = sh.run('eval "$(mcd-git completion zsh)"; echo $ZSH_VERSION')
        log("zsh：版本", out)
        _check_binary(sh, world, "zsh")

        sh.send("mcd-git --demo add 巨无霸套\t")
        screen = sh.expect("巨无霸套餐")
        log("zsh：mcd-git --demo add 巨无霸套<TAB>", screen)
        sh.send("\r")
        out = sh.expect("add '巨无霸套餐'", "ZP> ")
        log("zsh：回车执行", out)

        sh.send("mcd-git push --qr fancy=b\t")
        log("zsh：mcd-git push --qr fancy=b<TAB>", sh.expect("fancy=burger"))
        out = _roundtrip(sh, "ZP> ")
        assert "<fancy=burger>" in out

        _special_names(sh, "zsh", "ZP> ")

        sh.send("mcd-git --demo add 鸡腿\t")  # zsh 按名字排序列出，第一下 TAB 就列
        screen = sh.expect("板烧鸡腿堡", "麦辣鸡腿堡套餐")
        log("zsh：mcd-git --demo add 鸡腿<TAB>（包含匹配）", screen)
        out = _roundtrip(sh, "ZP> ")
        log("zsh：包含匹配列出后 printf", out)
        assert "<鸡腿>" in out

        # git mcd：系统 _git 调 _git-mcd
        sh.send("git mcd --demo add 巨\t\t")  # 第一下插入公共前缀「巨无霸」，第二下列出
        screen = sh.expect("巨无霸套餐")
        log("zsh：git mcd --demo add 巨<TAB><TAB>", screen)
        sh.cancel()
        sh.send("git mcd --demo add 巨无霸套\t")
        sh.expect("巨无霸套餐")
        out = _roundtrip(sh, "ZP> ", first="git")
        log("zsh：git mcd --demo add 巨无霸套<TAB> 后 printf", out)
        assert "<巨无霸套餐>" in out
        sh.send("git mcd push --qr \t")
        log("zsh：git mcd push --qr <TAB>", sh.expect("fancy=burger"))
        sh.cancel()

        # 自由文本选项不补，只提示要填什么（不插入文本）
        sh.send("mcd-git commit -m \t")
        log("zsh：mcd-git commit -m <TAB>", sh.expect("-m <说明>"))
        sh.cancel()
        sh.send("mcd-git ls-remote \t")
        log("zsh：真实模式 mcd-git ls-remote <TAB>（有缓存时直接列菜单）", sh.expect("巨无霸"))
        sh.cancel()

        # git <TAB> 列表里有 mcd（user-commands）：git mc<TAB> 补成 git mcd
        sh.send("git mc\t")
        screen = sh.expect("git mcd")
        log("zsh：git mc<TAB>", screen)
        sh.cancel()
    finally:
        sh.close()
