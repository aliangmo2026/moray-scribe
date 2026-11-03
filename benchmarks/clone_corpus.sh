#!/usr/bin/env bash
# Clone the v0.5 benchmark corpus (10 top Python projects, shallow).
# Primary channel: gitee.com/mirrors (GitHub is unreachable from this box).
# Usage: benchmarks/clone_corpus.sh [DEST_DIR]
set -u

DEST="${1:-.stress10}"
mkdir -p "$DEST"

repos="tqdm flask requests pandas scrapy click rich pydantic fastapi aiohttp"

for repo in $repos; do
  if [ -d "$DEST/$repo/.git" ]; then
    echo "[skip] $repo already cloned"
    continue
  fi
  echo "[clone] $repo ..."
  if git clone --depth 1 --single-branch "https://gitee.com/mirrors/$repo.git" "$DEST/$repo" 2>&1 | tail -1; then
    echo "[ok] $repo"
  else
    echo "[FAIL] $repo"
  fi
done

echo "---- corpus summary ----"
for repo in $repos; do
  if [ -d "$DEST/$repo/.git" ]; then
    files=$(find "$DEST/$repo" -name '*.py' -not -path '*/test*' | wc -l)
    echo "$repo: $files py files (non-test)"
  else
    echo "$repo: MISSING"
  fi
done
