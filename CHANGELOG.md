# 更新日志

## 0.2.0（2026-10-11）

### 新增

- `git mcd card`：1024 吃麦贡献图，最近 53 周，终端里预览，同时在本机写一个自包含的 SVG（`-o` 改路径；浅色 / 深色自动切换，不引用外部字体、图片和脚本，同样的数据输出完全一致）。`--style` 选三种样式：
  - `skyline` 麦门天际线（默认）：等距 3D，每天吃几次就叠几层汉堡，超过 4 层只画 4 层加「+」，10-24 插一面「0x400」小旗；纯 CSS 落下动画只播一次、3 秒内结束，减弱动态效果时直接显示完成状态；默认深色夜景，浅色模式换奶油底；用 `<defs>` + `<use>` 复用图形，一年的图二三十 KB。默认写到 `./mcd-contributions.svg`。
  - `receipt` 年度吃麦小票：竖版热敏小票，TOP 5 商品行和频次条、点阵热力图（红圈标 10-24）、时段分布、TOTAL、由统计数据确定生成的装饰条形码；终端里打印同样内容的文字小票。默认写到 `./mcd-receipt.svg`。
  - `grid` 平面格子：GitHub 风格的 7 行 × 53 周格子，10-24 那格单独描边，当天副标题是「Happy 0x400 Day · 1024 程序员节快乐」。
  - 统计只放总单数、最常点、最常时段，`--with-savings` 才显示用券省下的钱（只算有优惠明细的单，是下限，缺明细时卡片和终端都会说明）；卡片上没有门店、订单号、具体时刻和总消费。支持 `--demo` 和 `--json`（`data.style`）。
- 本机订单账本 `~/.config/mcd-git/ledger.json`（demo 另有一份，文件权限 600）：`log`、`cherry-pick`、`card` 查 `order-list` 时顺手合并最近 10 单，`show` 补上那一单的原价和优惠，`push` 成功时记一笔并关联本地 commit，`revert` 成功时标成已取消。只存下单时间、状态、餐品名和份数、原价 / 优惠 / 实付、取餐方式；写不进去也不影响原来的命令；删掉文件即清空。
- `--demo` 补了一年的合成订单（每周 1～3 单，以工作日午饭为主，周末零星几顿早餐和下午茶），`git mcd --demo card` 直接能看到效果。
- `git mcd pull`：领券，和 push 一样先确认。像 `git pull` 一样 fetch + merge：先用 `available-coupons` 列出麦麦省里可领的券，确认后调一次 `auto-bind-coupons` 全部领取（接口没有参数，不支持只领其中几张，摘要里会写明）。终端里问 y/N；非交互和 `--json` 只出摘要，`data.next` 给出带 `--confirm <指纹>` 的完整命令。指纹由可领券列表算出，预览 10 分钟有效、只能用一次；确认时重新拉一次列表，变了就拒绝。结果未知（超时、响应不完整、看不懂、Ctrl+C）不重试，提示先 `git mcd status` 或 `git mcd fetch` 核对。输出仿 git pull（`From mcd.cn:麦麦省`、`* [new coupon] <券名> -> coupons/<短名>`、领到 / 失败几张）；没有可领的券时输出 `Already up to date.`；`--force` 一律拒绝，`--dry-run` 只出预览。支持 `--demo` 和 `--json`。
- `git mcd log --stat`：吃麦统计。仿 `git log --stat`，每单列出餐品行（` 巨无霸套餐 | 1 +`）和一行小结（几样、实付、用券省了多少）；最后仿 `diff --stat` 汇总几单（其中几单已取消、几单待支付）、几样、实付、省下的钱，再按早餐 / 午饭 / 下午茶 / 晚饭 / 夜宵画时段分布，列出最常点的前 3 名（`shortlog -sn` 风格）。已取消的单照常显示但不计入金额，待支付的单单独标出、也不计入，口径和 `card` 一样。数据来自本机账本，运行时先调一次 `order-list` 刷新；支持 `-n`、`--since`、`--until`（含当天）、`--demo` 和 `--json`。没有优惠明细的单会在汇总里提示，省下的钱是下限。不显示热量。

### 变化

- 写操作放行收紧：客户端只放行 `create-order`、`cancel-order`、`auto-bind-coupons` 三个写操作，确认闸签发的许可也必须和要调用的 tool 一致；积分兑换、抽奖、派对订单、新建地址带着许可也调不出去。
- `fetch` 有可领券时的提示从「请在麦当劳 App 里领」改成「git mcd pull 领取：会先确认，一次领全部」。

## 0.1.0（2026-10-10）

首个版本。

- git 风格的子命令：`init`、`status`、`ls-remote`、`add` / `restore --staged` / `reset`、`diff`、`rebase`、`commit`、`push`、`log --graph`、`show`、`cherry-pick`、`revert`、`fetch`、`help`。
- `rebase` 只在购物车里的单品正好凑满时 squash 成套餐，再按真实算价挑最省的一张券，不多加任何餐品。
- `push`（create-order）和 `revert`（cancel-order）走确认闸：先出摘要，用户同意后 `--confirm` 执行；commit、参数、重新算出的价格和下单指纹任何一项对不上都拒绝；结果未知不重试。
- `--demo` 合成数据全流程离线可玩；`--json` 输出 + SKILL.md 给 Agent 用。
- 终端里直接出支付二维码；只连 `https://mcp.mcd.cn`，不跟随重定向。
