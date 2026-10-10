# README 首屏动图（约 15 秒）。全部是 --demo 合成数据：不需要 Token，不会真的下单。
#
# 用法（仓库根目录，先 uv pip install -e . 装好 .venv）：
#     vhs scripts/demo.tape
#
# 录制时用临时 HOME 和 MCD_GIT_DEMO=1：git mcd 在 PATH 里找到 .venv/bin/git-mcd，
# 状态写进临时目录，录完即丢。

Output assets/demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1000
Set Height 780
Set Padding 18
Set Framerate 24
Set TypingSpeed 14ms
Set Theme "GitHub Dark"

Hide
Type "unset PROMPT_COMMAND; export PATH=$PWD/.venv/bin:$PATH HOME=$(mktemp -d) MCD_GIT_DEMO=1 PS1='$ '; clear"
Enter
Sleep 500ms
Show

Type "# demo 模式：合成数据，不需要 Token，也不会真的下单"
Sleep 300ms
Enter
Type "git mcd status"
Enter
Sleep 1.1s

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
