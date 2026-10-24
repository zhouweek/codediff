#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  printf '请先安装 Python 3.9 或更新版本。\n' >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  printf '请先安装 Git 2.30 或更新版本。\n' >&2
  exit 1
fi
if ! python3 -c 'import sys; assert sys.version_info >= (3, 9); import pygments' >/dev/null 2>&1; then
  printf '请使用 Python 3.9+，并运行 python3 -m pip install -r requirements.txt 安装依赖。\n' >&2
  exit 1
fi
exec python3 server.py "$@"
