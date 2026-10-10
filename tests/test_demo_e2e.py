"""--demo 端到端：init → status → add → diff → rebase → commit → push → log --graph → show → cherry-pick HEAD。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from mcd_git.commands.log import graph
from mcd_git import history

from .conftest import Runner

ROOT = Path(__file__).resolve().parents[1]


def test_full_demo_flow(runner: Runner) -> None:
    code, out = runner.run("--demo", "status")
    assert code == 0
    assert "On branch lunch" in out and "mcd.cn:演示·代码港店" in out and "No commits yet" in out

    code, out = runner.run("--demo", "init")
    assert code == 0 and "init --pick" in out
    code, out = runner.run("--demo", "init", "--pick", "1")
    assert code == 0 and "Reinitialized demo playground" in out

    code, out = runner.run("--demo", "add", "巨无霸", "中薯条", "中可乐", "--no-pickle")
    assert code == 0 and "add '巨无霸' x1" in out and "去酸黄瓜" in out

    code, out = runner.run("--demo", "diff")
    assert code == 0
    assert "--- a/菜单价  ¥47.50" in out and "+++ b/应付  ¥45.50" in out
    assert re.search(r"^-  中可乐 .*¥10\.00", out, re.M) and re.search(r"^\+  中可乐 .*¥8\.00", out, re.M)

    code, out = runner.run("--demo", "rebase")
    assert code == 0 and "Successfully rebased and updated refs/heads/lunch." in out
    assert "¥45.50 → ¥33.50（省 ¥12.00）" in out

    code, out = runner.run("--demo", "diff")
    assert "a/不用券" in out and "b/用券后" in out and "券：示例·巨无霸套餐立减 5 元" in out

    code, out = runner.run("--demo", "commit", "-m", "发版前的午饭")
    assert code == 0 and re.search(r"\[lunch \(root-commit\) [0-9a-f]{7}\] 发版前的午饭", out)

    code, data = runner.json("--demo", "push", "--at", "12:20", "--locker", "--no-tableware")
    assert code == 0 and data["data"]["status"] == "confirmation_required"
    short = data["data"]["confirm"]
    # 给 Agent 的下一步命令原样带上 --demo 和 --json，照抄不会误下到真实账户
    assert data["data"]["next"] == f"git mcd --demo push --at '2026-10-24 12:20' --locker --no-tableware --confirm {short} --json"
    code, out = runner.run("--demo", "push", "--at", "12:20", "--locker", "--no-tableware", "--confirm", short)
    assert code == 0
    assert "To mcd.cn:演示·代码港店" in out and "* [new order]      lunch -> 巨无霸套餐" in out
    assert "请在 12:05 前付款" in out and "https://example.com/mcd-git-demo/pay?orderId=1024" in out
    assert "▀" in out or "█" in out  # 终端二维码

    code, out = runner.run("--demo", "status")
    assert "待支付，请在 12:05 前付款" in out

    code, out = runner.run("--demo", "log", "--graph")
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("* " + short) and "(HEAD -> lunch, origin/lunch)" in lines[0]
    assert any(line.startswith("| * ") and "(breakfast)" in line for line in lines)
    assert "|/" in lines

    code, out = runner.run("--demo", "show")
    assert code == 0 and "配餐中" in out and "取餐码" in out and "10241" in out
    assert "密码 2048" in out and "预约:    2026-10-24 12:20" in out

    code, out = runner.run("--demo", "cherry-pick", "HEAD")
    assert code == 0 and "cherry-pick " + short in out
    code, data = runner.json("--demo", "status")
    staged = data["data"]["index"]
    assert [x["name"] for x in staged] == ["巨无霸套餐"] and "coupon" not in staged[0]  # 券不跟着拣回来

    code, out = runner.run("--demo", "cherry-pick", "HEAD")
    assert code == 1 and "would be overwritten" in out

    # 再 commit 一次：HEAD 变成还没 push 的新 commit，show 显示快照，并指回最近一次 push 的订单
    code, out = runner.run("--demo", "commit", "-m", "再来一单")
    assert code == 0
    code, data = runner.json("--demo", "status")
    assert data["data"]["ahead"] == 1
    code, out = runner.run("--demo", "show")
    assert code == 0 and "commit 时核价" in out and "git mcd show 1024" in out


def test_pending_hint_points_at_the_pushed_order(runner: Runner) -> None:
    runner.run("--demo", "add", "热拿铁")
    runner.run("--demo", "commit", "-m", "下午茶")
    code, data = runner.json("--demo", "push")
    short = data["data"]["confirm"]
    code, data = runner.json("--demo", "push", "--confirm", short)
    order_id = data["data"]["orderId"]
    code, data = runner.json("--demo", "status")
    assert data["data"]["pendingPayment"]["show"] == "git mcd show"
    # 没付款、也没 show，就又拣了一单并 commit：提示要带上订单号
    runner.run("--demo", "cherry-pick", "HEAD")
    runner.run("--demo", "commit", "-m", "再来一杯")
    code, out = runner.run("--demo", "status")
    assert f'付完运行 "git mcd show {order_id}" 查看取餐码' in out


def test_cherry_pick_then_push_asks_for_commit(runner: Runner) -> None:
    """再来一单：cherry-pick 只放回购物车，不 commit 就 push 要报错，不能回 up-to-date 让人以为下好了。"""
    runner.run("--demo", "add", "热拿铁", mock=True)
    runner.run("--demo", "commit", "-m", "下午茶", mock=True)
    code, data = runner.json("--demo", "push", mock=True)
    code, data = runner.json("--demo", "push", "--confirm", data["data"]["confirm"], mock=True)
    assert code == 0 and data["data"]["status"] == "pushed"
    placed = runner.count("create-order")
    assert placed == 1

    runner.run("--demo", "cherry-pick", "HEAD", mock=True)
    code, data = runner.json("--demo", "push", mock=True)
    assert code == 128 and data["ok"] is False and "购物车还没 commit" in data["error"]["message"]
    assert any("git mcd commit" in h for h in data["error"]["hints"])
    code, out = runner.run("--demo", "push", mock=True)
    assert code == 128 and "fatal: 购物车还没 commit" in out and "Everything up-to-date" not in out
    assert runner.count("create-order") == placed  # cherry-pick 之后直接 push：一次 create-order 都没调

    # README 里的命令链：cherry-pick HEAD → commit → push（预览）→ 用户同意后 --confirm
    code, data = runner.json("--demo", "commit", "-m", "再来一单", mock=True)
    assert code == 0
    code, data = runner.json("--demo", "push", mock=True)
    assert data["data"]["status"] == "confirmation_required"
    code, data = runner.json("--demo", "push", "--confirm", data["data"]["confirm"], mock=True)
    assert code == 0 and data["data"]["status"] == "pushed"
    assert runner.count("create-order") == placed + 1

    # 购物车空着、HEAD 也下过单：up-to-date，提示怎么再来一单
    code, out = runner.run("--demo", "push", mock=True)
    assert code == 0 and "Everything up-to-date" in out and "cherry-pick HEAD" in out and "git mcd commit" in out


def test_cart_changes_after_commit_block_push(runner: Runner) -> None:
    """commit 之后又 add：push 只按 HEAD 下单，所以直接报错（git 风格文案）；给 Agent 的两条命令带上 --demo。"""
    runner.run("--demo", "add", "巨无霸", mock=True)
    code, data = runner.json("--demo", "commit", "-m", "午饭", mock=True)
    short = data["data"]["short"]
    runner.run("--demo", "add", "热拿铁", mock=True)
    code, out = runner.run("--demo", "push", "--dry-run", mock=True)
    assert code == 1
    assert f"error: 购物车里有还没 commit 的改动，这一单（{short}）不包含它们" in out
    assert "hint: 想并进这一单：git mcd commit --amend" in out and "hint: 不要这些改动：git mcd reset" in out
    code, data = runner.json("--demo", "push", mock=True)
    assert code == 1 and data["error"]["detail"]["code"] == "uncommitted_changes"
    assert data["error"]["detail"]["amend"] == "git mcd --demo commit --amend --json"
    assert data["error"]["detail"]["discard"] == "git mcd --demo reset --json"
    assert data["error"]["detail"]["withdraw"] == "git mcd --demo reset --soft 'HEAD~1' --json"
    assert runner.count("create-order") == 0


def test_unpushed_head_with_cart_points_to_amend_not_push(runner: Runner) -> None:
    """HEAD 没 push、购物车里又有东西时直接 push 会报 uncommitted_changes：commit / status / show 都先提示 amend / reset，
    不再让人先去 git mcd push。购物车空着时提示照旧。"""
    runner.run("--demo", "add", "热拿铁")
    code, data = runner.json("--demo", "commit", "-m", "下午茶")
    short = data["data"]["short"]
    code, data = runner.json("--demo", "commit", "-m", "再来")
    assert code == 1 and data["error"]["hints"] == ["先 git mcd push 下单", "或 git mcd commit --amend 把购物车并进去", "或 git mcd reset --soft HEAD~1 撤回重来"]
    code, out = runner.run("--demo", "status")
    assert '(use "git mcd push" to place the order)' in out and "--amend" not in out
    code, out = runner.run("--demo", "show")
    assert "hint: 还没 push：git mcd push 下单（会先确认）" in out

    runner.run("--demo", "add", "菠萝派")
    code, data = runner.json("--demo", "commit", "-m", "再来")
    assert code == 1 and data["error"]["message"] == f"HEAD {short}（下午茶）还没 push"
    assert data["error"]["hints"] == ["git mcd commit --amend 把购物车并进这一单", "不要购物车里的：git mcd reset，再 git mcd push 下单", "git mcd reset --soft HEAD~1 撤回重来"]
    code, out = runner.run("--demo", "commit", "-m", "再来")
    assert code == 1 and "hint: git mcd commit --amend 把购物车并进这一单" in out and "先 git mcd push" not in out
    code, out = runner.run("--demo", "status")
    assert code == 0
    assert '(use "git mcd commit --amend" to add the cart to this order)' in out
    assert '(use "git mcd reset" to drop the cart, then "git mcd push" to place the order)' in out
    assert '(use "git mcd push" to place the order)' not in out
    code, out = runner.run("--demo", "show")
    assert code == 0
    assert "hint: 还没 push，购物车里还有没 commit 的东西：git mcd commit --amend 并进这一单" in out
    assert "hint: 不要购物车里的：git mcd reset，再 git mcd push 下单" in out
    assert "还没 push：git mcd push 下单" not in out

    # 照第一条提示做，就能预览下单了
    code, data = runner.json("--demo", "commit", "--amend")
    assert code == 0 and [x["name"] for x in data["data"]["lines"]] == ["热拿铁", "菠萝派"]
    code, data = runner.json("--demo", "push")
    assert code == 0 and data["data"]["status"] == "confirmation_required"


def test_push_to_closed_store_fails_like_git(runner: Runner) -> None:
    runner.run("--demo", "add", "巨无霸")
    runner.run("--demo", "commit", "-m", "夜里")
    code, out = runner.run("--demo", "push", "--at", "23:30")  # 演示·代码港店 23:00 打烊
    assert code == 1
    assert "remote: 门店已打烊（code 600031" in out
    assert "error: failed to push some refs: 门店已打烊" in out
    code, data = runner.json("--demo", "status")
    assert data["data"]["ahead"] == 1 and data["data"]["pendingPayment"] is None


def test_cherry_pick_unpushed_head_is_refused(runner: Runner) -> None:
    """HEAD 还没 push 时它本来就是这一单：cherry-pick 它会把同样的餐品再放一份进购物车，commit --amend 之后这一单就翻倍了，所以拒绝。
    push 之后 cherry-pick HEAD（再来一单）照常。"""
    runner.run("--demo", "add", "巨无霸", "中薯条", "中可乐", "--no-pickle")
    runner.run("--demo", "rebase")
    code, data = runner.json("--demo", "commit", "-m", "午饭")
    short = data["data"]["short"]
    code, out = runner.run("--demo", "cherry-pick", "HEAD")
    assert code == 1
    assert f"error: HEAD {short}（午饭）还没 push，它本来就是这一单，不用 cherry-pick" in out
    assert "hint: 直接下单：git mcd push" in out
    assert "hint: 想多点一份：git mcd add <餐品>，再 git mcd commit --amend" in out
    for target in ("HEAD", short):  # 写哈希也一样
        code, data = runner.json("--demo", "cherry-pick", target)
        assert code == 1 and data["ok"] is False and data["error"]["type"] == "error"
        assert data["error"]["detail"] == {"code": "empty_cherry_pick", "commit": short}
    code, data = runner.json("--demo", "status")
    assert data["data"]["index"] == []  # 购物车没动

    code, data = runner.json("--demo", "push")
    code, data = runner.json("--demo", "push", "--confirm", data["data"]["confirm"])
    assert code == 0 and data["data"]["status"] == "pushed"
    code, data = runner.json("--demo", "cherry-pick", "HEAD")
    assert code == 0 and [x["name"] for x in data["data"]["index"]] == ["巨无霸套餐"]


def test_cherry_pick_reports_conflicts(runner: Runner) -> None:
    code, out = runner.run("--demo", "cherry-pick", "1024202610230812000000000003")
    assert code == 0
    assert "CONFLICT (not on menu): 猪柳蛋麦满分" in out and "add '热拿铁' x1" in out


def test_every_command_speaks_json(runner: Runner, tmp_path: Path) -> None:
    runner.run("--demo", "add", "热拿铁")
    commands = (["status"], ["ls-remote", "薯条"], ["diff"], ["rebase"], ["log", "--oneline"], ["fetch"], ["show", "1024202610221205000000000002"], ["card", "-o", str(tmp_path / "c.svg")])
    for argv in commands:
        code, data = runner.json("--demo", *argv)
        assert code == 0 and data["ok"] is True and data["demo"] is True, argv
    code, data = runner.json("--demo", "fetch")
    assert len(data["data"]["claimable"]) == 3
    code, data = runner.json("--demo", "show", "nope!")
    assert code == 128 and data["ok"] is False and data["error"]["type"] == "fatal"


def test_help_table_everywhere(runner: Runner) -> None:
    for argv in ([], ["help"], ["-h"], ["--help"]):
        code, out = runner.run(*argv)
        assert code == 0 and "cherry-pick HEAD" in out and "git mcd help" in out and "card [-o 文件]" in out, argv
    code, data = runner.json("help")
    assert any(c["writesAccount"] for c in data["data"]["commands"])
    code, out = runner.run("help", "nope")
    assert code == 1


def test_real_mode_without_token_or_remote(runner: Runner) -> None:
    code, out = runner.run("status")
    assert code == 128 and "git mcd init" in out
    code, out = runner.run("init")
    assert code == 128 and "Token" in out and "--demo" in out


def test_graph_lanes() -> None:
    rows = [
        {"hash": "a" * 40, "branch": "lunch", "head": True},
        {"hash": "b" * 40, "branch": "lunch"},
        {"hash": "c" * 40, "branch": "breakfast"},
        {"hash": "d" * 40, "branch": "lunch"},
    ]
    drawn = [(prefix.plain, connector.plain if connector else None) for prefix, connector in graph(rows)]
    assert drawn == [("* ", None), ("* ", None), ("| * ", "|/"), ("* ", None)]
    assert history.order_hash("1") == history.order_hash("1") != history.order_hash("2")


def test_entry_points_declared() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^git-mcd = "mcd_git\.cli:main"$', text, re.M)
    assert re.search(r'^mcd-git = "mcd_git\.cli:main"$', text, re.M)


def test_python_module_entry(tmp_path: Path) -> None:
    env = dict(os.environ, MCD_GIT_HOME=str(tmp_path / "cfg"), COLUMNS="100")
    proc = subprocess.run([sys.executable, "-m", "mcd_git", "--demo", "status"], capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert "On branch lunch" in proc.stdout
