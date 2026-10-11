"""config_home 的跨平台解析与向后兼容：全部 monkeypatch，不碰真实 ~/.config、不连 MCP。"""
from __future__ import annotations

from pathlib import Path

import pytest

from mcd_git.state import config_home


def _fake_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    # 模拟 Windows：patch 平台判断函数 _is_windows，而不是改全局 os.name
    # （改 os.name 会连累 pathlib 在非 Windows 机器上无法实例化 WindowsPath）。
    monkeypatch.setattr("mcd_git.state._is_windows", lambda: True)


def _fake_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mcd_git.state._is_windows", lambda: False)


def test_config_home_override_wins(tmp_path: Path) -> None:
    # MCD_GIT_HOME 覆盖一切，不受平台影响（demo 子目录逻辑由 state_root 处理，这里不变）
    env = {"MCD_GIT_HOME": str(tmp_path / "cfg")}
    assert config_home(env) == tmp_path / "cfg"


def test_config_home_legacy_dir_is_reused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 向后兼容：~/.config/mcd-git 已存在就继续用它，别因为换平台推荐目录而丢账本
    _fake_windows(monkeypatch)
    legacy = Path.home() / ".config" / "mcd-git"
    legacy.mkdir(parents=True, exist_ok=True)
    env = {"APPDATA": str(tmp_path / "AppData")}  # 即便是 Windows + 有 APPDATA
    assert config_home(env) == legacy


def test_config_home_windows_appdata_when_no_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_windows(monkeypatch)
    appdata = tmp_path / "AppData"
    assert config_home({"APPDATA": str(appdata)}) == appdata / "mcd-git"


def test_config_home_xdg_when_no_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_posix(monkeypatch)
    xdg = tmp_path / "xdg"
    assert config_home({"XDG_CONFIG_HOME": str(xdg)}) == xdg / "mcd-git"


def test_config_home_posix_default_when_no_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_posix(monkeypatch)
    assert config_home({}) == Path.home() / ".config" / "mcd-git"


def test_config_home_xdg_wins_over_appdata_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_windows(monkeypatch)
    xdg = tmp_path / "xdg"
    assert config_home({"XDG_CONFIG_HOME": str(xdg), "APPDATA": str(tmp_path / "AppData")}) == xdg / "mcd-git"
