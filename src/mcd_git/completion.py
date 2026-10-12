"""`mcd-git completion {bash,zsh,fish}`：从 argparse 自动导出补全脚本（不在热路径，只在这个命令里运行）。

子命令、全局选项、各子命令的选项与取值全部从 cli.build_parser() 读出来，不手写清单；只有两张小表是手写的：
VALUE_HINTS（--qr 的取值，argparse 里它是自由字符串、在 validate_qr 里校验）和 POSITIONAL_KINDS（哪些位置参数
补餐品名 / 购物车 / 子命令名），外加 FILE_OPTS（哪些选项补文件名）。测试会断言这些表引用的子命令和参数真实存在。

餐品名 / 购物车名在按 TAB 时通过 `mcd-git __complete` 只读本机缓存（见 complete.py），脚本自己不带菜单。

bash 脚本必须兼容 macOS 自带的 bash 3.2：不用 declare -A / mapfile / compopt / ${v,,} / ;& / [[ -v ]] /
负数组下标 / local -n；中文转义在 Python 侧做（bash 3.2 的 printf %q 会把中文转成 $'\\xxx'）。
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__
from . import render as R

SHELLS = ("bash", "zsh", "fish")

# 手写的小表（唯一不从 argparse 导出的部分）
VALUE_HINTS: Dict[str, Tuple[str, ...]] = {"--qr": R.QR_STYLE_VALUES}
FILE_OPTS = {("card", "--output")}
POSITIONAL_KINDS = {("add", "meals"): "meals", ("ls-remote", "keyword"): "meals", ("restore", "targets"): "staged", ("help", "topic"): "commands"}

_GLOBAL_HELP = {"-h": "命令对照表", "--help": "命令对照表", "--version": "显示版本号"}
_SUB_HELP = "这个子命令的帮助"
_WORD_RE = re.compile(r"^[A-Za-z0-9_.=+-]+$")  # 嵌进脚本的子命令名 / 选项 / 取值只允许这些字符（脚本里按空格切词）


# ---------------------------------------------------------------- 从 argparse 导出


def _short_help(text: Optional[str], limit: int = 28) -> str:
    """补全列表里的说明只留第一句，按显示宽度截断（中文算 2 列）。"""
    text = " ".join(str(text or "").split())
    for sep in ("；", "。"):
        text = text.split(sep, 1)[0]
    out, width = "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + w > limit * 2 - 2:
            return out + "…"
        out, width = out + ch, width + w
    return out


def _check_word(word: str) -> str:
    if not _WORD_RE.match(word):
        raise ValueError(f"补全脚本里不能嵌入这个词：{word!r}")
    return word


def _arg_kind(sub: str, action: argparse.Action) -> Tuple[str, Tuple[str, ...]]:
    """取值来源：VALUE_HINTS > choices > FILE_OPTS > 自由文本（不补）。"""
    for opt in action.option_strings:
        if opt in VALUE_HINTS:
            return "values", tuple(VALUE_HINTS[opt])
    if action.choices:
        return "values", tuple(str(c) for c in action.choices)
    if any((sub, opt) in FILE_OPTS for opt in action.option_strings):
        return "file", ()
    return "free", ()


def _option(sub: Optional[str], action: argparse.Action, help_fallback: Dict[str, str]) -> Dict[str, Any]:
    takes_value = action.nargs != 0
    help_text = action.help
    if isinstance(action, argparse._HelpAction):  # noqa: SLF001 —— argparse 自带的 -h 是英文说明
        help_text = _SUB_HELP
    if not help_text:
        help_text = next((help_fallback[o] for o in action.option_strings if o in help_fallback), "")
    arg, values = _arg_kind(sub or "", action) if takes_value else ("none", ())
    return {
        "strings": [_check_word(o) for o in action.option_strings],
        "takes_value": takes_value,
        "choices": list(action.choices) if action.choices else None,
        "help": _short_help(help_text),
        "metavar": action.metavar if isinstance(action.metavar, str) else None,
        "arg": arg,
        "values": [_check_word(v) for v in values],
    }


def build_spec() -> Dict[str, Any]:
    """把 parser 里补全要用的东西导出成纯数据（三种脚本都从它渲染）。"""
    from .cli import build_parser  # 函数内导入：cli 也会导入 commands.completion

    parser, sub = build_parser()
    helps = {ca.dest: ca.help or "" for ca in sub._choices_actions}  # noqa: SLF001
    commands = [(_check_word(name), _short_help(helps.get(name))) for name in sub.choices if not name.startswith("_")]
    fallback = dict(_GLOBAL_HELP)
    for sp in sub.choices.values():
        for action in sp._actions:  # noqa: SLF001
            if action.help and action.help is not argparse.SUPPRESS:
                for opt in action.option_strings:
                    fallback.setdefault(opt, action.help)
    global_opts = [_option(None, a, fallback) for a in parser._actions if a.option_strings and a.help is not argparse.SUPPRESS]  # noqa: SLF001
    subs: Dict[str, Dict[str, Any]] = {}
    for name, _ in commands:
        sp = sub.choices[name]
        options = [_option(name, a, fallback) for a in sp._actions if a.option_strings and a.help is not argparse.SUPPRESS]  # noqa: SLF001
        positional = None
        for action in sp._actions:  # noqa: SLF001
            if action.option_strings:
                continue
            kind = POSITIONAL_KINDS.get((name, action.dest))
            values: List[str] = []
            if kind is None and action.choices:
                kind, values = "values", [_check_word(str(c)) for c in action.choices]
            positional = {"dest": action.dest, "kind": kind, "values": values}
            break
        subs[name] = {"options": options, "positional": positional}
    return {"version": __version__, "commands": commands, "global_opts": global_opts, "subs": subs}


# ---------------------------------------------------------------- 引号


def sh_quote(text: str) -> str:
    """bash / zsh 通用的单引号包裹：' → '\\''。"""
    return "'" + str(text).replace("'", "'\\''") + "'"


def fish_quote(text: str) -> str:
    """fish 的单引号包裹：\\ → \\\\、' → \\'。"""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _var(sub: str) -> str:
    return sub.replace("-", "_")


# ---------------------------------------------------------------- bash / zsh 共用的取值表（POSIX case 语法两边都认）


def _optarg_func(spec: Dict[str, Any]) -> str:
    """__mcd_git_optarg <子命令> <选项> → none | file | values:<空格分隔取值> | free:<提示>"""
    lines = ["__mcd_git_optarg() {", '  case "$1 $2" in']
    for name, info in spec["subs"].items():
        for opt in info["options"]:
            if not opt["takes_value"]:
                continue
            pats = "|".join(sh_quote(f"{name} {s}") for s in opt["strings"])
            if opt["arg"] == "values":
                out = "values:" + " ".join(opt["values"])
            elif opt["arg"] == "file":
                out = "file"
            else:
                out = "free:" + (opt["metavar"] or opt["help"] or opt["strings"][-1])
            lines.append(f"    {pats}) printf '%s\\n' {sh_quote(out)} ;;")
    lines += ["    *) printf '%s\\n' none ;;", "  esac", "}"]
    return "\n".join(lines)


def _poskind_func(spec: Dict[str, Any]) -> str:
    """__mcd_git_poskind <子命令> → meals | staged | commands | values:<取值> | none"""
    groups: Dict[str, List[str]] = {}
    for name, info in spec["subs"].items():
        pos = info["positional"]
        if not pos or not pos["kind"]:
            continue
        out = "values:" + " ".join(pos["values"]) if pos["kind"] == "values" else pos["kind"]
        groups.setdefault(out, []).append(name)
    lines = ["__mcd_git_poskind() {", '  case "$1" in']
    for out, names in groups.items():
        lines.append(f"    {'|'.join(sh_quote(n) for n in names)}) printf '%s\\n' {sh_quote(out)} ;;")
    lines += ["    *) printf '%s\\n' none ;;", "  esac", "}"]
    return "\n".join(lines)


def _header(shell: str, install: Sequence[str]) -> str:
    lines = [f"# mcd-git 命令补全（{shell}）—— 由 mcd-git {__version__} completion {shell} 生成，请勿手改；升级后重新生成即可。"]
    lines += ["# " + x for x in install]
    return "\n".join(lines)


# ---------------------------------------------------------------- bash

_BASH_BODY = r'''
# 把候选里以 $2 开头的加进 COMPREPLY（$1 空格分隔，只含 [A-Za-z0-9_.=+-]，不会触发通配；$3 拼在每个候选前面）
__mcd_git_add_words() {
  local c
  for c in $1; do
    case "$3$c" in
      "$2"*) COMPREPLY+=("$3$c") ;;
    esac
  done
}

# 跑 mcd-git __complete，一行一个候选（Python 侧已经转义好，原样放进 COMPREPLY）
__mcd_git_lines() {
  local line
  while IFS= read -r line; do
    [ -n "$line" ] && COMPREPLY+=("$line")
  done < <(command mcd-git "$@" 2>/dev/null)
}

# 选项取值：$1 = optarg 结果，$2 = 已输入的值，$3 = 候选前缀（--opt= 写法时是 "--opt="）
__mcd_git_value() {
  local line
  case "$1" in
    values:*) __mcd_git_add_words "${1#values:}" "$3$2" "$3" ;;
    file)
      while IFS= read -r line; do
        COMPREPLY+=("$3$line")
      done < <(compgen -f -- "$2")
      ;;
  esac
}

# readline 只替换最后一个分词符（COMP_WORDBREAKS 里的 = 和 :）之后的部分：把候选里这段前缀裁掉，
# 否则 --qr fancy=b 会变成 fancy=fancy=burger
__mcd_git_trim() {
  local cur="$1" pre="" p i
  case "$COMP_WORDBREAKS" in
    *=*) case "$cur" in *=*) p="${cur%"${cur##*=}"}"; [ ${#p} -gt ${#pre} ] && pre="$p" ;; esac ;;
  esac
  case "$COMP_WORDBREAKS" in
    *:*) case "$cur" in *:*) p="${cur%"${cur##*:}"}"; [ ${#p} -gt ${#pre} ] && pre="$p" ;; esac ;;
  esac
  [ -z "$pre" ] && return 0
  case "$pre" in
    *\\=|*\\:) return 0 ;;  # 反斜杠转义过的 = / : 不是分词符
  esac
  for (( i=0; i<${#COMPREPLY[@]}; i++ )); do
    COMPREPLY[i]="${COMPREPLY[i]#"$pre"}"
  done
}

# 状态机：读 __mcd_w（已重组的词）和 __mcd_cw（当前词下标），$1 = 程序名（mcd-git / git-mcd / git 后面的 mcd）的下标
__mcd_git_dispatch() {
  local prog="$1" cur="${__mcd_w[__mcd_cw]}" prev="" sub="" demo="" i w kind="" opt v
  if [ "$__mcd_cw" -gt $((prog + 1)) ]; then
    prev="${__mcd_w[__mcd_cw-1]}"
  fi
  for (( i=prog+1; i<${#__mcd_w[@]}; i++ )); do
    w="${__mcd_w[i]}"
    [ "$i" -ne "$__mcd_cw" ] && [ "$w" = --demo ] && demo=--demo
    if [ -z "$sub" ] && [ "$i" -lt "$__mcd_cw" ]; then
      case "$w" in
        -*|'') ;;
        *) sub="$w" ;;
      esac
    fi
  done
  COMPREPLY=()
  if [ -z "$sub" ]; then
    case "$cur" in
      -*) __mcd_git_add_words "$__mcd_git_global_opts" "$cur" "" ;;
      *) __mcd_git_add_words "$__mcd_git_cmds" "$cur" "" ;;
    esac
    return 0
  fi
  case " $__mcd_git_cmds " in
    *" $sub "*) ;;
    *) return 0 ;;
  esac
  case "$prev" in
    --*=*) ;;
    -*) kind=$(__mcd_git_optarg "$sub" "$prev") ;;
  esac
  if [ -n "$kind" ] && [ "$kind" != none ]; then
    __mcd_git_value "$kind" "$cur" ""
  else
    case "$cur" in
      --*=*)
        opt="${cur%%=*}"
        __mcd_git_value "$(__mcd_git_optarg "$sub" "$opt")" "${cur#*=}" "$opt="
        ;;
      -*)
        v="__mcd_git_opts_${sub//-/_}"
        __mcd_git_add_words "${!v}" "$cur" ""
        ;;
      *)
        kind=$(__mcd_git_poskind "$sub")
        case "$kind" in
          meals|staged) __mcd_git_lines __complete "$kind" --shell bash $demo -- "$cur" ;;
          commands) __mcd_git_add_words "$__mcd_git_cmds" "$cur" "" ;;
          values:*) __mcd_git_add_words "${kind#values:}" "$cur" "" ;;
        esac
        ;;
    esac
  fi
  __mcd_git_trim "$cur"
}

# mcd-git / git-mcd 入口
_mcd_git() {
  local -a __mcd_w
  local __mcd_cw=0 i j=-1 w glue=0
  # bash 4+ 会在 COMP_WORDBREAKS 字符处拆词（--qr fancy=b → --qr fancy = b）：把单独的 = / : 和前后相邻的词拼回去；
  # bash 3.2 不拆，这里什么都不做
  for (( i=0; i<${#COMP_WORDS[@]}; i++ )); do
    w="${COMP_WORDS[i]}"
    if [ "$i" -gt 0 ] && [ -n "$w" ] && [ -z "${w//[=:]/}" ]; then
      __mcd_w[j]="${__mcd_w[j]}$w"
      glue=1
    elif [ "$glue" = 1 ]; then
      __mcd_w[j]="${__mcd_w[j]}$w"
      glue=0
    else
      j=$((j + 1))
      __mcd_w[j]="$w"
    fi
    [ "$i" -eq "$COMP_CWORD" ] && __mcd_cw=$j
  done
  __mcd_git_dispatch 0
}
complete -F _mcd_git mcd-git git-mcd

# git mcd 入口：只有加载了 git 自带的 git-completion.bash 才会被调用（它对未知子命令找 _git_<子命令>）。
# words / cword 已由它按 -n =: 重组；它注册时带了 -o nospace，所以完整候选后面自己补空格
_git_mcd() {
  local -a __mcd_w
  local __mcd_cw="$cword" prog="${__git_cmd_idx-}" i
  __mcd_w=("${words[@]}")
  if [ -z "$prog" ] || [ "$prog" = 0 ]; then
    prog=1
    for (( i=1; i<${#__mcd_w[@]}; i++ )); do
      if [ "${__mcd_w[i]}" = mcd ]; then
        prog=$i
        break
      fi
    done
  fi
  __mcd_git_dispatch "$prog"
  for (( i=0; i<${#COMPREPLY[@]}; i++ )); do
    COMPREPLY[i]="${COMPREPLY[i]} "
  done
}
'''


def render_bash(spec: Dict[str, Any]) -> str:
    head = _header(
        "bash",
        [
            "安装（macOS 自带 bash 3.2 也能用）：echo 'eval \"$(mcd-git completion bash)\"' >> ~/.bash_profile",
            "mcd-git <TAB> 总是可用；git mcd <TAB> 只有在加载了 git 自带的 git-completion.bash 之后才生效。",
        ],
    )
    data = [
        "__mcd_git_cmds=" + sh_quote(" ".join(n for n, _ in spec["commands"])),
        "__mcd_git_global_opts=" + sh_quote(" ".join(s for o in spec["global_opts"] for s in o["strings"])),
    ]
    for name, info in spec["subs"].items():
        data.append(f"__mcd_git_opts_{_var(name)}=" + sh_quote(" ".join(s for o in info["options"] for s in o["strings"])))
    return "\n".join([head, "", *data, "", _optarg_func(spec), "", _poskind_func(spec), _BASH_BODY.rstrip("\n"), ""])


# ---------------------------------------------------------------- zsh

_ZSH_BODY = r'''
# 选项取值：$1 = optarg 结果，$2 = 选项名
__mcd_git_zvalue() {
  local -a vals expl
  case "$1" in
    values:*)
      vals=(${=${1#values:}})
      _wanted values expl "$2 的取值" compadd -a vals
      ;;
    file) _files ;;
    free:*) _message -r "$2 ${1#free:}" ;;  # 自由文本不补，只提示要填什么（-r：不依赖用户配 format）
  esac
}

# 餐品名 / 购物车：mcd-git __complete 输出「值\t说明」，值为空的那行是提示
__mcd_git_zcache() {
  local -a vals disp
  local line value desc typed=${(Q)PREFIX}
  for line in "${(@f)$(command mcd-git __complete $1 --shell zsh $2 -- "$typed" 2>/dev/null)}"; do
    [[ -z $line ]] && continue
    value=${line%%$'\t'*}
    desc=${line#*$'\t'}
    if [[ -z $value ]]; then
      _message -r "$desc"
      continue
    fi
    vals+=("$value")
    disp+=("$value  -- $desc")
  done
  (( $#vals )) || return 1
  # -U：候选里有包含匹配（「鸡腿」→ 麦辣鸡腿堡），别让 zsh 再按前缀过滤；compadd 会自己给空格、括号、引号加转义
  compadd -U -l -d disp -a vals
  # 多个候选又不全以已输入的字开头时，-U 会把这个词换成候选的公共前缀（可能是空串，等于吞掉已输入的字）：
  # 这种情况只列出候选、不改命令行
  if (( $#vals > 1 )); then
    for value in "${vals[@]}"; do
      if [[ $value != ${(b)typed}* ]]; then
        compstate[insert]=''
        compstate[list]='list force'
        break
      fi
    done
  fi
}

_mcd-git() {
  local cur=${words[CURRENT]} prev= sub= demo= kind= opt= w n
  local -i i
  (( CURRENT > 2 )) && prev=${words[CURRENT-1]}
  for (( i = 2; i <= $#words; i++ )); do
    w=${words[i]}
    (( i != CURRENT )) && [[ $w == --demo ]] && demo=--demo
    if [[ -z $sub ]] && (( i < CURRENT )) && [[ -n $w && $w != -* ]]; then
      sub=$w
    fi
  done
  if [[ -z $sub ]]; then
    if [[ $cur == -* ]]; then
      _describe -t options '全局选项' __mcd_git_global_descs
    else
      _describe -t commands 'git mcd 命令' __mcd_git_cmd_descs
    fi
    return
  fi
  (( ${__mcd_git_cmds[(Ie)$sub]} )) || return 1
  if [[ $prev == -* && $prev != *=* ]]; then
    kind=$(__mcd_git_optarg $sub $prev)
    if [[ $kind != none ]]; then
      __mcd_git_zvalue $kind $prev
      return
    fi
  fi
  if [[ $cur == --*=* ]]; then
    opt=${cur%%=*}
    kind=$(__mcd_git_optarg $sub $opt)
    if [[ $kind != none ]]; then
      compset -P 1 '*='
      __mcd_git_zvalue $kind $opt
    fi
    return
  fi
  if [[ $cur == -* ]]; then
    n=__mcd_git_opts_descs_${sub//-/_}
    _describe -t options "$sub 的选项" $n
    return
  fi
  kind=$(__mcd_git_poskind $sub)
  case $kind in
    meals|staged) __mcd_git_zcache $kind $demo ;;
    commands) _describe -t commands 'git mcd 命令' __mcd_git_cmd_descs ;;
    values:*)
      local -a vals expl
      vals=(${=${kind#values:}})
      _wanted values expl '取值' compadd -a vals
      ;;
  esac
}

# git mcd 入口：系统 _git 对 `git mcd …` 会调用 _git-mcd（此时 words[1] = mcd，和 _mcd-git 的 words[1] = 程序名同构）
_git-mcd() { _mcd-git "$@" }

if (( ! $+functions[compdef] )); then
  autoload -Uz compinit && compinit -i
fi
compdef _mcd-git mcd-git git-mcd

# 让 git <TAB> 的命令列表里出现 mcd：追加（不覆盖）git 的 user-commands，已有就不重复加
() {
  local -a uc
  zstyle -g uc ':completion:*:*:git:*' user-commands
  if (( ! ${uc[(I)mcd:*]} )); then
    zstyle ':completion:*:*:git:*' user-commands "${uc[@]}" 'mcd:用 git 命令点麦当劳'
  fi
}
'''


def _zsh_desc(name: str, help_text: str) -> str:
    return sh_quote(name.replace(":", "\\:") + (":" + help_text if help_text else ""))


def render_zsh(spec: Dict[str, Any]) -> str:
    head = _header(
        "zsh",
        [
            "安装：echo 'eval \"$(mcd-git completion zsh)\"' >> ~/.zshrc（放在 compinit 之后；没有 compinit 会自动加载）",
            "mcd-git <TAB> 与 git mcd <TAB> 都可用（后者靠系统 _git 调用 _git-mcd）。",
        ],
    )
    data = [
        "__mcd_git_cmds=(" + " ".join(sh_quote(n) for n, _ in spec["commands"]) + ")",
        "__mcd_git_cmd_descs=(" + " ".join(_zsh_desc(n, h) for n, h in spec["commands"]) + ")",
        "__mcd_git_global_descs=(" + " ".join(_zsh_desc(s, o["help"]) for o in spec["global_opts"] for s in o["strings"]) + ")",
    ]
    for name, info in spec["subs"].items():
        data.append(f"__mcd_git_opts_descs_{_var(name)}=(" + " ".join(_zsh_desc(s, o["help"]) for o in info["options"] for s in o["strings"]) + ")")
    return "\n".join([head, "", *data, "", _optarg_func(spec), "", _poskind_func(spec), _ZSH_BODY.rstrip("\n"), ""])


# ---------------------------------------------------------------- fish

_FISH_HELPERS = r'''complete -c mcd-git -f

# 程序名之后还没有子命令（不以 - 开头的词）
function __mcd_git_needs_command
    set -l toks (commandline -opc)
    set -e toks[1]
    for t in $toks
        switch $t
            case '-*'
            case '*'
                return 1
        end
    end
    return 0
end

# 子命令是参数里的某一个
function __mcd_git_using
    set -l toks (commandline -opc)
    set -e toks[1]
    for t in $toks
        switch $t
            case '-*'
            case '*'
                contains -- $t $argv
                return
        end
    end
    return 1
end

# 餐品名 / 购物车：mcd-git __complete 输出「值\t说明」，fish 直接认
function __mcd_git_cached
    set -l demo
    contains -- --demo (commandline -opc); and set demo --demo
    command mcd-git __complete $argv[1] --shell fish $demo -- (commandline -ct) 2>/dev/null
end
'''


def _fish_opt(cond: str, opt: Dict[str, Any]) -> str:
    parts = ["complete -c mcd-git -n " + fish_quote(cond)]
    for s in opt["strings"]:
        parts.append(f"-l {s[2:]}" if s.startswith("--") else f"-s {s[1:]}")
    if opt["takes_value"]:
        if opt["arg"] == "values":
            parts.append("-x -a " + fish_quote(" ".join(opt["values"])))
        elif opt["arg"] == "file":
            parts.append("-r -F")
        else:
            parts.append("-r")
    if opt["help"]:
        parts.append("-d " + fish_quote(opt["help"]))
    return " ".join(parts)


def render_fish(spec: Dict[str, Any]) -> str:
    head = _header(
        "fish",
        [
            "安装：mcd-git completion fish > ~/.config/fish/completions/mcd-git.fish",
            "git mcd <TAB> 是否生效取决于 fish 自带的 git 补全会不会转调 git-mcd 的补全。",
        ],
    )
    lines = [head, "", _FISH_HELPERS]
    for name, help_text in spec["commands"]:
        lines.append(f"complete -c mcd-git -n __mcd_git_needs_command -a {name} -d {fish_quote(help_text)}")
    for opt in spec["global_opts"]:
        lines.append(_fish_opt("__mcd_git_needs_command", opt))
    for name, info in spec["subs"].items():
        cond = f"__mcd_git_using {name}"
        for opt in info["options"]:
            lines.append(_fish_opt(cond, opt))
        pos = info["positional"]
        if not pos or not pos["kind"]:
            continue
        if pos["kind"] in ("meals", "staged"):
            lines.append(f"complete -c mcd-git -n {fish_quote(cond)} -a {fish_quote('(__mcd_git_cached ' + pos['kind'] + ')')}")
        elif pos["kind"] == "commands":
            lines.append(f"complete -c mcd-git -n {fish_quote(cond)} -a {fish_quote(' '.join(n for n, _ in spec['commands']))}")
        elif pos["kind"] == "values":
            lines.append(f"complete -c mcd-git -n {fish_quote(cond)} -a {fish_quote(' '.join(pos['values']))}")
    lines += ["", "# git mcd：把 git-mcd 的补全包装成 mcd-git 的（fish 的 git 补全会不会转调它取决于 fish 版本，未实测）", "complete -c git-mcd -w mcd-git", ""]
    return "\n".join(lines)


# ----------------------------------------------------------------


def render(shell: str, spec: Optional[Dict[str, Any]] = None) -> str:
    if shell not in SHELLS:
        raise ValueError(f"不支持的 shell：{shell}")
    spec = spec or build_spec()
    return {"bash": render_bash, "zsh": render_zsh, "fish": render_fish}[shell](spec)
