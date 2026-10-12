"""0.2.4 隐藏的补全入口 `__complete`：只读本机缓存、绝不调 MCP、不加载 rich / 网络模块；
bash 一行一个转义好的名称，zsh / fish 是「值\\t说明」，zsh 在没候选时给一行提示。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List

import pytest

from mcd_git import menucache
from mcd_git.api import McdApi, Menu
from mcd_git.commands.common import resolve
from mcd_git.context import Context
from mcd_git.mcp_client import McdMcpClient
from mcd_git.state import CN_TZ

from .conftest import FIXED_NOW, Runner

ROOT = Path(__file__).resolve().parents[1]
REMOTE = {"storeCode": "1450001", "storeName": "演示店", "mode": "pickup", "beType": 1, "orderType": 1, "beCode": None}


def _forbid_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """补全里任何 MCP 调用都直接炸（conftest 已经拦了 urlopen / socket）。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("补全不允许调 MCP")

    monkeypatch.setattr(McdMcpClient, "call_tool", boom)
    monkeypatch.setattr(McdApi, "_call", boom)
    monkeypatch.setattr(Context, "api", boom)


def _complete(runner: Runner, *argv: str, **kwargs: Any) -> List[str]:
    code, out = runner.run("__complete", *argv, **kwargs)
    assert code == 0
    return out.splitlines()


def _home() -> Path:
    return Path(os.environ["MCD_GIT_HOME"])


def _seed_real(home: Path, meals: Any, now: datetime) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "state.json").write_text(json.dumps({"version": 1, "remote": REMOTE, "index": []}, ensure_ascii=False), encoding="utf-8")
    menucache.write(home, REMOTE, meals, now)


def test_demo_completes_without_cache(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_mcp(monkeypatch)
    lines = _complete(runner, "meals", "--demo", "--shell", "bash", "--", "巨")
    assert lines == ["巨无霸", "巨无霸套餐"]
    assert not (_home() / "demo" / "menu-cache.json").exists()  # 只读，不写缓存
    lines = _complete(runner, "meals", "--shell", "bash", "--", "巨", env={"MCD_GIT_DEMO": "1"})
    assert lines == ["巨无霸", "巨无霸套餐"]


def test_real_mode_without_cache_is_quiet_and_zsh_hints(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_mcp(monkeypatch)
    assert _complete(runner, "meals", "--shell", "zsh", "--", "巨") == ["\t先运行 git mcd init 选门店"]
    _home().mkdir(parents=True)
    (_home() / "state.json").write_text(json.dumps({"remote": REMOTE}), encoding="utf-8")
    assert _complete(runner, "meals", "--shell", "bash", "--", "巨") == []
    assert _complete(runner, "meals", "--shell", "zsh", "--", "巨") == ["\t先运行 git mcd ls-remote 刷新菜单"]
    assert _complete(runner, "meals", "--shell", "fish", "--", "巨") == []
    assert _complete(runner, "meals", "--", "巨") == []
    (_home() / "state.json").write_text("{坏了", encoding="utf-8")  # 坏的 state.json 也不能报错
    assert _complete(runner, "meals", "--shell", "zsh", "--", "巨") == ["\t先运行 git mcd init 选门店"]
    assert _complete(runner, "bogus", "--shell", "zsh") == []


def test_real_mode_reads_cache_written_by_ls_remote(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.run("init", "--city", "示例市", "--keyword", "码农路", "--pick", "1", mock=True)[0] == 0
    assert runner.run("ls-remote", mock=True)[0] == 0
    calls = len(runner.calls)
    _forbid_mcp(monkeypatch)
    lines = _complete(runner, "meals", "--shell", "zsh", "--", "麦辣")
    assert lines == ["麦辣鸡腿堡\t单品 · 1002", "麦辣鸡腿堡套餐\t套餐 · 9900001002"]
    assert _complete(runner, "meals", "--shell", "fish", "--", "麦辣") == lines
    assert _complete(runner, "meals", "--shell", "bash", "--", "麦辣") == ["麦辣鸡腿堡", "麦辣鸡腿堡套餐"]
    assert _complete(runner, "meals", "--shell", "zsh", "--", "990000100")[0] == "9900001001\t巨无霸套餐 · 套餐"
    assert _complete(runner, "meals", "--shell", "bash", "--", "990000100") == []  # bash 只给名称
    assert len(runner.calls) == calls  # 补全零 MCP 调用


def test_store_switch_and_expiry_drop_candidates(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_mcp(monkeypatch)
    _seed_real(_home(), [{"code": "1", "name": "巨无霸", "type": "单品"}], FIXED_NOW)
    assert _complete(runner, "meals", "--shell", "bash", "--", "巨") == ["巨无霸"]
    assert _complete(runner, "meals", "--shell", "zsh", "--", "巨", now=FIXED_NOW + timedelta(hours=25)) == ["\t先运行 git mcd ls-remote 刷新菜单"]
    (_home() / "state.json").write_text(json.dumps({"remote": dict(REMOTE, storeCode="999")}), encoding="utf-8")
    assert _complete(runner, "meals", "--shell", "zsh", "--", "巨") == ["\t先运行 git mcd ls-remote 刷新菜单"]
    assert _complete(runner, "meals", "--shell", "bash", "--", "巨") == []


def test_staged_lists_cart_names(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.run("--demo", "add", "巨无霸", "中薯条")[0] == 0
    _forbid_mcp(monkeypatch)
    lines = _complete(runner, "staged", "--demo", "--shell", "zsh")
    index = json.loads((_home() / "demo" / "state.json").read_text(encoding="utf-8"))["index"]
    assert lines == [f"{x['name']}\tx{x['qty']} · {x['id']}" for x in index]
    assert [x.split("\t")[0] for x in lines] == ["巨无霸", "中薯条"]
    assert _complete(runner, "staged", "--demo", "--shell", "bash", "--", "薯") == ["中薯条"]
    assert _complete(runner, "staged", "--shell", "zsh") == []  # 真实模式购物车是空的


def test_complete_is_hidden_from_help(runner: Runner) -> None:
    code, out = runner.run("help")
    assert code == 0 and "__complete" not in out
    code, out = runner.run("-h")
    assert "__complete" not in out


# ---------------------------------------------------------------- 子进程：轻量、安静

PROBE = (
    "import sys; sys.argv=['mcd-git','__complete','meals','--shell','bash','--','巨']; "
    "from mcd_git.entry import main; main(); "
    "bad=[m for m in sys.modules if m.split('.')[0] in ('rich','qrcode') or m in "
    "('mcd_git.api','mcd_git.mcp_client','mcd_git.cli','mcd_git.context','http.client','urllib.request')]; "
    "sys.stderr.write(repr(bad)); sys.exit(1 if bad else 0)"
)


def _sub_env(home: Path) -> dict:
    return {"MCD_GIT_HOME": str(home), "HOME": str(home), "PYTHONPATH": str(ROOT / "src"), "PATH": os.environ.get("PATH", "")}


def test_hot_path_does_not_import_heavy_or_network_modules(tmp_path: Path) -> None:
    home = tmp_path / "cfg"
    _seed_real(home, [{"code": "1001", "name": "巨无霸", "type": "单品"}, {"code": "9900001001", "name": "巨无霸套餐", "type": "套餐"}], datetime.now(CN_TZ))
    start = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, env=_sub_env(home), timeout=30, stdin=subprocess.DEVNULL)
    elapsed = time.monotonic() - start
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["巨无霸", "巨无霸套餐"]
    assert elapsed < 1.0  # 宽松上限防回归；真正的 <100ms 人工计时


def test_module_entry_is_silent_on_missing_cache(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mcd_git", "__complete", "meals", "--shell", "zsh", "--", "巨"],
        capture_output=True, text=True, env=_sub_env(tmp_path / "cfg"), timeout=30, stdin=subprocess.DEVNULL,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "\t先运行 git mcd init 选门店\n", "")


# ---------------------------------------------------------------- bash 转义往返

ODD_NAMES = [
    "巨无霸 套餐",
    "麦乐鸡(5块)",
    "麦辣鸡腿堡（中）",
    "Tom's \"Best\" 汉堡",
    "麦咖啡™美式",
    "可口可乐®零度",
    "汉堡/薯条+可乐",
    "尾随空格汉堡 ",
    "【限定】安格斯[厚]*?",
    "特价`$x`!&;|<>#~=:@\\{}",
]


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="没有 /bin/bash")
@pytest.mark.parametrize("name", ODD_NAMES)
def test_bash_escape_round_trips_through_bash_and_resolve(name: str) -> None:
    escaped = menucache.bash_escape(menucache.clean_name(name))
    assert all(ch not in escaped for ch in "\t\n")
    proc = subprocess.run(["/bin/bash", "-c", 'printf "%s\\n" ' + escaped], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    back = proc.stdout.rstrip("\n")
    assert back == name.strip()
    menu = Menu({"meals": {"9001": {"name": name, "currentPrice": "10"}, "1001": {"name": "巨无霸", "currentPrice": "24"}}})
    assert resolve(menu, back) == "9001"


def test_bash_escape_keeps_non_ascii_and_safe_ascii() -> None:
    assert menucache.bash_escape("麦辣鸡腿堡（中）™") == "麦辣鸡腿堡（中）™"
    assert menucache.bash_escape("a-b_c.d/e+f%g,h^i") == "a-b_c.d/e+f%g,h^i"
    assert menucache.bash_escape("巨无霸 套餐(大)") == "巨无霸\\ 套餐\\(大\\)"
