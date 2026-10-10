"""pull（领券）的确认闸：走 demo=False 的真实代码路径，MCP 换成 mock（RecordingClient），绝不联网，
也从不对真实账户调 auto-bind-coupons。另外覆盖写操作放行收紧：许可绑定 tool，四个永不调用的写操作带着许可也发不出去。"""
from __future__ import annotations

import http.client
import io
import json
import os
import shlex
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from rich.console import Console

from mcd_git import gate
from mcd_git.api import McdApi, normalize_bind
from mcd_git.cli import main
from mcd_git.demo import DemoClient
from mcd_git.errors import GitMcdError, McdError
from mcd_git.mcp_client import McdMcpClient

from .conftest import FIXED_NOW, Runner, mcp_result
from .test_client import FakeServer, TruncatedResponse
from .test_push_gate import PUSH, _jsonrpc_error, _setup

NEVER_TOOLS = ("mall-create-order", "draw-lottery", "party-order-create", "delivery-create-address")
COUPONS = ["示例·新品尝鲜券", "示例·咖啡第二杯半价券", "示例·小食立减券"]
SHORTS = ["新品尝鲜券", "咖啡第二杯半价券", "小食立减券"]
REJECTED_HINTS = ["什么都没领；处理完上面的问题后重新 git mcd pull 预览"]


def _envelope(data: Any) -> Dict[str, Any]:
    return mcp_result({"success": True, "code": 200, "message": "请求成功", "data": data})


def _coupons(*names: str, status: str = "CAN_GET") -> Any:
    rows = [{"couponName": n, "couponImage": "", "couponStatus": status, "label": "可领取"} for n in names]
    return lambda args, allow_write: _envelope(rows)


def _next_argv(data: Dict[str, Any]) -> List[str]:
    """照抄 data.next：去掉开头的 git mcd 和末尾的 --json（runner.json 自己会加）。"""
    argv = shlex.split(data["data"]["next"])
    assert argv[:2] == ["git", "mcd"] and argv[-1] == "--json", argv
    return argv[2:-1]


def _preview(runner: Runner) -> List[str]:
    code, data = runner.json("pull", mock=True)
    assert code == 0 and data["data"]["status"] == "confirmation_required", data
    return _next_argv(data)


def _pending(runner: Runner) -> Optional[Dict[str, Any]]:
    path = Path(runner.tmp_path) / "mcd-git" / "state.json"
    return json.loads(path.read_text(encoding="utf-8")).get("pending_pull") if path.is_file() else None


def _truncated(tool: str) -> Any:
    """响应读到一半连接断了：经真实的 McdMcpClient 解析（FakeServer 回放，不联网），变成 transport 错误。"""

    def call(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        client = McdMcpClient("unit-test-token", opener=FakeServer([lambda payload: TruncatedResponse(b"")]), min_interval=0)
        return client.call_tool(tool, args, allow_write=allow_write)

    return call


# ---------------------------------------------------------------- 写操作放行收紧


def test_demo_client_blocks_never_tools(tmp_path: Path) -> None:
    client = DemoClient(tmp_path, now=FIXED_NOW)
    for tool in NEVER_TOOLS:
        with pytest.raises(GitMcdError) as err:
            client.call_tool(tool, {}, allow_write=True)
        assert err.value.fatal and "push / revert / pull 的确认闸" in err.value.message
    with pytest.raises(GitMcdError):
        client.call_tool("auto-bind-coupons", {})  # 没有确认闸放行
    assert client.calls == []


def test_approval_is_bound_to_its_tool(tmp_path: Path) -> None:
    client = DemoClient(tmp_path, now=FIXED_NOW)
    api = McdApi(client)
    with pytest.raises(GitMcdError):
        api.auto_bind_coupons(gate.issue("create-order", {"storeCode": "1000001"}))  # push 的许可不能拿来领券
    with pytest.raises(GitMcdError) as err:
        api._call("auto-bind-coupons", {}, cache=False, approval=gate.issue("cancel-order", {}))
    assert "许可和要调用的 tool 不一致" in err.value.message
    with pytest.raises(GitMcdError):
        api.create_order(gate.issue("auto-bind-coupons", {}), {})  # 反过来也一样
    with pytest.raises(GitMcdError):
        api._call("draw-lottery", {}, cache=False, approval=gate.issue("draw-lottery", {}))  # 拿着许可也调不出去
    assert client.calls == []

    approval = gate.issue("auto-bind-coupons", {})
    assert api.auto_bind_coupons(approval)["successCount"] == 3
    with pytest.raises(GitMcdError) as err:
        approval.check("auto-bind-coupons", {})
    assert "用过了" in err.value.message
    assert [name for name, _ in client.calls] == ["auto-bind-coupons"]


def test_normalize_bind_shapes() -> None:
    full = {
        "successCount": 2,
        "failedCount": 1,
        "totalCount": 3,
        "successCoupons": [
            {"couponId": "cid-1", "couponName": "A", "couponCode": "CODE-1", "couponImage": "https://example.com/a.png"},
            {"couponId": "cid-2", "couponName": " B ", "couponCode": "CODE-2"},
        ],
        "failedCoupons": [{"couponId": "cid-3", "couponName": "C", "reason": "已达领取上限"}],
    }
    out = normalize_bind(full)
    assert out == {"success": [{"name": "A"}, {"name": "B"}], "failed": [{"name": "C", "reason": "已达领取上限"}], "successCount": 2, "failedCount": 1, "totalCount": 3}
    assert not any(secret in json.dumps(out) for secret in ("cid-", "CODE-", "png"))  # couponId、券码、图片都不往上传
    # 只有计数没有名单
    assert normalize_bind({"successCount": 3, "failedCount": "0"}) == {"success": [], "failed": [], "successCount": 3, "failedCount": 0, "totalCount": 3}
    # 只有名单没有计数
    lists = {"successCoupons": [{"couponName": "A"}], "failedCoupons": [{"couponName": "B"}]}
    assert normalize_bind(lists) == {"success": [{"name": "A"}], "failed": [{"name": "B", "reason": ""}], "successCount": 1, "failedCount": 1, "totalCount": 2}
    # 全空
    assert normalize_bind({}) == {"success": [], "failed": [], "successCount": 0, "failedCount": 0, "totalCount": 0}
    # 乱七八糟的类型：计数不是非负整数就不信，列表不是列表就当空，计数比名单短时以名单为准
    weird = {"successCount": "lots", "failedCount": True, "totalCount": -1, "successCoupons": "A,B", "failedCoupons": [None, "x", {"couponName": "C"}]}
    assert normalize_bind(weird) == {"success": [], "failed": [{"name": "C", "reason": ""}], "successCount": 0, "failedCount": 1, "totalCount": 1}
    assert normalize_bind({"successCount": 1, "successCoupons": [{"couponName": "A"}, {"couponName": "B"}]})["successCount"] == 2


# ---------------------------------------------------------------- 预览


def test_preview_never_claims(runner: Runner) -> None:
    code, data = runner.json("pull", mock=True)
    assert code == 0 and data["ok"] is True and data["data"]["status"] == "confirmation_required"
    summary = data["data"]["summary"]
    assert summary["count"] == 3
    assert [c["name"] for c in summary["coupons"]] == COUPONS and [c["short"] for c in summary["coupons"]] == SHORTS
    assert "一次" in summary["note"] and "不能只领" in summary["note"]
    fp = data["data"]["confirm"]
    assert len(fp) == 7 and data["data"]["next"] == f"git mcd pull --confirm {fp} --json"
    assert data["data"]["expiresInMinutes"] == 10
    assert data["mcpCalls"] == {"available-coupons": 1}
    assert _pending(runner)["count"] == 3 and _pending(runner)["fingerprint"].startswith(fp)

    code, data = runner.json("pull", "--dry-run", mock=True)
    assert code == 0 and data["data"]["status"] == "confirmation_required"
    assert data["data"]["confirm"] == fp  # 同一份列表，同一个指纹
    code, out = runner.run("pull", "--dry-run", mock=True, interactive=True, answers=["y"])  # --dry-run 连问都不问
    assert code == 0 and "即将 pull：一次领取全部 3 张可领券" in out and "不能只领其中几张" in out
    assert f"用户确认无误后运行：git mcd pull --confirm {fp}" in out
    code, out = runner.run("pull", mock=True)  # 管道里（非交互）也只出摘要
    assert code == 0 and all(name in out for name in COUPONS) and "一次全部领进你的麦当劳账户" in out
    assert runner.count("auto-bind-coupons") == 0


def test_fingerprint_follows_the_list_not_its_order(runner: Runner) -> None:
    code, data = runner.json("pull", mock=True)
    fp = data["data"]["confirm"]
    runner.hooks["available-coupons"] = _coupons(*reversed(COUPONS))
    code, data = runner.json("pull", mock=True)
    assert data["data"]["confirm"] == fp  # 顺序变了不算变
    runner.hooks["available-coupons"] = _coupons(*COUPONS, COUPONS[0])  # 同名券重复出现：多一张就是另一份列表
    code, data = runner.json("pull", mock=True)
    assert data["data"]["summary"]["count"] == 4 and data["data"]["confirm"] != fp
    runner.hooks["available-coupons"] = _coupons("示例·已经领过的券", status="RECEIVED")  # 只算 CAN_GET（和 fetch 一样）
    code, data = runner.json("pull", mock=True)
    assert code == 0 and data["data"]["status"] == "up-to-date"
    assert runner.count("auto-bind-coupons") == 0


def test_interactive_prompt(runner: Runner) -> None:
    prompts: List[str] = []
    console = Console(file=io.StringIO(), width=120, highlight=False, markup=False, emoji=False, color_system=None)
    code = main(["pull"], console=console, env=dict(os.environ), client_factory=runner.factory, input_fn=lambda p: prompts.append(p) or "n", interactive=True, now=FIXED_NOW)
    assert code == 0 and prompts == ["确认领券？会把这 3 张券一次全部领进你的麦当劳账户（接口不支持只领其中几张）[y/N] "]
    assert "没有领券。" in console.file.getvalue()
    assert runner.count("auto-bind-coupons") == 0 and _pending(runner) is None

    code, out = runner.run("pull", mock=True, interactive=True, answers=["y"])
    assert code == 0, out
    assert runner.count("auto-bind-coupons") == 1
    assert runner.clients[-1].args_of("auto-bind-coupons") == [{}]  # 没有参数：一次领全部
    for text in ("即将 pull", "From mcd.cn:麦麦省", "Fast-forward", "领到 3 张，失败 0 张", "hint: 下单前跑一次 git mcd rebase"):
        assert text in out
    for name, short in zip(COUPONS, SHORTS):
        assert f" * [new coupon]      {name}" in out and f"-> coupons/{short}" in out
    assert _pending(runner) is None


# ---------------------------------------------------------------- --confirm


def test_confirm_claims_once_then_up_to_date(runner: Runner) -> None:
    argv = _preview(runner)
    code, data = runner.json(*argv, mock=True)
    assert code == 0 and data["data"]["status"] == "pulled", data
    assert (data["data"]["successCount"], data["data"]["failedCount"], data["data"]["totalCount"]) == (3, 0, 3)
    assert data["data"]["claimed"] == [{"name": n, "short": s} for n, s in zip(COUPONS, SHORTS)] and data["data"]["failed"] == []
    assert "rebase" in data["data"]["next"]
    assert data["mcpCalls"] == {"available-coupons": 1, "auto-bind-coupons": 1}  # 确认前重新拉一次列表、比指纹
    assert runner.count("auto-bind-coupons") == 1 and runner.count("available-coupons") == 2
    blob = json.dumps(data, ensure_ascii=False)
    assert "DEMOPULL" not in blob and "demo-claim" not in blob  # 券码、couponId 不往外给

    code, data = runner.json(*argv, mock=True)  # 同一份确认不能用两次
    assert code == 1 and "还没有给用户看过" in data["error"]["message"]
    assert runner.count("auto-bind-coupons") == 1

    code, data = runner.json("pull", mock=True)  # 券都领完了
    assert code == 0 and data["data"]["status"] == "up-to-date"
    code, out = runner.run("pull", mock=True)
    assert code == 0 and "Already up to date." in out
    assert runner.count("auto-bind-coupons") == 1 and _pending(runner) is None


def test_confirm_requires_a_matching_preview(runner: Runner) -> None:
    code, data = runner.json("pull", "--confirm", "abcdef0", mock=True)
    assert code == 1 and "还没有给用户看过" in data["error"]["message"]
    assert runner.calls == []  # 连可领券都不查
    code, data = runner.json("pull", mock=True)
    fp = data["data"]["confirm"]
    code, data = runner.json("pull", "--confirm", "0000000" if fp != "0000000" else "1111111", mock=True)
    assert code == 1 and "对不上" in data["error"]["message"]
    for bad in ("12", "xyz1234", "8262-81"):
        code, data = runner.json("pull", "--confirm", bad, mock=True)
        assert code == 1 and "至少 7 位" in data["error"]["message"], bad
    assert runner.count("available-coupons") == 1 and runner.count("auto-bind-coupons") == 0
    assert _pending(runner)["fingerprint"].startswith(fp)  # 输错指纹不作废预览
    code, data = runner.json("pull", "--confirm", fp.upper(), mock=True, now=FIXED_NOW + timedelta(minutes=10))  # 10 分钟整还有效
    assert code == 0 and data["data"]["status"] == "pulled"
    assert runner.count("auto-bind-coupons") == 1


def test_preview_expires_after_ten_minutes(runner: Runner) -> None:
    argv = _preview(runner)
    code, data = runner.json(*argv, mock=True, now=FIXED_NOW + timedelta(minutes=11))
    assert code == 1 and "确认已过期" in data["error"]["message"]
    assert _pending(runner) is None
    code, data = runner.json(*argv, mock=True)  # 过期的预览已经清掉，回到 10 分钟内也不能用
    assert code == 1 and "还没有给用户看过" in data["error"]["message"]
    assert runner.count("auto-bind-coupons") == 0 and runner.count("available-coupons") == 1


def test_list_changed_after_preview(runner: Runner) -> None:
    argv = _preview(runner)
    runner.hooks["available-coupons"] = _coupons(*COUPONS, "示例·第四张券")
    code, data = runner.json(*argv, mock=True)
    assert code == 1 and data["error"]["message"] == "可领券列表变了（预览时 3 张，现在 4 张），什么都没领"
    assert any("重新运行 git mcd pull 预览" in h for h in data["error"]["hints"])
    assert _pending(runner) is None and runner.count("auto-bind-coupons") == 0

    runner.hooks.clear()
    argv = _preview(runner)
    runner.hooks["available-coupons"] = _coupons(*COUPONS[:2], "示例·换了一张")  # 张数一样、券换了也不行
    code, data = runner.json(*argv, mock=True)
    assert code == 1 and "还是 3 张，但券不一样了" in data["error"]["message"]
    assert runner.count("auto-bind-coupons") == 0

    runner.hooks.clear()
    argv = _preview(runner)
    runner.hooks["available-coupons"] = _coupons()  # 预览之后券都没了（比如在 App 里领了）
    code, data = runner.json(*argv, mock=True)
    assert code == 0 and data["data"]["status"] == "up-to-date" and _pending(runner) is None
    assert runner.count("auto-bind-coupons") == 0


def test_nothing_to_pull(runner: Runner) -> None:
    runner.hooks["available-coupons"] = _coupons()
    code, data = runner.json("pull", mock=True)
    assert code == 0 and data["ok"] is True and data["data"]["status"] == "up-to-date"
    code, out = runner.run("pull", mock=True, interactive=True, answers=["y"])
    assert code == 0 and out.strip() == "Already up to date.（没有可领的券）"
    assert runner.count("auto-bind-coupons") == 0 and _pending(runner) is None


def test_available_coupons_failure_claims_nothing(runner: Runner) -> None:
    argv = _preview(runner)

    def down(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        raise McdError("网络错误：timed out", transport=True)

    runner.hooks["available-coupons"] = down
    code, data = runner.json(*argv, mock=True)
    assert code == 1 and data["error"]["message"] == "查可领券失败：网络错误：timed out" and data["error"]["hints"] == ["什么都没领"]
    assert runner.count("auto-bind-coupons") == 0
    runner.hooks.clear()
    code, data = runner.json(*argv, mock=True)  # 什么都没发生，预览还在
    assert code == 0 and data["data"]["status"] == "pulled"


def test_force_and_dry_run_confirm_are_refused(runner: Runner) -> None:
    code, out = runner.run("pull", "--force", mock=True)
    assert code == 128 and "fatal: --force 不支持：券是领的，不是抢的" in out
    code, data = runner.json("pull", "-f", mock=True)
    assert code == 128 and data["error"]["type"] == "fatal"
    code, out = runner.run("pull", "--dry-run", "--confirm", "abcdef0", mock=True)
    assert code == 1 and "不能一起用" in out
    assert runner.calls == []


def test_help(runner: Runner, capsys: pytest.CaptureFixture) -> None:
    code, _ = runner.run("pull", "-h")
    text = capsys.readouterr().out
    assert code == 0 and "--confirm" in text and "只看有哪些券可领，不领" in text
    assert "--force" not in text  # 隐藏参数，只用来回一句拒绝


# ---------------------------------------------------------------- 领券结果


@pytest.mark.parametrize(
    "outcome, exit_code",
    [
        pytest.param(McdError("网络错误：timed out", transport=True), 1, id="timeout"),
        pytest.param(_truncated("auto-bind-coupons"), 1, id="truncated-response"),
        pytest.param(http.client.IncompleteRead(b'{"success": true', 300), 1, id="raw-incomplete-read"),
        pytest.param(McdError("服务端返回 HTTP 302", http_status=302), 1, id="redirect-302"),
        pytest.param(McdError("服务端返回 HTTP 502", http_status=502), 1, id="http-502"),
        pytest.param(_jsonrpc_error("auto-bind-coupons", -32603, "Internal error"), 1, id="jsonrpc-internal-error"),
        pytest.param(_jsonrpc_error("auto-bind-coupons", -32000, "Server error"), 1, id="jsonrpc-server-error"),
        pytest.param(_envelope("领好了"), 1, id="data-is-a-string"),
        pytest.param(mcp_result({"success": True, "code": 200, "message": "请求成功"}), 1, id="no-data"),
        pytest.param(mcp_result(text="502 Bad Gateway", structured=False), 1, id="unparseable"),
        pytest.param(KeyboardInterrupt(), 130, id="ctrl-c"),
    ],
)
def test_unknown_result_says_check_first(runner: Runner, outcome: Any, exit_code: int) -> None:
    """请求已经发出去、结果却说不清：不重试、不说「什么都没领」，提示先 status / fetch 核对，也不留下能再确认一次的预览。"""
    argv = _preview(runner)

    def hook(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        assert allow_write  # 只有确认闸放行的这一次
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):  # 经真实的 McdMcpClient 发出去
            return outcome(args, allow_write)
        return outcome

    runner.hooks["auto-bind-coupons"] = hook
    code, data = runner.json(*argv, mock=True)
    assert code == exit_code and data["ok"] is False and data["error"]["exitCode"] == exit_code
    assert data["error"]["message"].startswith("pull 结果未知")
    hints = " ".join(data["error"]["hints"])
    assert "git mcd status" in hints and "git mcd fetch" in hints and "不要直接重试" in hints
    assert "什么都没领" not in json.dumps(data, ensure_ascii=False)
    assert runner.count("auto-bind-coupons") == 1
    code, data = runner.json(*argv, mock=True)  # 预览已消费，不能原样再领一次
    assert code == 1 and "还没有给用户看过" in data["error"]["message"]
    assert runner.count("auto-bind-coupons") == 1


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(_jsonrpc_error("auto-bind-coupons", -32700, "Parse error"), id="jsonrpc-parse-error"),
        pytest.param(_jsonrpc_error("auto-bind-coupons", -32600, "Invalid Request"), id="jsonrpc-invalid-request"),
        pytest.param(_jsonrpc_error("auto-bind-coupons", -32601, "Method not found"), id="jsonrpc-method-not-found"),
        pytest.param(_jsonrpc_error("auto-bind-coupons", -32602, "Invalid params"), id="jsonrpc-invalid-params"),
        pytest.param(mcp_result({"success": False, "code": 600099, "message": "活动太火爆了，稍后再试", "traceId": "f" * 32}), id="business-error"),
        pytest.param(McdError("Token 无效或已过期", http_status=401), id="http-401"),
    ],
)
def test_explicit_rejection_says_nothing_claimed(runner: Runner, outcome: Any) -> None:
    """服务端明确拒绝（JSON-RPC 没受理这次请求、业务错误码、HTTP 4xx）：才能说「什么都没领」。"""
    argv = _preview(runner)

    def hook(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome(args, allow_write) if callable(outcome) else outcome

    runner.hooks["auto-bind-coupons"] = hook
    code, data = runner.json(*argv, mock=True)
    assert code == 1 and data["ok"] is False and data["error"]["message"].startswith("pull 失败：")
    assert data["error"]["hints"] == REJECTED_HINTS
    assert runner.count("auto-bind-coupons") == 1
    if isinstance(outcome, dict):
        assert data["error"]["remote"][0].startswith("活动太火爆了，稍后再试（code 600099")


def test_partial_and_total_failure(runner: Runner) -> None:
    def bound(successes: List[str], failures: List[str], **counts: Any) -> Any:
        payload = dict(
            counts,
            successCoupons=[{"couponId": f"cid-{i}", "couponName": n, "couponCode": f"SECRET-{i}", "couponImage": "https://example.com/c.png"} for i, n in enumerate(successes)],
            failedCoupons=[{"couponId": f"cid-f{i}", "couponName": n, "reason": "已达领取上限"} for i, n in enumerate(failures)],
        )
        return lambda args, allow_write: _envelope(payload)

    runner.hooks["auto-bind-coupons"] = bound(COUPONS[:2], COUPONS[2:], successCount=2, failedCount=1, totalCount=3)
    code, out = runner.run(*_preview(runner), mock=True)
    assert code == 0, out
    assert " * [new coupon]      示例·新品尝鲜券" in out and "-> coupons/新品尝鲜券" in out
    assert " ! [rejected]        示例·小食立减券" in out and "-> coupons/小食立减券（已达领取上限）" in out
    assert "Fast-forward" in out and "领到 2 张，失败 1 张" in out
    code, data = runner.json(*_preview(runner), mock=True)
    assert code == 0 and data["data"]["status"] == "pulled"
    assert (data["data"]["successCount"], data["data"]["failedCount"], data["data"]["totalCount"]) == (2, 1, 3)
    assert data["data"]["failed"] == [{"name": "示例·小食立减券", "short": "小食立减券", "reason": "已达领取上限"}]
    assert not any(secret in json.dumps(data, ensure_ascii=False) for secret in ("cid-", "SECRET-", "c.png"))

    runner.hooks["auto-bind-coupons"] = bound([], [], successCount=3)  # 只给了计数
    code, out = runner.run(*_preview(runner), mock=True)
    assert code == 0 and " * [new coupon]      （接口没返回券名）× 3" in out and "领到 3 张，失败 0 张" in out

    runner.hooks["auto-bind-coupons"] = bound([], COUPONS, successCount=0, failedCount=3)  # 一张都没领到
    code, data = runner.json(*_preview(runner), mock=True)
    assert code == 1 and data["ok"] is False and data["error"]["message"] == "pull 失败：3 张券一张都没领到"
    assert data["error"]["remote"] == [f"{n}：已达领取上限" for n in COUPONS]
    assert data["error"]["detail"]["code"] == "nothing_claimed" and data["error"]["detail"]["failedCount"] == 3
    code, out = runner.run(*_preview(runner), mock=True)
    assert code == 1 and "remote: 示例·新品尝鲜券：已达领取上限" in out and "error: pull 失败：3 张券一张都没领到" in out

    runner.hooks["auto-bind-coupons"] = bound([], [], successCount=0, failedCount=0, totalCount=0)  # 服务端说这次没发券
    code, data = runner.json(*_preview(runner), mock=True)
    assert code == 0 and data["data"]["status"] == "up-to-date" and "可能刚刚已经领过" in data["data"]["note"]
    assert runner.count("auto-bind-coupons") == 6


# ---------------------------------------------------------------- 和 push、demo 的配合


def test_pull_and_push_previews_do_not_clobber_each_other(runner: Runner) -> None:
    """push 的预览和 pull 的预览分开存：先预览下单、去领券、再回来确认下单，都不会互相作废。"""
    short = _setup(runner)
    code, data = runner.json(*PUSH, mock=True)
    assert data["data"]["status"] == "confirmation_required"
    argv = _preview(runner)
    code, data = runner.json(*PUSH, "--confirm", short, mock=True)
    assert code == 0 and data["data"]["status"] == "pushed"
    code, data = runner.json(*argv, mock=True)
    assert code == 0 and data["data"]["status"] == "pulled"
    assert runner.count("create-order") == 1 and runner.count("auto-bind-coupons") == 1


def test_demo_pull_then_fetch_and_init(runner: Runner) -> None:
    code, out = runner.run("--demo", "fetch")
    assert code == 0 and "(git mcd pull 领取：会先确认，一次领全部)" in out
    code, out = runner.run("--demo", "pull", interactive=True, answers=["y"])
    assert code == 0 and "即将 pull：一次领取全部 3 张可领券（demo）" in out and "领到 3 张，失败 0 张" in out
    code, data = runner.json("--demo", "fetch")
    assert code == 0 and data["data"]["claimable"] == []
    code, out = runner.run("--demo", "pull")
    assert code == 0 and "Already up to date." in out
    assert runner.run("--demo", "init", "--pick", "1")[0] == 0  # 从头再来：演示账户的券恢复
    code, data = runner.json("--demo", "fetch")
    assert len(data["data"]["claimable"]) == 3
    code, data = runner.json("--demo", "pull")
    assert data["data"]["next"] == f"git mcd --demo pull --confirm {data['data']['confirm']} --json"
