#!/usr/bin/env bash
# moray-scribe 一键安装（Linux / macOS）。
#
#   bash install.sh
#
# 四步：找 Python >= 3.10 → 建独立 venv → 装 moray-scribe → 检查 Ollama。
# 全部可跳过/重定向，绝不静默换解释器、不写系统目录。
#
# 环境变量：
#   MORAY_PIP_INDEX          pip 源（默认 https://pypi.org/simple；国内推荐
#                            https://pypi.tuna.tsinghua.edu.cn/simple）
#   MORAY_VERSION            版本约束（默认 >=0.5）
#   MORAY_HOME               安装根目录（默认 ~/.moray-scribe）
#   MORAY_SKIP_OLLAMA_CHECK  =1 跳过 Ollama 检查
set -euo pipefail

VERSION_CONSTRAINT="${MORAY_VERSION:->=0.5}"
PIP_INDEX="${MORAY_PIP_INDEX:-https://pypi.org/simple}"
MORAY_HOME="${MORAY_HOME:-$HOME/.moray-scribe}"
VENV_DIR="$MORAY_HOME/venv"

log()  { printf '\033[1;36m[moray-scribe]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[moray-scribe]\033[0m %s\n' "$*" >&2; }
die()  { printf '[moray-scribe] %s\n' "$*" >&2; exit 1; }

# --- 1. find python >= 3.10 ----------------------------------------------
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  warn "没找到 Python >= 3.10。"
  warn "Ubuntu/Debian:  sudo apt install python3.12 python3.12-venv"
  warn "macOS:          brew install python@3.12"
  warn "或用 Docker（无需本地 Python）：docker run --rm -p 8000:8000 -v \"\$(pwd)\":/work:ro moray-scribe"
  exit 1
fi
log "使用 Python: $PYTHON ($("$PYTHON" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"

# --- 2. venv --------------------------------------------------------------
mkdir -p "$MORAY_HOME"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  log "创建虚拟环境: $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR" || die "venv creation failed; Debian/Ubuntu needs: apt install python3-venv"
fi

PIP="$VENV_DIR/bin/pip"
MORAY_BIN="$VENV_DIR/bin/moray-scribe"

# --- 3. install -----------------------------------------------------------
log "安装 moray-scribe $VERSION_CONSTRAINT (index: $PIP_INDEX)"
ARTIFACT=""
if compgen -G "dist/moray_scribe-*.whl" >/dev/null; then
  ARTIFACT=$(ls -t dist/moray_scribe-*.whl | head -n 1)
elif compgen -G "dist/moray_scribe-*.tar.gz" >/dev/null; then
  ARTIFACT=$(ls -t dist/moray_scribe-*.tar.gz | head -n 1)
fi

"$PIP" install --upgrade pip
if [ -n "$ARTIFACT" ]; then
  log "发现本地产物，优先使用: $ARTIFACT"
  "$PIP" install "$ARTIFACT" --index-url "$PIP_INDEX" \
    || die "本地产物安装失败，检查 MORAY_PIP_INDEX"
else
  "$PIP" install "moray-scribe$VERSION_CONSTRAINT" --index-url "$PIP_INDEX" \
    || die "PyPI 安装失败：检查网络，或 MORAY_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
fi

"$MORAY_BIN" --version || die "安装后无法运行 moray-scribe --version"

# --- 4. Ollama check (advisory) -------------------------------------------
if [ "${MORAY_SKIP_OLLAMA_CHECK:-0}" != "1" ]; then
  ollama_url="${OLLAMA_HOST:-http://127.0.0.1:11434}"
  if curl -s --max-time 3 "$ollama_url/api/tags" >/dev/null 2>&1; then
    log "检测到 Ollama 在运行 ✓ （doc / 图谱问答可用）"
  else
    warn "没检测到 Ollama（$ollama_url）。doc 模式和图谱问答需要它："
    warn "  安装与模型拉取方法见 ollama.com/download，然后: ollama pull qwen2.5:7b"
    warn "静态分析（scan/graph）与拓扑查看器完全不需要 Ollama。"
  fi
fi

log "安装完成 ✓  试试："
log "  $MORAY_BIN graph ./your-project --out out/"
log "  $MORAY_BIN doc ./your-project --out out/      # 需要 Ollama"
log "  $MORAY_BIN check . --baseline <基线.json> --fail-on none"
log "  拓扑查看器：python web_viewer/serve.py out/pruned-graph.json"
