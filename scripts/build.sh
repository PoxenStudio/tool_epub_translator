#!/usr/bin/env bash
# 构建 epub_translator 工具安装包。
#
# 不依赖全局安装 mytool：优先用本机全局的 mytool（更快），拿不到时回退到 npx，
# 保证"直接构建出安装包"这一步在任何机器上都能跑。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "== validate =="
if command -v mytool >/dev/null 2>&1; then
  mytool validate .
  echo "== build =="
  mytool build .
else
  npx --yes mybooks-tools-builder validate .
  echo "== build =="
  npx --yes mybooks-tools-builder build .
fi
