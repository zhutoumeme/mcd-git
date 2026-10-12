# README 首屏动图（约 15 秒）。全部是 --demo 合成数据：不需要 Token，不会真的下单。
#
# 用法（仓库根目录，先 uv pip install -e . 装好 .venv）：
#     vhs scripts/demo.tape
#
# 录制时用临时 HOME 和 MCD_GIT_DEMO=1：git mcd 在 PATH 里找到 .venv/bin/git-mcd，
# 状态写进临时目录，录完即丢。
#
# 标题注释和第一条 git mcd status 在 Hide 里先跑完：GitHub 在动图加载前只显示第一帧，
# 这样第一帧就是完整的 status 输出，而不是空屏。

Output assets/demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1000
# 0.2.4 起默认整格二维码：一个模块占两格、一行一个模块行（demo 链接 37 行 × 74 列）。
# 高度要放得下 push 命令、确认摘要、整个二维码和 hint（约 60 行）
Set Height 1060
Set Padding 18
Set Framerate 24
Set TypingSpeed 14ms
Set Theme "GitHub Dark"

Hide
Type "unset PROMPT_COMMAND; export PATH=$PWD/.venv/bin:$PATH HOME=$(mktemp -d) MCD_GIT_DEMO=1 PS1='$ '; clear"
Enter
Sleep 500ms
Type "# demo 模式：合成数据，不需要 Token，也不会真的下单"
Enter
Type "git mcd status"
Enter
Sleep 2s
Show

Sleep 1.6s

Type "git mcd add 巨无霸 中薯条 中可乐 --no-pickle"
Enter
Sleep 900ms

Type "git mcd diff"
Enter
Sleep 1.3s
Ctrl+L

Type "git mcd rebase"
Enter
Sleep 1.4s

Type 'git mcd commit -m "发版前的午饭"'
Enter
Sleep 700ms
Ctrl+L

Type "git mcd push --at 12:20 --locker --no-tableware"
Enter
Sleep 1.1s
Type "y"
Sleep 200ms
Enter
Sleep 2.2s
Ctrl+L

Type "git mcd log --graph"
Enter
Sleep 2s
