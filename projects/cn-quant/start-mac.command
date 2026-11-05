#!/bin/bash
# Double-click to start 量化助手. First run sets everything up (3-5 minutes).
cd "$(dirname "$0")" || exit 1

pause_and_exit() { read -r -p "按回车键关闭……"; exit "$1"; }

PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 &&
     "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "没有找到 Python 3.10 或更新的版本。"
  echo "请先从 https://www.python.org/downloads/ 下载安装，再双击本文件。"
  pause_and_exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "第一次运行：正在准备环境，大约需要 3-5 分钟……"
  "$PY" -m venv .venv || { echo "准备环境失败，请截图发给我。"; pause_and_exit 1; }
fi
echo "正在检查组件……"
.venv/bin/python -m pip install --disable-pip-version-check -q -e ".[app]" \
  || echo "（安装组件时出错，尝试直接启动……）"

echo ""
echo "正在启动，浏览器会自动打开 http://localhost:8502"
echo "用完后关闭这个窗口即可退出。"
(sleep 6; open "http://localhost:8502") &
.venv/bin/python -m streamlit run src/cnquant/app.py
pause_and_exit 0
