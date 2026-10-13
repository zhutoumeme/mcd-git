"""0.2.5 `mcd-git completion {bash,zsh,fish,powershell}`：脚本从 argparse 自动导出；bash 行为用 macOS 自带 /bin/bash（3.2）实跑；
餐品名经 `mcd-git __complete` 只读本机缓存补全。fish 本机没装就跳过。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mcd_git import completion, menucache, render
from mcd_git.api import Menu
from mcd_git.cli import build_parser
from mcd_git.commands.common import resolve
from mcd_git.state import CN_TZ

from .conftest import Runner

ROOT = Path(__file__).resolve().parents[1]
BASH = "/bin/bash"
REMOTE = {"storeCode": "1450001", "storeName": "演示店", "mode": "pickup", "beType": 1, "orderType": 1, "beCode": None}
ODD_MEALS = [
    {"code": "7001", "name": "堡堡 双层(大)", "type": "套餐"},
    {"code": "7002", "name": "堡堡Tom's \"Best\"", "type": "单品"},
    {"code": "7003", "name": "堡堡麦咖啡™（中）", "type": "单品"},
    {"code": "1001", "name": "巨无霸", "type": "单品"},
]
needs_bash = pytest.mark.skipif(not Path(BASH).exists(), reason="没有 /bin/bash")


@pytest.fixture(scope="module")
def spec() -> Dict[str, Any]:
    return completion.build_spec()


# ---------------------------------------------------------------- spec 与 parser 一致


def test_spec_matches_parser(spec: Dict[str, Any]) -> None:
    parser, sub = build_parser()
    names = [n for n, _ in spec["commands"]]
    assert set(names) == set(sub.choices)
    assert "completion" in names and "__complete" not in names
    assert {s for o in spec["global_opts"] for s in o["strings"]} == {"-h", "--help", "--version", "--demo", "--json"}
    for name, sp in sub.choices.items():
        got = {s for o in spec["subs"][name]["options"] for s in o["strings"]}
        want = {s for a in sp._actions if a.option_strings and a.help is not argparse.SUPPRESS for s in a.option_strings}
        assert got == want, name
    assert "--force" not in {s for o in spec["subs"]["push"]["options"] for s in o["strings"]}
    assert "-f" not in {s for o in spec["subs"]["pull"]["options"] for s in o["strings"]}


def test_hand_written_tables_reference_real_parser_entries() -> None:
    parser, sub = build_parser()
    for (name, dest), kind in completion.POSITIONAL_KINDS.items():
        assert kind in ("meals", "staged", "commands")
        assert dest in [a.dest for a in sub.choices[name]._actions if not a.option_strings], (name, dest)
    for name, opt in completion.FILE_OPTS:
        assert opt in [s for a in sub.choices[name]._actions for s in a.option_strings], (name, opt)
    for opt in completion.VALUE_HINTS:
        assert any(opt in a.option_strings for sp in sub.choices.values() for a in sp._actions), opt
    assert completion.VALUE_HINTS["--qr"] == render.QR_STYLE_VALUES


def test_value_sources(spec: Dict[str, Any]) -> None:
    def opt(sub: str, name: str) -> Dict[str, Any]:
        return next(o for o in spec["subs"][sub]["options"] if name in o["strings"])

    assert opt("push", "--qr")["values"] == list(render.QR_STYLE_VALUES)
    assert opt("show", "--qr")["values"] == list(render.QR_STYLE_VALUES)
    assert opt("card", "--style")["values"] == ["skyline", "receipt", "grid"]
    assert opt("init", "--mode")["values"] == ["delivery", "drive", "pickup"]
    assert opt("card", "-o")["arg"] == "file"
    assert opt("init", "--city")["arg"] == "free"
    assert opt("push", "--dry-run")["arg"] == "none"
    assert spec["subs"]["completion"]["positional"] == {"dest": "shell", "kind": "values", "values": ["bash", "zsh", "fish", "powershell"]}
    assert spec["subs"]["add"]["positional"]["kind"] == "meals"
    assert spec["subs"]["restore"]["positional"]["kind"] == "staged"
    assert spec["subs"]["reset"]["positional"]["kind"] is None


@pytest.mark.parametrize("shell", completion.SHELLS)
def test_scripts_contain_every_command_and_value(spec: Dict[str, Any], shell: str) -> None:
    script = completion.render(shell, spec)
    assert f"completion {shell} 生成" in script
    for name, _ in spec["commands"]:
        assert name in script
    for word in list(render.QR_STYLE_VALUES) + ["skyline", "receipt", "grid", "delivery", "drive", "pickup"]:
        assert word in script
    assert "__complete" in script


def test_scripts_mark_mcd_git_as_primary_and_git_mcd_as_compatibility(spec: Dict[str, Any]) -> None:
    for shell in completion.SHELLS:
        script = completion.render(shell, spec)
        assert "mcd-git <TAB>" in script
        assert "git mcd <TAB> 只是可选兼容" in script


def test_powershell_script_has_native_registration_and_safe_menu_call(spec: Dict[str, Any]) -> None:
    script = completion.render("powershell", spec)
    assert "Register-ArgumentCompleter" in script
    assert "CompletionResult" in script
    assert "Get-Command mcd-git" in script
    assert "$arguments = @('__complete', $Kind, '--shell', 'powershell')" in script
    assert "& $command @arguments" in script
    assert "values += @('fancy:burger'" not in script
    for name in render.FANCY_TEMPLATES:
        assert f"fancy={name}" in script
        assert f"fancy:{name}" in script
    assert "mcd-git" in script and "git-mcd" in script


@pytest.mark.parametrize("executable_name", ("powershell", "pwsh"))
def test_powershell_script_loads_and_executes_completion(spec: Dict[str, Any], tmp_path: Path, executable_name: str) -> None:
    selected = os.environ.get("MCD_GIT_POWERSHELL")
    if selected and selected != executable_name:
        pytest.skip(f"CI 当前矩阵只验证 {selected}")
    executable = shutil.which(executable_name)
    if executable is None:
        pytest.skip(f"本机没有 {executable_name}")
    path = tmp_path / "mcd-git-completion.ps1"
    path.write_text(completion.render("powershell", spec), encoding="utf-8")
    quoted = str(path).replace("'", "''")
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{quoted}'; "
        "if ($ErrorActionPreference -ne 'Stop') { throw 'completion script changed the caller error preference' }; "
        "$matches = @(TabExpansion2 'mcd-git pu' 10).CompletionMatches; "
        "if (-not (@($matches | Where-Object { $_.CompletionText -eq 'push' }).Count -gt 0)) { throw 'push completion missing' }"
    )
    proc = subprocess.run([executable, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_bash_script_avoids_macOS_incompatible_features(spec: Dict[str, Any]) -> None:
    script = completion.render("bash", spec)
    for bad in ("declare -A", "mapfile", "readarray", "compopt", ",,}", "^^}", ";&", "[[ -v", "local -n", "[-1]", "printf %q"):
        assert bad not in script, bad


def test_quoting_helpers() -> None:
    assert completion.sh_quote("it's") == "'it'\\''s'"
    assert completion.fish_quote("a\\b'c") == "'a\\\\b\\'c'"


# ---------------------------------------------------------------- bash（/bin/bash 3.2 实跑）


@pytest.fixture
def shell_env(tmp_path: Path) -> Dict[str, str]:
    """PATH 最前是调本仓库源码的 mcd-git / git-mcd wrapper（不会用到 ~/.local/bin 里的旧版本）。"""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("mcd-git", "git-mcd"):
        wrapper = bindir / name
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m mcd_git "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    env = dict(os.environ)
    env.update({"PATH": f"{bindir}{os.pathsep}{env.get('PATH', '')}", "PYTHONPATH": str(ROOT / "src"), "LC_ALL": "en_US.UTF-8"})
    env.pop("MCD_GIT_DEMO", None)
    return env


@pytest.fixture
def bash_script(tmp_path: Path, spec: Dict[str, Any]) -> Path:
    path = tmp_path / "mcd-git.bash"
    path.write_text(completion.render("bash", spec), encoding="utf-8")
    return path


DRIVER = 'source "$1"; shift; COMP_WORDS=("$@"); COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 )); _mcd_git; for c in "${COMPREPLY[@]}"; do printf "%s\\n" "$c"; done'


def _bash(script: Path, env: Dict[str, str], *words: str) -> List[str]:
    proc = subprocess.run([BASH, "-c", DRIVER, "_", str(script), *words], capture_output=True, text=True, env=env, timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    return proc.stdout.splitlines()


@needs_bash
def test_bash_is_the_old_macos_one() -> None:
    out = subprocess.run([BASH, "-c", "echo $BASH_VERSION"], capture_output=True, text=True).stdout
    if sys.platform == "darwin":
        assert out.startswith("3.2"), out


@needs_bash
def test_bash_syntax(bash_script: Path) -> None:
    proc = subprocess.run([BASH, "-n", str(bash_script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@needs_bash
def test_bash_completes_commands_options_and_values(bash_script: Path, shell_env: Dict[str, str]) -> None:
    _, sub = build_parser()
    assert set(_bash(bash_script, shell_env, "mcd-git", "")) == set(sub.choices)
    assert _bash(bash_script, shell_env, "git-mcd", "pu") == ["push", "pull"]
    assert {"--demo", "--json", "--version"} <= set(_bash(bash_script, shell_env, "mcd-git", "--"))
    assert _bash(bash_script, shell_env, "mcd-git", "push", "--qr", "") == list(render.QR_STYLE_VALUES)
    assert _bash(bash_script, shell_env, "mcd-git", "push", "--q") == ["--qr"]
    assert "--force" not in _bash(bash_script, shell_env, "mcd-git", "push", "--")
    # bash 3.2 不在 = 处拆词，但 readline 只替换 = 之后的部分：候选裁成 burger，插入后是 fancy=burger（pty 实测一致）
    assert _bash(bash_script, shell_env, "mcd-git", "push", "--qr", "fancy=b") == ["burger"]
    assert _bash(bash_script, shell_env, "mcd-git", "show", "--qr=fancy=c") == ["cone"]
    assert _bash(bash_script, shell_env, "mcd-git", "card", "--style", "r") == ["receipt"]
    assert _bash(bash_script, shell_env, "mcd-git", "init", "--mode", "") == ["delivery", "drive", "pickup"]
    assert set(_bash(bash_script, shell_env, "mcd-git", "help", "")) == set(sub.choices)
    assert _bash(bash_script, shell_env, "mcd-git", "completion", "") == ["bash", "zsh", "fish", "powershell"]
    assert _bash(bash_script, shell_env, "mcd-git", "commit", "-m", "") == []  # 自由文本不补
    assert _bash(bash_script, shell_env, "mcd-git", "bogus", "") == []


@needs_bash
def test_bash_reassembles_bash4_split_words(bash_script: Path, shell_env: Dict[str, str]) -> None:
    """bash 4+ 会把 fancy=b 拆成 fancy = b；本机只有 3.2，手工造出拆开的 COMP_WORDS 验证重组逻辑。"""
    assert _bash(bash_script, shell_env, "mcd-git", "push", "--qr", "fancy", "=", "b") == ["burger"]
    assert _bash(bash_script, shell_env, "mcd-git", "push", "--qr", "=", "fa") == ["fancy", "fancy=burger", "fancy=fries", "fancy=nuggets", "fancy=cone", "fancy=pie", "fancy=arch"]


@needs_bash
def test_bash_completes_demo_meals(bash_script: Path, shell_env: Dict[str, str]) -> None:
    assert _bash(bash_script, shell_env, "mcd-git", "--demo", "add", "巨") == ["巨无霸", "巨无霸套餐"]
    assert _bash(bash_script, shell_env, "mcd-git", "add", "--demo", "麦辣") == ["麦辣鸡腿堡", "麦辣鸡腿堡套餐"]
    assert _bash(bash_script, shell_env, "mcd-git", "ls-remote", "巨") == []  # 真实模式没有缓存：安静，不回退成文件名
    assert _bash(bash_script, shell_env, "mcd-git", "add", "巨") == []


def _seed_cache(home: Path, meals: List[Dict[str, str]]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "state.json").write_text(json.dumps({"version": 1, "remote": REMOTE, "index": []}, ensure_ascii=False), encoding="utf-8")
    menucache.write(home, REMOTE, meals, datetime.now(CN_TZ))


@needs_bash
def test_bash_special_names_insert_escaped_and_resolve(bash_script: Path, shell_env: Dict[str, str]) -> None:
    _seed_cache(Path(shell_env["MCD_GIT_HOME"]), ODD_MEALS)
    got = _bash(bash_script, shell_env, "mcd-git", "add", "堡堡")
    odd = ODD_MEALS[:3]
    assert got == [menucache.bash_escape(m["name"]) for m in odd]
    assert got[0] == "堡堡\\ 双层\\(大\\)"
    menu = Menu({"meals": {m["code"]: {"name": m["name"], "currentPrice": "10"} for m in ODD_MEALS}})
    for escaped, meal in zip(got, odd):
        back = subprocess.run([BASH, "-c", 'printf "%s" ' + escaped], capture_output=True, text=True, env=shell_env).stdout
        assert back == meal["name"]
        assert resolve(menu, back) == meal["code"]
    # 已经敲了一半转义也照样补（Python 侧去掉反斜杠再匹配）
    assert _bash(bash_script, shell_env, "mcd-git", "add", "堡堡\\ 双") == [got[0]]


@needs_bash
def test_bash_restore_completes_cart(runner: Runner, bash_script: Path, shell_env: Dict[str, str]) -> None:
    assert runner.run("--demo", "add", "巨无霸", "中薯条")[0] == 0
    assert _bash(bash_script, shell_env, "mcd-git", "--demo", "restore", "--staged", "") == ["巨无霸", "中薯条"]


def _git_completion_bash() -> Any:
    candidates = []
    try:
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, timeout=10).stdout.strip()
        if exec_path:
            candidates.append(Path(exec_path).parent.parent / "share" / "git-core" / "git-completion.bash")
    except (OSError, subprocess.SubprocessError):
        pass
    candidates += [
        Path("/Library/Developer/CommandLineTools/usr/share/git-core/git-completion.bash"),
        Path("/opt/homebrew/etc/bash_completion.d/git-completion.bash"),
    ]
    return next((p for p in candidates if p.is_file()), None)


GIT_DRIVER = (
    'source "$1"; source "$2"; shift 2; COMP_WORDS=("$@"); COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 )); '
    'COMP_LINE="$*"; COMP_POINT=${#COMP_LINE}; __git_wrap__git_main; for c in "${COMPREPLY[@]}"; do printf "%s\\n" "$c"; done'
)


@needs_bash
@pytest.mark.skipif(_git_completion_bash() is None, reason="找不到 git-completion.bash")
def test_bash_git_mcd_via_git_completion(bash_script: Path, shell_env: Dict[str, str]) -> None:
    gitc = str(_git_completion_bash())

    def run(*words: str) -> List[str]:
        proc = subprocess.run([BASH, "-c", GIT_DRIVER, "_", gitc, str(bash_script), *words], capture_output=True, text=True, env=dict(shell_env, MCD_GIT_DEMO="1"), timeout=30, stdin=subprocess.DEVNULL)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.splitlines()

    got = run("git", "mcd", "add", "")
    assert "巨无霸 " in got and "麦辣鸡腿堡套餐 " in got  # git-completion 注册时带 -o nospace，候选自带空格
    assert run("git", "mcd", "push", "--qr", "fancy=b") == ["burger "]
    assert "push " in run("git", "mcd", "pu")


# ---------------------------------------------------------------- zsh / fish


@pytest.mark.skipif(shutil.which("zsh") is None, reason="没有 zsh")
def test_zsh_registers_functions_and_user_commands(shell_env: Dict[str, str], tmp_path: Path) -> None:
    env = dict(shell_env, HOME=str(tmp_path))  # compinit 写 ~/.zcompdump：指到临时目录
    code = (
        'eval "$(mcd-git completion zsh)"; eval "$(mcd-git completion zsh)"; '
        "print -r -- ${+functions[_mcd-git]} ${+functions[_git-mcd]} ${_comps[mcd-git]} ${_comps[git-mcd]}; "
        'zstyle -g x ":completion:*:*:git:*" user-commands; print -l -- $x'
    )
    proc = subprocess.run(["zsh", "-f", "-c", code], capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == "1 1 _mcd-git _mcd-git"
    assert lines[1:] == ["mcd:用 git 命令点麦当劳"]  # eval 两次也不重复追加


@pytest.mark.skipif(shutil.which("zsh") is None, reason="没有 zsh")
def test_zsh_keeps_existing_user_commands(shell_env: Dict[str, str], tmp_path: Path) -> None:
    env = dict(shell_env, HOME=str(tmp_path))
    code = "zstyle ':completion:*:*:git:*' user-commands foo:bar; eval \"$(mcd-git completion zsh)\"; zstyle -g x ':completion:*:*:git:*' user-commands; print -l -- $x"
    proc = subprocess.run(["zsh", "-f", "-c", code], capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert proc.stdout.splitlines() == ["foo:bar", "mcd:用 git 命令点麦当劳"], proc.stderr


@pytest.mark.skipif(shutil.which("zsh") is None, reason="没有 zsh")
def test_zsh_syntax(spec: Dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "mcd-git.zsh"
    path.write_text(completion.render("zsh", spec), encoding="utf-8")
    proc = subprocess.run(["zsh", "-n", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(shutil.which("fish") is None, reason="本机没装 fish（未实测）")
def test_fish_script(spec: Dict[str, Any], shell_env: Dict[str, str], tmp_path: Path) -> None:  # pragma: no cover - 本机无 fish
    path = tmp_path / "mcd-git.fish"
    path.write_text(completion.render("fish", spec), encoding="utf-8")
    assert subprocess.run(["fish", "-n", str(path)], capture_output=True).returncode == 0
    proc = subprocess.run(["fish", "-c", f"source {path}; complete -C 'mcd-git ad'"], capture_output=True, text=True, env=shell_env, timeout=60)
    assert any(line.split("\t")[0] == "add" for line in proc.stdout.splitlines()), proc.stdout


# ---------------------------------------------------------------- 子命令本身


def test_completion_command_prints_script_verbatim(runner: Runner) -> None:
    code, out = runner.run("completion", "bash", width=40)  # 窄终端也不能折行
    assert code == 0
    assert out == completion.render("bash")


def test_completion_json(runner: Runner) -> None:
    code, data = runner.json("completion", "zsh")
    assert code == 0 and data["ok"] is True
    assert data["data"]["shell"] == "zsh"
    assert "compdef _mcd-git mcd-git git-mcd" in data["data"]["script"]


def test_completion_listed_in_help_and_rejects_unknown_shell(runner: Runner) -> None:
    code, out = runner.run("help")
    assert code == 0 and "completion <shell>" in out
    code, out = runner.run("completion", "bogus")
    assert code != 0
    assert "invalid choice" in out or "bash" in out
    code, data = runner.json("completion", "bogus")
    assert data["ok"] is False and data["error"]["type"] == "usage"
