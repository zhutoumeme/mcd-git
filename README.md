# mcd-git · 用 git 命令点麦当劳

<p>
  <img src="assets/badges/python.svg" alt="python 3.9+">
  <img src="assets/badges/license.svg" alt="license MIT">
  <img src="assets/badges/mcp.svg" alt="MCP streamable http">
  <img src="assets/badges/demo.svg" alt="try --demo 无需 Token">
  <img src="assets/badges/unofficial.svg" alt="非官方 参赛作品">
</p>

`git mcd add` 放进购物车，`git mcd rebase` 自动凑套餐、挑最省的券，`git mcd push` 才真正下单，终端里直接出支付二维码。基于麦当劳中国 MCP Server（`mcp.mcd.cn`）。

![git mcd 演示：status → add → diff → rebase → commit → push 出二维码 → log --graph](assets/demo.gif)

> 动图和下面所有截图都是 `--demo` 合成数据：门店、价格、订单号、券都是编的，支付链接指向 `example.com`。

## 一套引擎，两个入口

- **开发者**：在终端里敲 `git mcd`。`git-mcd` 在 PATH 里，git 就会把 `git mcd …` 交给它，任何目录都能用，不碰你代码仓库的 `.git`。
- **普通用户**：把 [SKILL.md](SKILL.md) 装进 WorkBuddy / Claude Code / Kiro，用大白话点餐，Agent 在后台跑 `git mcd --json`。

一段 Agent 对话（demo 数据）：

```text
你：   中午想吃巨无霸，不要酸黄瓜，再来中薯条和中可乐。12:20 去店外取餐柜拿，不要餐具，看看怎么最省。

Agent：（git mcd add 巨无霸 中薯条 中可乐 --no-pickle --json → git mcd rebase --json → git mcd diff --json）
       三样正好凑成巨无霸套餐，再用一张「示例·巨无霸套餐立减 5 元」：¥45.50 → ¥33.50。
       （git mcd commit -m 午饭 --json → git mcd push --at 12:20 --locker --no-tableware --json）
       门店 演示·代码港店 · 外带·店外取餐柜 · 预约 12:20 · 不要餐具
       巨无霸套餐 [巨无霸 · 中薯条 · 中可乐] 去酸黄瓜，应付 ¥33.50。要下单吗？付款需要你自己完成。

你：   确认。

Agent：（git mcd push --at 12:20 --locker --no-tableware --confirm bfc651c --json）
       下好了，请在 12:05 前打开这个链接付款：https://example.com/mcd-git-demo/pay?orderId=1024…
       付完跟我说一声，我帮你查取餐码。
```

**和普通 Skill 的区别：确认由代码强制执行。** 普通 Skill 只是一段提示词，Agent 手里有 MCP 连接器就能随时直接调 `create-order`。mcd-git 的 Skill 不需要给 Agent 配 MCP 连接器，Token 只给 mcd-git 用，Agent 手里只有 `git mcd` 这一套命令：下单必须先出确认摘要，再用 `--confirm <commit 哈希>` 执行；哈希、参数、重新算出的应付金额，任何一项和预览时对不上，代码都会拒绝。

## git 命令 ↔ 点餐动作

| git mcd | 点餐动作 | 会改动你的账户吗 |
|---|---|---|
| `init` | 选门店和取餐方式（到店 / 得来速 / 麦乐送），相当于设置远端 | 否 |
| `status` | 当前分支（按时段：breakfast / lunch / tea / dinner）、购物车、可用券、待支付订单 | 否 |
| `ls-remote [关键词]` | 看本店菜单 | 否 |
| `add <餐品>` | 放进购物车；`--no-pickle` 去酸黄瓜，`--with 少冰`，`--choose 大薯条` | 否 |
| `restore --staged <餐品>` | 从购物车拿出来 | 否 |
| `reset` | 清空购物车；`reset ORIG_HEAD` 撤销 rebase；`reset --soft HEAD~1` 撤回没 push 的 commit | 否 |
| `diff` | 像 unified diff 一样逐行看差价：菜单价 vs 应付，或用券前 vs 用券后 | 否 |
| `rebase` | 单品凑成套餐（squash）、换上最省的一张券（coupon），每个方案都真实算价 | 否 |
| `commit -m <说明>` | 锁定这一单：本地快照 + 核价，哈希由内容决定 | 否 |
| `push` | **真正下单**，出支付链接和二维码；支持 `--at 12:20` 预约、`--locker` 取餐柜、`--no-tableware`、`--remark` | ✎ 会，先确认 |
| `log --graph` | 吃麦提交历史，按时段画分支图 | 否 |
| `show [HEAD]` | 订单状态、取餐码、取餐柜、外送进度（只在你运行时查一次） | 否 |
| `cherry-pick HEAD` | 再来一单：把上一单拣回购物车，换了门店也会按新菜单重新对齐 | 否 |
| `revert` | 取消订单，必须选原因 | ✎ 会，先确认 |
| `fetch` | 今日活动、可领的券（只读，不替你领） | 否 |

所有命令都支持 `--demo`（合成数据，不需要 Token）和 `--json`（给 Agent 和脚本用）。对照表随时看：`git mcd help`。

## 30 秒试玩，无需 Token

装了 uv 的话，一行就能跑：

```bash
uvx --from git+https://github.com/zhutoumeme/mcd-git git-mcd --demo status
```

想多玩几步，就装上它（需要 Python 3.9+）：

```bash
pip install "git+https://github.com/zhutoumeme/mcd-git"   # 或 uv tool install git+https://github.com/zhutoumeme/mcd-git

git mcd --demo status
git mcd --demo add 巨无霸 中薯条 中可乐 --no-pickle
git mcd --demo diff
git mcd --demo rebase
git mcd --demo commit -m "发版前的午饭"
git mcd --demo push --at 12:20 --locker --no-tableware   # 终端里会问 y/N
git mcd --demo log --graph
git mcd --demo show
git mcd --demo cherry-pick HEAD
```

装完会有两个等价的命令：`git-mcd`（git 会把 `git mcd` 分发给它）和 `mcd-git`。demo 的状态单独放在 `~/.config/mcd-git/demo/`，想从头再来就 `git mcd --demo init --pick 1`。嫌每条都敲 `--demo` 麻烦，可以先 `export MCD_GIT_DEMO=1`（首屏动图就是这么录的）。

## 命令一览

### init：选门店，相当于设置远端

![git mcd init](assets/init.svg)

### status：看看现在什么情况

![git mcd status](assets/status.svg)

### add：放进购物车，顺手去掉酸黄瓜

![git mcd add](assets/add.svg)

### diff：每一分钱花在哪儿

![git mcd diff](assets/diff.svg)

### rebase：凑套餐 + 挑券，省下来的才是真的

`rebase` 只在购物车里的单品正好能凑满套餐（主食、小食、饮料都在）时才 squash，绝不为了便宜往购物车里多塞东西。一单只能用一张券，而且券和账户自带的折扣可能互斥，所以每个方案都会真实调一次 `calculate-price`，每条命令最多 20 次。不满意就 `git mcd reset ORIG_HEAD`。

![git mcd rebase](assets/rebase.svg)

### commit：锁定这一单

![git mcd commit](assets/commit.svg)

### push：确认后才下单，出支付二维码

终端里先给确认摘要再问 y/N。Agent、管道或 `--json` 等非交互环境只出摘要，用户同意后再用 `--confirm <commit 哈希>` 执行。mcd-git 只生成待支付订单，**不会替你付款**。

![git mcd push](assets/push.svg)

### log --graph：吃麦提交历史

![git mcd log --graph](assets/log-graph.svg)

### show：取餐码在这里

![git mcd show](assets/show.svg)

### cherry-pick HEAD：再来一单

![git mcd cherry-pick HEAD](assets/cherry-pick.svg)

### help：对照表

对照表用 `git mcd help` 看（`git mcd -h` 和不带参数的 `git mcd` 也行）。注意别在 `mcd` 后面直接跟 `--help`，git 会把它拦截成 `git help mcd` 去找 man 手册。子命令的帮助照常可用，比如 `git mcd push -h`。

![git mcd help](assets/help.svg)

## 接入真实账户（3 步）

1. **申请 Token**：在 [麦当劳 MCP 开放平台](https://open.mcd.cn/mcp) 登录控制台，激活并复制 MCP Token。
2. **放在本机**：二选一，两个都有时环境变量 `MCD_MCP_TOKEN` 优先，其次读 `~/.config/mcd-mcp/token`。Token 不会被打印、写日志或写进仓库。
   ```bash
   # 长期使用：从剪贴板写进文件（macOS 示例，Token 不会留在 shell 历史里）
   mkdir -p ~/.config/mcd-mcp && pbpaste > ~/.config/mcd-mcp/token && chmod 600 ~/.config/mcd-mcp/token
   # 临时使用：只在当前终端有效
   export MCD_MCP_TOKEN=你的Token
   ```
3. **选门店开点**：`git mcd init` 列收藏门店，或 `git mcd init --city 上海 --keyword 人民广场` 按地标搜；得来速加 `--mode drive`，麦乐送用 `--mode delivery`。之后的用法和 demo 一模一样。

[mcp-config.example.json](mcp-config.example.json) 是比赛要求的脱敏配置示例，只有 `${MCD_MCP_TOKEN}` 占位符。mcd-git 自己不需要它，想让其他 MCP 客户端直连时可以照着配。

**只有 `push` 和 `revert` 会改动你的账户，每次都会先给你看确认摘要；不会自动付款；没有后台任务、定时或轮询，每一次调用都由你的命令触发。**

## 交给你的 Agent

把下面这段话原样发给 WorkBuddy / Claude Code / Kiro（Agent 需要能在本机执行命令）：

```text
请帮我安装 mcd-git 这个点麦当劳的 Skill：
1. 运行 uv tool install git+https://github.com/zhutoumeme/mcd-git（没有 uv 就用 pip install "git+https://github.com/zhutoumeme/mcd-git"），然后运行 git mcd help 确认装好了；
2. 把 https://github.com/zhutoumeme/mcd-git 仓库根目录的 SKILL.md 装进你的 Skill 目录（Claude Code 是 ~/.claude/skills/mcd-git/SKILL.md，Kiro 是 ~/.kiro/skills/mcd-git/SKILL.md，WorkBuddy 在「技能」里导入）；
3. 以后我说要点麦当劳，就按 SKILL.md 用 git mcd --json 操作。下单和取消前必须把确认摘要给我看，我明确同意后才能加 --confirm。
Token 我会自己配置到 ~/.config/mcd-mcp/token，不用在对话里发给你。没有 Token 时先用 --demo 给我演示。
```

## 安全边界

- **写操作只有两个**：`create-order`（push）和 `cancel-order`（revert），都要经过确认闸：预览 → 用户同意 → `--confirm`。领券、积分兑换、抽奖、新建地址这些写操作，代码里一律拦截，不调用。
- **确认闸由代码执行**：`--confirm` 必须对得上 10 分钟内的预览（同一个 commit、同样的参数）；真正下单前重新算价，应付金额和下单参数的指纹都要和预览时一致；同一份确认只能用一次；`push --force` 一律拒绝。
- **结果未知不重试**：网络超时等情况不会自动重发 `create-order`，会提示先 `git mcd log` 核对，避免重复下单。
- **低频、手动**：每条命令由你触发；`calculate-price` 每条命令最多 20 次，带缓存；遇到 429 只对只读请求退避重试。
- **隐私**：Token 只在请求头里，只发给 `https://mcp.mcd.cn`（地址写死在代码里，不能用环境变量改）；收货地址只保留 addressId 和打码提示；状态文件在 `~/.config/mcd-git/`（目录 700、文件 600）。

## 目标用户

- **天天开终端的开发者**：不想切到手机上凑券、比价，`git mcd status && git mcd rebase` 一气呵成。
- **想让 Agent 代点餐、但每一步都要看得见管得住的人**：Agent 只能用带确认闸的命令，下单前一定先问你。
- **常点固定几样的人**：`git mcd cherry-pick HEAD && git mcd commit -m 再来一单 && git mcd push` 就是再来一单；预约、取餐柜、不要餐具一条命令说清楚。

它解决两件事：自己算「单点还是套餐、用哪张券」太费劲；把下单交给 Agent 又怕它自作主张。

## FAQ

**真的会下单吗？** 只有 `push` 并且你确认之后才会。生成的是待支付订单，付款要你自己在支付页或麦当劳 App 里完成。

**会读写我的代码仓库吗？** 不会。mcd-git 不碰任何 `.git` 目录，状态只放在 `~/.config/mcd-git/`。

**rebase 会不会为了省钱让我多吃？** 不会。只有购物车里的东西正好凑满一个套餐时才换，不会额外加任何餐品。

**价格准吗？** 一切以 `calculate-price` 的实时结果为准。`commit` 时核一次价，`push` 预览和确认时各核一次，价格变了会拦下来让你重新确认。

**为什么一单只能用一张券？** 这是 MCP 的规则：两张券会返回「暂不支持多张券使用」（600022）。

**`log` 为什么最多只有 10 单？** `order-list` 没有分页参数，只返回最近 10 单。

**`show` 会一直刷新订单状态吗？** 不会。只在你运行时查一次，不轮询。

**没有 Token 能用吗？** 加 `--demo` 就行，全套命令都能离线体验。

## 路线图

- **P1**：`stash` / `stash pop` 暂存购物车；`pull` 领券（写操作，同样走确认闸）；`rebase -i` 手动挑方案；预约时段在本地先校验。
- **P2**：`card` 在本地生成 SVG「吃麦贡献图」；`ship --team` 发版庆功的团餐方案（下单仍走 push）；`push --at next-gap` 读本地 .ics 找会议空档。

欢迎提 Issue 和 PR，新命令请保持「只读命令零副作用、写操作必须过确认闸」这条规矩。

## 更多文档

- [MCP_INTEGRATION.md](MCP_INTEGRATION.md)：用到的 MCP tool、命令和 tool 的对应关系、调用时序图、业务价值和写操作边界
- [SKILL.md](SKILL.md)：给 Agent 的使用说明
- [CONTEST_DECLARATION.md](CONTEST_DECLARATION.md)：参赛声明
- 截图和动图可以复现：`python scripts/make_screenshots.py`、`vhs scripts/demo.tape`

## 声明

- 本项目是麦当劳程序员节创意开发大赛的参赛作品，由参赛者独立开发，非麦当劳官方产品。
- 项目输出仅供参考，不构成医疗、营养或其他专业建议。
- 餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。

仓库里不使用麦当劳的 Logo、IP 形象或官方图片；终端截图和 `--demo` 夹具全部是合成数据。代码以 [MIT](LICENSE) 协议开源。
