---
name: mcd-git
description: 用本地命令行 mcd-git（git mcd --json）帮用户点麦当劳：选门店、看菜单、加购物车（去酸黄瓜、少冰、套餐换搭）、自动凑套餐挑最省的券、核价、下单拿支付链接、查取餐码、再来一单、取消订单、领券（pull，先确认），以及生成 1024 吃麦贡献图（card）。用户提到点麦当劳、麦当劳到店 / 得来速 / 麦乐送、取餐码、再来一单、取消麦当劳订单、领麦当劳的券、吃麦贡献图时使用。下单、取消和领券都必须先把确认摘要给用户看，用户明确同意后才能带 --confirm 执行。
---

# mcd-git：用 git 命令点麦当劳

mcd-git 把麦当劳中国 MCP Server 包装成 git 风格的命令：`add` 放进购物车，`rebase` 凑最省，`commit` 锁单，`push` 才真正下单。你通过 `git mcd … --json` 操作它，读 JSON 结果，再用自然语言告诉用户。

## 前提

- **需要能在用户本机执行命令。** 只能对话、不能跑命令的环境用不了这个 Skill。
- **安装**（没装时）：
  ```bash
  uv tool install git+https://github.com/zhutoumeme/mcd-git
  # 没有 uv：
  pip install "git+https://github.com/zhutoumeme/mcd-git"
  ```
  装好后运行 `git mcd help` 确认（也可以用等价的 `mcd-git help`）。看命令对照表一律用 `git mcd help`；不要在 `mcd` 后面直接跟 `--help`，那样会被 git 拦截去找 man 手册。单个子命令的帮助用 `git mcd <命令> -h`。真人用户需要 TAB 补全时，按 README 的平台说明运行 `mcd-git completion powershell` / `bash` / `zsh` / `fish`，再重载对应 shell；Agent 不需要安装或调用补全脚本。
- **跨平台补全边界**：本 Skill 的 Agent 不按 TAB、不解析 shell 补全候选，也不把 `completion` 当作业务流程；始终只执行 `git mcd --json`，再根据 JSON 结果继续。`mcd-git` / `git-mcd` 是保证的补全入口；`git mcd` 依赖 Git 自带的 bash/zsh/fish 补全转发，Windows PowerShell 不保证 Git 转发。菜单补全只读最近的本机缓存，缓存缺失或过期时不联网；需要最新菜单时才按业务流程调用 `git mcd ls-remote --json`。
- **Token 由用户自己配置**：环境变量 `MCD_MCP_TOKEN`（所有平台首选），或配置文件（macOS / Linux 用 `~/.config/mcd-mcp/token`，Windows 用 `%APPDATA%\mcd-mcp\token`，也支持 `XDG_CONFIG_HOME`）。不要让用户把 Token 发进对话，不要读取、打印或改写 Token 文件。命令报「没有找到麦当劳 MCP Token」时，告诉用户去 open.mcd.cn 控制台申请并自己写进上面的位置（命令的报错 hint 会按平台给出具体命令和路径）。
- **没有 Token 时**可以加 `--demo` 演示全流程（合成数据，不会真的下单），并明确告诉用户这是演示。

## 必须遵守的规则

1. **只通过 `git mcd … --json` 操作。** 一律带 `--json`：输出稳定好解析，而且不会弹出 y/N 交互提示（交互提示是留给真人的，不要替用户回答）。不要直接调用 MCP 的写操作 tool（`create-order`、`cancel-order`、`auto-bind-coupons`、`mall-create-order`、`draw-lottery`、`party-order-create`、`delivery-create-address`），即使你的环境里配了麦当劳 MCP 连接器。
2. **下单前必须确认。** 先不带 `--confirm` 运行 `git mcd push … --json`，把返回的确认摘要（门店、取餐方式、时间、餐具、每样商品、券、应付金额）如实告诉用户，并说明「会生成一笔待支付订单，付款要你自己完成」。只有用户明确说「确认 / 下单 / 可以」之后，才运行 JSON 里 `data.next` 给出的那条命令（带 `--confirm <commit 哈希>`）。用户没明确同意、或改了任何条件（时间、取餐方式、餐品），都要重新预览、重新确认；改餐品要先并进这一单，见常用流程「下单前改餐品」。`push` 返回 `error.detail.code == "uncommitted_changes"`，说明购物车里还有没并进这一单的餐品（列在 `error.detail.uncommitted`），这次什么都没下单：**先问用户**这些要不要加进这一单，要就运行 `error.detail.amend`（`commit --amend`），不要就运行 `error.detail.discard`（`reset` 清空购物车）。`error.detail.amend` 是 null 时，HEAD 是另一家店（或另一种取餐方式）的单，购物车没法并进去：**先问用户**是放弃购物车（`error.detail.discard`），还是撤回那一单（`error.detail.withdraw`，即 `reset --soft HEAD~1`：那一单的餐品放回购物车，再按现在的门店重新 commit）。两种情况都不要替用户选，处理完都要重新预览、重新确认。
3. **取消订单同理。** `git mcd revert --reason <编号> --json` 先预览，用户同意后运行 `data.next`（带 `--confirm <订单号>`）。取消原因要让用户选：1 改主意了、2 重复下单、3 点错了/点多了/点少了、4 地址/电话填错了、5 送达时间选错了、-1 其它原因。
4. **领券也一样先确认。** 只用 `git mcd pull`，不要直接调 `auto-bind-coupons`。先运行 `git mcd pull --json` 预览，把 `data.summary` 如实告诉用户：一共 `count` 张、每张叫什么（`coupons[].name`），以及「会一次全部领进你的账户，接口不支持只领其中几张」。用户明确同意后才运行 `data.next`（带 `--confirm <指纹>`）；用户只想要其中几张、或者没表态，就不要领。预览时 `data.status == "up-to-date"` 说明现在没有可领的券；执行 `data.next` 之后返回 `up-to-date`（带 `note`）表示服务端这次一张都没发，先 `git mcd fetch --json` 核对，不要重试。`--confirm` 报「可领券列表变了」时什么都没领，要重新预览、重新确认。报「不确定券有没有领到」时先 `git mcd fetch --json` 核对还剩哪些可领，不要重试。不要定时、循环或者批量领券，每次都由用户发起。
5. **不代付、不轮询。** 只把 `payH5Url` 和付款截止时间交给用户；用户说付好了，再运行一次 `git mcd show --json` 查取餐码。不要循环查询订单状态。
6. **出错不盲目重试。** `push` 报「不确定订单有没有建成」时，先 `git mcd log --json` 核对最新订单，再和用户商量，绝不直接再 push 一次。
7. **不替用户多点。** 份数、餐品都以用户说的为准；`rebase` 只会在不加东西的前提下省钱，你也不要主动加购。
8. **金额单位是「分」。** JSON 里的金额（`payable`、`price`、`subtotal`、`unitPrice` 等）都是整数分，告诉用户时除以 100，写成 ¥33.50。

## 常用流程

| 步骤 | 命令 | 看 JSON 的哪里 |
|---|---|---|
| 选门店 | `git mcd init [--limit N] --json` | `data.status == "choose"` 时把 `data.candidates` 给用户选，再 `git mcd init --pick <序号> --json`；`--limit N` 只在本地截取前 N 家（接口一次大约只给 5 家，不会多调） |
| 按地标找店 | `git mcd init --city 上海 --keyword 人民广场 [--limit N] --json` | 同上；`--city` 和 `--keyword` 必须一起给（只给其中一个会报错，接口不认）；得来速加 `--mode drive`；麦乐送 `--mode delivery`（再用 `--address <序号>` 选地址） |
| 现状 | `git mcd status --json` | `data.remote`、`data.index`（购物车）、`data.coupons`、`data.pendingPayment` |
| 看菜单 | `git mcd ls-remote <关键词> --json` | `data.meals[]`：`code`、`name`、`type`、`price`（真实可得价）；有 `offerNote` 的是条件优惠（如「¥37.00 需随单购麦金卡」，`offerPrice` 是条件价），要同时买会员卡才成立，不要当成用户能拿到的价格 |
| 加购 | `git mcd add 巨无霸 中薯条 --no-pickle --json` | 名字有歧义时 `ok == false`，候选在 `error.detail.candidates`，问用户要哪个，或直接用 code |
| 特制 / 换搭 | `--no <配料>`、`--with 少冰`、`--with 加一份浓缩`、`--choose 大薯条`、`-n 2` | `data.warnings` 里是没生效的定制 |
| 移出 / 清空 | `git mcd restore --staged <餐品> --json`、`git mcd reset --json` | `data.index` |
| 省钱 | `git mcd rebase --json` | `data.before` → `data.after`、`data.saved`、`data.todo`；不满意 `git mcd reset ORIG_HEAD` |
| 看明细 | `git mcd diff --json` | `data.rows[]`：`a`（原价 / 不用券）、`b`（应付 / 用券后） |
| 锁单 | `git mcd commit -m "午饭" --json` | `data.short`（commit 哈希）、`data.payable` |
| 下单前改餐品 | `add` / `restore --staged` 改购物车 → `git mcd commit --amend --json` 并进这一单 → 重新预览 `git mcd push … --json` | `data.short` 会变，之前的预览作废，要重新给用户确认。`restore` 只动购物车；要去掉已经 commit 的餐品，先 `git mcd reset --soft HEAD~1 --json` 放回购物车，改完再 `git mcd commit -m … --json` |
| 下单预览 | `git mcd push [--at 12:20] [--locker / --eat-in / --take-out] [--no-tableware] [--remark 备注] --json` | `data.status == "confirmation_required"`、`data.summary`、`data.next` |
| 用户同意后 | 原样运行 `data.next` 里的命令（已经带好 `--confirm <哈希>` 和 `--json`） | `data.status == "pushed"`、`data.payH5Url`、`data.expirePayTime` |
| 查取餐码 | `git mcd show --json` | `data.order.statusLabel`、`pickupCode`、`lockerCode`、`delivery` |
| 历史 | `git mcd log --oneline --json` | `data.entries[]`（最多最近 10 单） |
| 吃麦统计 | `git mcd log --stat [-n 10] [--since YYYY-MM-DD] [--until YYYY-MM-DD] --json` | `data.summary`（`orders`、`cancelled`、`pending`、`lines`、`paid`、`saved`、`savedUnknown`、`slots`、`top`；金额是分，只算吃了的单，已取消和待支付不计入）、`data.entries[]`（`counted` 表示是否计入）。数据来自本机账本，`savedUnknown` 是没有优惠明细的单数，省下的钱是下限 |
| 再来一单 | `git mcd cherry-pick HEAD --json`，再 `git mcd commit -m 再来一单 --json`，然后按「下单预览」走 | `data.index`、`data.conflicts`（本店买不到的会列出来）。cherry-pick 只放回购物车，不 commit 就 push 会报「购物车还没 commit」。只用于已经 push 过的单：HEAD 还没 push 时它本来就是这一单，`cherry-pick HEAD` 会被拒绝（`error.detail.code == "empty_cherry_pick"`，购物车不动），直接按「下单预览」走；用户要多点就 `add` 后 `commit --amend` |
| 取消 | `git mcd revert --reason 3 --json` | 预览后用户同意，再运行 `data.next` |
| 活动和可领券 | `git mcd fetch --json` | `data.events`、`data.claimable`（只读；要领就按下面「领券」走） |
| 领券预览 | `git mcd pull --json` | `data.status == "confirmation_required"`、`data.summary`（`count`、`coupons[]`、`note`）、`data.next`；`"up-to-date"` 表示没有可领的券 |
| 用户同意领券后 | 原样运行 `data.next` 里的命令（已经带好 `--confirm <指纹>` 和 `--json`） | `data.status == "pulled"`、`data.successCount`、`data.failedCount`、`data.claimed[]`、`data.failed[]`（`reason`）；领到之后下单前照常 `git mcd rebase` 挑最省的券 |
| 吃麦贡献图 | `git mcd card -o <路径> --json`；`--style skyline`（默认，麦门天际线，贴 GitHub 主页）/ `receipt`（年度吃麦小票，发朋友圈）/ `grid`（平面格子），用户没说就用默认 | `data.style`、`data.path`（写好的 SVG）、`data.stats`（`total`、`topItem`、`topSlot`、`earliest`）；`data.status == "empty"` 说明还没有记录，没写文件。不给 `-o` 时默认文件名按样式区分（`mcd-contributions-<样式>.svg`，互不覆盖）。只在本机写文件：不要替用户把图提交或上传到 GitHub，也不要设置定时任务；默认不加 `--with-savings`，用户想看省了多少钱再加 |

- `--remark` 只用于麦乐送；`--locker` 要门店有取餐柜，没有会报错，把可选的取餐方式告诉用户。
- `--at` 写 `HH:MM`（今天）或 `"YYYY-MM-DD HH:MM"`；预览和确认时要用完全相同的参数。
- `push` 返回 `data.status == "up-to-date"` 说明 HEAD 早就下过单，这次**没有新下单**，不要告诉用户「已下单」。只有 `data.status == "pushed"` 才是新下了一单。
- `--qr` 默认 `full`（整格，各终端都扫得出）；`half` 更紧凑，但部分终端（如 macOS Terminal.app）可能扫不出；`fancy`（麦当劳主题码：金色 + 正中像素麦当劳元素 Logo，6 个图案随机出一个，可用 `fancy=名字` 指定汉堡/薯条/麦乐鸡/甜筒/派/金拱门）是给人看 / 扫的。Agent 走 `--json` 时不用特意加。
- 命令补全（`git mcd completion`）是给真人在终端里按 TAB 用的，Agent 不需要，照常用 `--json`。
- 每条命令的结果都在 `ok` 字段：`false` 时读 `error.message` 和 `error.hints` 转告用户，退出码非 0。

## 一个完整例子

```text
用户：中午想吃巨无霸，不要酸黄瓜，加中薯条和中可乐，12:20 取餐柜取，不要餐具。

你：git mcd add 巨无霸 中薯条 中可乐 --no-pickle --json
    git mcd rebase --json            → before 4550，after 3350（凑成套餐 + 一张立减券）
    git mcd commit -m 午饭 --json    → short: bfc651c
    git mcd push --at 12:20 --locker --no-tableware --json
                                     → status: confirmation_required，summary.payable: 3350
你对用户：门店 演示·代码港店，外带·店外取餐柜，预约 12:20，不要餐具；
          巨无霸套餐 [巨无霸 · 中薯条 · 中可乐] 去酸黄瓜，用券后应付 ¥33.50。
          确认的话我就下单，生成待支付订单后需要你自己付款。

用户：确认。

你：git mcd push --at 12:20 --locker --no-tableware --confirm bfc651c --json
                                     → status: pushed，payH5Url、expirePayTime
你对用户：下好了，请在 12:05 前打开支付链接付款；付完告诉我，我帮你查取餐码。
```

（例子是 `--demo` 合成数据，门店、价格、哈希都是编的。）
