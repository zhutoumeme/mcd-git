"""push / revert 的确认闸：走 demo=False 的真实代码路径，MCP 换成 mock（RecordingClient），绝不联网。"""
from __future__ import annotations

import http.client
import json
import shlex
from datetime import datetime
from typing import Any, Dict

import pytest

from mcd_git import gate
from mcd_git.api import McdApi
from mcd_git.commands.common import scope_gap
from mcd_git.errors import GitMcdError, McdError
from mcd_git.mcp_client import McdMcpClient
from mcd_git.state import CN_TZ, DEMO_NOW, now

from .conftest import Runner
from .test_client import FakeResponse, FakeServer


def _setup(runner: Runner, *, pick: str = "1") -> str:
    """选门店 → 加购 → rebase → commit，返回 commit 短哈希。全程不碰写操作。"""
    assert runner.run("init", mock=True)[0] == 0  # 列收藏门店
    assert runner.run("init", "--pick", pick, mock=True)[0] == 0
    assert runner.run("add", "巨无霸", "中薯条", "中可乐", "--no-pickle", mock=True)[0] == 0
    assert runner.run("rebase", mock=True)[0] == 0
    code, data = runner.json("commit", "-m", "发版前的午饭", mock=True)
    assert code == 0
    return data["data"]["short"]


PUSH = ("push", "--at", "12:20", "--locker", "--no-tableware")


def test_preview_never_writes(runner: Runner) -> None:
    short = _setup(runner)
    code, data = runner.json(*PUSH, mock=True)
    assert code == 0
    assert data["data"]["status"] == "confirmation_required"
    assert data["data"]["confirm"] == short
    assert data["data"]["summary"]["payable"] == 3350
    assert data["data"]["summary"]["takeWayCode"] == "locker-out"
    assert "--confirm " + short in data["data"]["next"]
    code, data = runner.json(*PUSH, "--dry-run", mock=True)
    assert code == 0 and data["data"]["status"] == "confirmation_required"
    assert runner.count("create-order") == 0


def test_confirm_requires_a_preview_first(runner: Runner) -> None:
    short = _setup(runner)
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)
    assert code == 1 and "确认摘要" in out
    assert runner.count("create-order") == 0


def test_confirm_hash_must_match_head(runner: Runner) -> None:
    _setup(runner)
    runner.run(*PUSH, mock=True)
    before = runner.count("calculate-price")
    code, out = runner.run(*PUSH, "--confirm", "deadbee", mock=True)
    assert code == 1 and "对不上" in out
    code, out = runner.run(*PUSH, "--confirm", "abc", mock=True)
    assert code == 1
    assert runner.count("calculate-price") == before  # 哈希不对，连算价都不发
    assert runner.count("create-order") == 0


def test_confirm_options_must_match_preview(runner: Runner) -> None:
    short = _setup(runner)
    runner.run(*PUSH, mock=True)
    code, data = runner.json("push", "--at", "12:20", "--eat-in", "--no-tableware", "--confirm", short, mock=True)
    assert code == 1 and "不一样" in data["error"]["message"]
    assert data["error"]["detail"]["previewed"]["takeWay"] == "locker"
    code, _ = runner.run("push", "--at", "12:30", "--locker", "--no-tableware", "--confirm", short, mock=True)
    assert code == 1
    assert runner.count("create-order") == 0


def test_confirm_refuses_when_price_changed(runner: Runner) -> None:
    short = _setup(runner)
    runner.run(*PUSH, mock=True)

    def bumped(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        result = runner.clients[-1]._t_calculate_price(args)
        result["price"] += 100  # 预览之后门店调价了
        env = {"success": True, "code": 200, "message": "请求成功", "data": result}
        return {"content": [{"type": "text", "text": ""}], "isError": False, "structuredContent": env}

    runner.hooks["calculate-price"] = bumped
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)
    assert code == 1 and "价格变了" in out and "¥33.50 → ¥34.50" in out
    assert runner.count("create-order") == 0
    # 预览已作废，必须重新预览
    runner.hooks.clear()
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)
    assert code == 1 and "确认摘要" in out


def test_confirmed_push_places_exactly_one_order(runner: Runner) -> None:
    short = _setup(runner)
    runner.run(*PUSH, mock=True)
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)
    assert code == 0, out
    assert "[new order]" in out and "lunch -> 巨无霸套餐" in out
    assert "https://example.com/mcd-git-demo/pay?orderId=" in out
    assert runner.count("create-order") == 1
    args = runner.clients[-1].args_of("create-order")[0]
    assert args["takeWayCode"] == "locker-out"
    assert args["reservationDate"] == "2026-10-24 12:20"
    assert args["needTableware"] is False
    assert args["storeCode"] == "1000001" and "beCode" not in args
    assert args["items"][0]["couponId"] == "c0ffee00000000000000000000000001"
    assert args["items"][0]["roundList"][0]["comboItemList"][0]["modification"]["values"][0]["key"] == "0-0"
    # 同一份确认不能用两次；HEAD 已 push
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)
    assert code == 0 and "Everything up-to-date" in out
    assert runner.count("create-order") == 1
    # status 提示待支付和截止时间（本地记录，不发请求）
    code, data = runner.json("status", mock=True)
    assert data["data"]["pendingPayment"]["expirePayTime"] == "2026-10-24 12:05:00"


def test_preview_expires_after_ten_minutes(runner: Runner) -> None:
    short = _setup(runner)
    runner.run(*PUSH, mock=True)
    code, out = runner.run(*PUSH, "--confirm", short, mock=True, now=datetime(2026, 10, 24, 12, 1, tzinfo=CN_TZ))
    assert code == 1 and "过期" in out
    assert runner.count("create-order") == 0


@pytest.mark.parametrize("resolve", ["amend", "discard"])
def test_cart_changes_after_preview_block_push(runner: Runner, resolve: str) -> None:
    """预览之后又往购物车里加了东西：push 只会按 HEAD 下单，所以预览和 --confirm 都直接报错，一单都不下。
    按提示二选一（commit --amend 并进这一单，或 reset 放弃改动），重新预览之后才能下单。"""
    short = _setup(runner)
    code, data = runner.json(*PUSH, mock=True)
    assert data["data"]["status"] == "confirmation_required"
    assert runner.run("add", "热拿铁", mock=True)[0] == 0
    for extra in ((), ("--confirm", short)):
        code, data = runner.json(*PUSH, *extra, mock=True)
        assert code == 1 and data["ok"] is False and data["error"]["type"] == "error"
        assert data["error"]["message"] == f"购物车里有还没 commit 的改动，这一单（{short}）不包含它们"
        assert data["error"]["hints"] == ["想并进这一单：git mcd commit --amend", "不要这些改动：git mcd reset"]
        detail = data["error"]["detail"]
        assert detail["code"] == "uncommitted_changes" and detail["commit"] == short
        assert [x["name"] for x in detail["uncommitted"]] == ["热拿铁"]
        assert (detail["amend"], detail["discard"]) == ("git mcd commit --amend --json", "git mcd reset --json")
        assert detail["withdraw"] == "git mcd reset --soft 'HEAD~1' --json"
    assert runner.count("create-order") == 0

    # 照抄 JSON 里给的命令（去掉开头的 git mcd，--json 由 runner.json 自己加）
    code, _ = runner.json(*shlex.split(detail[resolve])[2:-1], mock=True)
    assert code == 0
    code, data = runner.json(*PUSH, mock=True)
    assert code == 0 and data["data"]["status"] == "confirmation_required"
    latte = any("热拿铁" in item["name"] for item in data["data"]["summary"]["items"])
    assert latte is (resolve == "amend")
    assert (data["data"]["confirm"] == short) is (resolve == "discard")  # amend 换了新哈希，旧预览作废
    code, data = runner.json(*PUSH, "--confirm", data["data"]["confirm"], mock=True)
    assert code == 0 and data["data"]["status"] == "pushed"
    assert runner.count("create-order") == 1
    codes = [item["productCode"] for item in runner.clients[-1].args_of("create-order")[0]["items"]]
    assert ("4001" in codes) is (resolve == "amend")  # 4001 是 demo 菜单里的热拿铁


def test_cart_from_another_store_is_not_offered_amend(runner: Runner) -> None:
    """真实模式下 commit 之后 init 换店：只清购物车、不动 HEAD。在新店 add 之后 commit --amend 会拒绝（HEAD 是另一家店的单），
    所以 push 不给 amend（detail.amend 是 null），只给 reset 放弃购物车和 reset --soft HEAD~1 撤回 HEAD；
    换店前已经预览过，预览和 --confirm 也都一单不下。commit / status / show 的提示同样不提 amend。"""
    short = _setup(runner)  # 演示·代码港店
    code, data = runner.json(*PUSH, mock=True)
    assert data["data"]["status"] == "confirmation_required"
    assert runner.run("init", "--pick", "2", mock=True)[0] == 0  # 换到 演示·提交大道店
    assert runner.run("add", "热拿铁", mock=True)[0] == 0
    note = "HEAD 是另一家店的单（演示·代码港店 · 到店自取），购物车没法并进去"
    for extra in ((), ("--confirm", short)):
        code, data = runner.json(*PUSH, *extra, mock=True)
        assert code == 1 and data["ok"] is False and data["error"]["type"] == "error"
        assert data["error"]["hints"] == [note, "放弃购物车：git mcd reset", "撤回 HEAD（餐品放回购物车）：git mcd reset --soft HEAD~1"]
        detail = data["error"]["detail"]
        assert detail["code"] == "uncommitted_changes" and detail["commit"] == short
        assert [x["name"] for x in detail["uncommitted"]] == ["热拿铁"]
        assert detail["amend"] is None
        assert detail["discard"] == "git mcd reset --json"
        assert detail["withdraw"] == "git mcd reset --soft 'HEAD~1' --json"
    assert runner.count("create-order") == 0

    code, out = runner.run("commit", "--amend", mock=True)  # 这就是不给 amend 的原因
    assert code == 1 and "不能 amend" in out
    code, data = runner.json("commit", "-m", "换了店", mock=True)
    assert code == 1 and data["error"]["hints"] == [note, "不要购物车里的：git mcd reset，再 git mcd push 下单", "git mcd reset --soft HEAD~1 撤回重来"]
    code, out = runner.run("status", mock=True)
    assert f"  ({note})" in out and '(use "git mcd reset --soft HEAD~1" to withdraw HEAD)' in out and "--amend" not in out
    code, out = runner.run("show", mock=True)
    assert "hint: 还没 push；它是另一家店的单（演示·代码港店 · 到店自取），购物车没法并进去" in out and "--amend" not in out

    # 照抄 withdraw：HEAD 撤回，那一单的餐品放回购物车，可以按现在这家店重新 commit
    code, _ = runner.json(*shlex.split(detail["withdraw"])[2:-1], mock=True)
    assert code == 0
    code, data = runner.json("status", mock=True)
    assert data["data"]["head"] is None and [x["name"] for x in data["data"]["index"]] == ["巨无霸套餐", "热拿铁"]
    assert runner.count("create-order") == 0

    # 同一家店换了取餐方式也一样，提示里说的是「另一种取餐方式」
    pickup = {"storeCode": "1000001", "storeName": "演示·代码港店", "modeLabel": "到店自取", "beType": 1, "orderType": 1, "beCode": None}
    assert scope_gap(pickup, dict(pickup)) is None
    assert scope_gap(pickup, dict(pickup, modeLabel="麦乐送", beType=2, orderType=2, beCode="100000102")) == "另一种取餐方式的单（演示·代码港店 · 到店自取）"


def test_fake_clock_env_only_works_in_demo() -> None:
    env = {"MCD_GIT_NOW": "2026-10-24 11:51:00"}
    assert now(True, env) == datetime(2026, 10, 24, 11, 51, tzinfo=CN_TZ)
    assert now(True, {}) == DEMO_NOW
    # 真实模式无视它：不能借此把过期的预览「拉回」10 分钟内，也不能往订单记录里写假时间
    assert abs((now(False, env) - datetime.now(CN_TZ)).total_seconds()) < 60


def test_force_and_dry_run_confirm_are_refused(runner: Runner) -> None:
    short = _setup(runner)
    code, out = runner.run("push", "--force", mock=True)
    assert code == 128 and "强推" in out
    code, out = runner.run("push", "--dry-run", "--confirm", short, mock=True)
    assert code == 1
    assert runner.count("create-order") == 0


def test_interactive_prompt(runner: Runner) -> None:
    _setup(runner)
    code, out = runner.run(*PUSH, mock=True, interactive=True, answers=["n"])
    assert code == 0 and "没有下单" in out and "即将 push" in out
    code, out = runner.run(*PUSH, "--dry-run", mock=True, interactive=True, answers=["y"])
    assert runner.count("create-order") == 0  # --dry-run 连问都不问
    code, out = runner.run(*PUSH, mock=True, interactive=True, answers=["y"])
    assert code == 0 and "[new order]" in out
    assert runner.count("create-order") == 1


def test_transport_error_is_not_retried(runner: Runner) -> None:
    short = _setup(runner)
    runner.run(*PUSH, mock=True)

    def boom(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        raise McdError("网络错误：timed out", transport=True)

    runner.hooks["create-order"] = boom
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)
    assert code == 1 and "git mcd log" in out and "不要直接重试" in out
    assert runner.count("create-order") == 1
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)  # 预览已消费
    assert code == 1 and runner.count("create-order") == 1


def _mcp(envelope: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": ""}], "isError": False, "structuredContent": envelope}


def _jsonrpc_error(tool: str, code: int, message: str) -> Any:
    """tool 的 JSON-RPC 响应是 {"error": {"code": …, "message": …}}：经真实的 McdMcpClient 解析（FakeServer 回放，不联网）。"""

    def reply(payload: Dict[str, Any]) -> FakeResponse:
        body = {"jsonrpc": "2.0", "id": payload["id"], "error": {"code": code, "message": message}}
        return FakeResponse(json.dumps(body).encode("utf-8"))

    def call(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        client = McdMcpClient("unit-test-token", opener=FakeServer([reply]), min_interval=0)
        return client.call_tool(tool, args, allow_write=allow_write)

    return call


@pytest.mark.parametrize(
    "outcome, exit_code",
    [
        pytest.param(http.client.IncompleteRead(b'{"success": true', 300), 1, id="truncated-response"),
        pytest.param(_mcp({"success": True, "code": 200, "data": ["not", "a", "dict"]}), 1, id="data-not-a-dict"),
        pytest.param(_mcp({"success": True, "code": 200, "data": {"payH5Url": "https://example.com/x"}}), 1, id="no-order-id"),
        pytest.param({"content": [{"type": "text", "text": "502 Bad Gateway"}], "isError": False}, 1, id="unparseable"),
        # 默认 opener 不跟随重定向，30x 经 _http_error 变成这个（见 test_client 的重定向用例）
        pytest.param(McdError("服务端返回 HTTP 302", http_status=302), 1, id="redirect-302"),
        # JSON-RPC 层的服务端错误：处理时出了错，说明不了订单没建成
        pytest.param(_jsonrpc_error("create-order", -32603, "Internal error"), 1, id="jsonrpc-internal-error"),
        pytest.param(_jsonrpc_error("create-order", -32000, "Server error"), 1, id="jsonrpc-server-error"),
        pytest.param(KeyboardInterrupt(), 130, id="ctrl-c"),
    ],
)
def test_unknown_create_order_result_says_check_log_first(runner: Runner, outcome: Any, exit_code: int) -> None:
    """请求已经发出去、结果却说不清：不出 traceback，提示先 git mcd log 核对，也不留下能再确认一次的预览。"""
    short = _setup(runner)
    runner.run(*PUSH, mock=True)

    def hook(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):  # 经真实的 McdMcpClient 发出去
            return outcome(args, allow_write)
        return outcome

    runner.hooks["create-order"] = hook
    code, data = runner.json(*PUSH, "--confirm", short, mock=True)
    assert code == exit_code and data["ok"] is False
    assert data["error"]["message"].startswith("failed to push some refs")
    assert any("git mcd log" in h and "不要直接重试" in h for h in data["error"]["hints"])
    assert runner.count("create-order") == 1
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)  # 预览已消费，不能原样再下一次
    assert code == 1 and "确认摘要" in out
    assert runner.count("create-order") == 1
    code, data = runner.json("status", mock=True)
    assert data["data"]["ahead"] == 1 and data["data"]["pendingPayment"] is None


def test_business_error_message_like_git(runner: Runner) -> None:
    short = _setup(runner)
    runner.run(*PUSH, mock=True)

    def closed(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        env = {"success": False, "code": 600031, "message": "门店已打烊", "traceId": "f" * 32}
        return {"content": [{"type": "text", "text": ""}], "isError": False, "structuredContent": env}

    runner.hooks["create-order"] = closed
    code, out = runner.run(*PUSH, "--confirm", short, mock=True)
    assert code == 1
    assert "error: failed to push some refs: 门店已打烊" in out
    assert "remote: 门店已打烊（code 600031" in out
    assert "什么都没下单" in out  # 服务端明确拒绝，才能这么说


@pytest.mark.parametrize("rpc_code", [-32700, -32600, -32601, -32602])
def test_jsonrpc_request_not_accepted_counts_as_rejected(runner: Runner, rpc_code: int) -> None:
    """JSON-RPC 层没受理这次请求（解析失败、请求无效、方法不存在、参数无效）：可以断定什么都没下单。"""
    short = _setup(runner)
    runner.run(*PUSH, mock=True)
    runner.hooks["create-order"] = _jsonrpc_error("create-order", rpc_code, "Invalid params")
    code, data = runner.json(*PUSH, "--confirm", short, mock=True)
    assert code == 1 and data["ok"] is False
    assert data["error"]["hints"] == ["什么都没下单；处理完上面的问题后重新 git mcd push 预览"]
    assert data["error"]["remote"] == [f"tools/call：Invalid params（code {rpc_code}）"]
    assert runner.count("create-order") == 1


def test_take_way_and_remark_validation(runner: Runner) -> None:
    _setup(runner, pick="2")  # 演示·提交大道店没有取餐柜
    code, out = runner.run("push", "--locker", mock=True)
    assert code == 1 and "没有取餐柜" in out
    code, out = runner.run("push", "--remark", "无接触", mock=True)
    assert code == 1 and "只支持麦乐送" in out
    code, out = runner.run("push", "--at", "09:00", mock=True)
    assert code == 1 and "已经过去了" in out
    assert runner.count("create-order") == 0


def test_approval_cannot_be_forged_or_reused(tmp_path: Any) -> None:
    with pytest.raises(RuntimeError):
        gate.Approval(object(), "create-order", {})
    approval = gate.issue("create-order", {"a": 1})

    class Client:
        def call_tool(self, *a: Any, **kw: Any) -> Dict[str, Any]:
            return {"structuredContent": {"success": True, "code": 200, "data": {"orderId": "1"}}}

    api = McdApi(Client())
    with pytest.raises(GitMcdError):
        api.create_order(approval, {"a": 2})  # 参数和确认时不一致
    api.create_order(approval, {"a": 1})
    with pytest.raises(GitMcdError):
        api.create_order(approval, {"a": 1})  # 用过就作废


def test_log_falls_back_to_local_commits(runner: Runner) -> None:
    short = _setup(runner)

    def down(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        raise McdError("网络错误：timed out", transport=True)

    runner.hooks["order-list"] = down
    code, out = runner.run("log", "--oneline", mock=True)
    assert code == 0
    assert "warning: order-list 查询失败" in out and short in out


# ---------------------------------------------------------------- revert


def _pushed(runner: Runner) -> str:
    short = _setup(runner)
    runner.run(*PUSH, mock=True)
    code, data = runner.json(*PUSH, "--confirm", short, mock=True)
    assert code == 0
    return data["data"]["orderId"]


def test_revert_needs_reason_preview_and_confirm(runner: Runner) -> None:
    order_id = _pushed(runner)
    code, data = runner.json("revert", mock=True)
    assert code == 1 and "取消原因" in data["error"]["message"]
    code, data = runner.json("revert", "--reason", "3", mock=True)
    assert code == 0 and data["data"]["status"] == "confirmation_required"
    assert data["data"]["summary"]["reason"] == {"code": "3", "label": "点错了/点多了/点少了"}
    assert data["data"]["next"] == f"git mcd revert {order_id} --reason 3 --confirm {order_id} --json"
    assert runner.count("cancel-order") == 0
    code, _ = runner.run("revert", "--reason", "3", "--confirm", "999999", mock=True)
    assert code == 1
    code, out = runner.run("revert", "--reason", "1", "--confirm", order_id, mock=True)
    assert code == 1 and "不一样" in out
    assert runner.count("cancel-order") == 0
    code, out = runner.run("revert", "--reason", "3", "--confirm", order_id, mock=True)
    assert code == 0 and "已取消" in out and 'Revert "发版前的午饭"' in out
    assert runner.count("cancel-order") == 1
    assert runner.clients[-1].args_of("cancel-order")[0] == {"orderId": order_id, "cancelReasonCode": "3"}


def test_revert_confirm_without_preview_and_completed_orders(runner: Runner) -> None:
    order_id = _pushed(runner)
    code, out = runner.run("revert", "--reason", "2", "--confirm", order_id, mock=True)
    assert code == 1 and "确认摘要" in out
    code, out = runner.run("revert", "1024202610231231000000000004", "--reason", "1", mock=True)
    assert code == 1 and "不能 revert" in out
    assert runner.count("cancel-order") == 0


@pytest.mark.parametrize("rpc_code, message", [(-32603, "Internal error"), (-32602, "Invalid params")])
def test_revert_jsonrpc_error_never_says_not_cancelled(runner: Runner, rpc_code: int, message: str) -> None:
    """cancel-order 收到 JSON-RPC 错误：不管是 -32603（处理时出错）还是 -32602（参数无效），revert 都只让用户先 git mcd show
    看订单现在的状态，从不断言「没取消」；cancel-order 只调 1 次，预览已消费，要再取消得重新预览。"""
    order_id = _pushed(runner)
    code, data = runner.json("revert", "--reason", "3", mock=True)
    assert code == 0 and data["data"]["status"] == "confirmation_required"
    runner.hooks["cancel-order"] = _jsonrpc_error("cancel-order", rpc_code, message)
    code, data = runner.json("revert", "--reason", "3", "--confirm", order_id, mock=True)
    assert code == 1 and data["ok"] is False and data["error"]["exitCode"] == 1
    assert data["error"]["message"] == f"revert 失败：tools/call：{message}"
    assert data["error"]["remote"] == [f"tools/call：{message}（code {rpc_code}）"]
    assert data["error"]["hints"] == ["git mcd show 看看订单现在的状态"]
    output = json.dumps(data, ensure_ascii=False)
    assert not any(words in output for words in ("什么都没取消", "没有取消", "没取消", "未取消"))
    assert runner.count("cancel-order") == 1
    code, out = runner.run("revert", "--reason", "3", "--confirm", order_id, mock=True)  # 预览已消费，不能原样再取消一次
    assert code == 1 and "确认摘要" in out
    assert runner.count("cancel-order") == 1
    code, data = runner.json("status", mock=True)
    assert data["data"]["pendingPayment"]["orderId"] == order_id  # 本地记录也没被当成已取消
