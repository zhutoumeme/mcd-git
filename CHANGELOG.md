# 更新日志

## 0.2.0（2026-10-11）

### 新增

- `git mcd card`：1024 吃麦贡献图。7 行 × 最近 53 周的 GitHub 风格格子，终端里预览，同时在本机写一个自包含的 SVG（默认 `./mcd-contributions.svg`，`-o` 改路径；浅色 / 深色自动切换，不引用外部字体、图片和脚本）。统计只放总单数、最常点、最常时段，`--with-savings` 才显示用券省下的钱；卡片上没有门店、订单号、具体时刻和总消费。10-24 那格单独描边，当天副标题是「Happy 0x400 Day · 1024 程序员节快乐」。支持 `--demo` 和 `--json`。
- 本机订单账本 `~/.config/mcd-git/ledger.json`（demo 另有一份，文件权限 600）：`log`、`cherry-pick`、`card` 查 `order-list` 时顺手合并最近 10 单，`show` 补上那一单的原价和优惠，`push` 成功时记一笔并关联本地 commit，`revert` 成功时标成已取消。只存下单时间、状态、餐品名和份数、原价 / 优惠 / 实付、取餐方式；写不进去也不影响原来的命令；删掉文件即清空。
- `--demo` 补了一年的合成订单（每周 1～3 单，以工作日午饭为主，周末零星几顿早餐和下午茶），`git mcd --demo card` 直接能看到效果。

## 0.1.0（2026-10-10）

首个版本。

- git 风格的子命令：`init`、`status`、`ls-remote`、`add` / `restore --staged` / `reset`、`diff`、`rebase`、`commit`、`push`、`log --graph`、`show`、`cherry-pick`、`revert`、`fetch`、`help`。
- `rebase` 只在购物车里的单品正好凑满时 squash 成套餐，再按真实算价挑最省的一张券，不多加任何餐品。
- `push`（create-order）和 `revert`（cancel-order）走确认闸：先出摘要，用户同意后 `--confirm` 执行；commit、参数、重新算出的价格和下单指纹任何一项对不上都拒绝；结果未知不重试。
- `--demo` 合成数据全流程离线可玩；`--json` 输出 + SKILL.md 给 Agent 用。
- 终端里直接出支付二维码；只连 `https://mcp.mcd.cn`，不跟随重定向。
