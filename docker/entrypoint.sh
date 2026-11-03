#!/bin/sh
# moray-scribe container entrypoint.
#
#   /work  — the project to analyze (mount read-only)
#   /data  — graph/doc artifacts (mount a volume)
#   OLLAMA_HOST — where the model lives; default = Ollama on the HOST machine
#
# Modes (docker-entrypoint CMD):
#   viewer (default) — analyze /work then serve the interactive UI on :8000
#   graph            — analyze /work, write /data/pruned-graph.json, exit
#   doc              — analyze /work, write ARCHITECTURE.md (+ graph), exit
#   shell            — drop into a shell for debugging
set -eu

: "${OLLAMA_HOST:=http://host.docker.internal:11434}"
PROJECT="${MORAY_PROJECT:-/work}"
DATA="${MORAY_DATA_DIR:-/data}"
EXCLUDES="--exclude **/tests/** --exclude **/.venv/** --exclude **/__pycache__/**"

mkdir -p "$DATA"
cd "$DATA"

# The scanner resolves a package-style project (one with a top-level
# __init__.py) to its parent; /work's parent inside the container is /, which
# would walk the whole volume. Copy the project one level deeper so the
# resolved scan root is a small dedicated directory, and keep the source there
# for the viewer's source panel / chat slices.
SCAN_BASE="/tmp/scan"
SCAN_PROJECT="$SCAN_BASE/project"
case "${1:-viewer}" in
  viewer|graph|doc)
    if [ ! -d "$PROJECT" ]; then
      echo "error: mount the project at /work (docker run -v \"\$(pwd)\":/work:ro ...)" >&2
      exit 1
    fi
    rm -rf "$SCAN_BASE"
    mkdir -p "$SCAN_PROJECT"
    cp -r "$PROJECT"/. "$SCAN_PROJECT"/
    SOURCE_ROOT="$SCAN_BASE"
    if [ ! -f "$SCAN_PROJECT/__init__.py" ]; then
      SOURCE_ROOT="$SCAN_PROJECT"
    fi
    ;;
esac

case "${1:-viewer}" in
  graph)
    exec moray-scribe graph "$SCAN_PROJECT" --out "$DATA" $EXCLUDES
    ;;
  doc)
    # The doc stage talks to OLLAMA_HOST; inside the container the host's
    # Ollama is reachable via host.docker.internal (compose adds the mapping).
    exec moray-scribe doc "$SCAN_PROJECT" --out "$DATA" $EXCLUDES
    ;;
  viewer)
    echo "[moray-scribe] analyzing $PROJECT -> $DATA"
    moray-scribe graph "$SCAN_PROJECT" --out "$DATA" $EXCLUDES
    echo "[moray-scribe] serving UI on 0.0.0.0:8000 (chat model talks to $OLLAMA_HOST)"
    exec python /app/web_viewer/serve.py "$DATA/pruned-graph.json" \
      --host 0.0.0.0 --port 8000 \
      --project "$(basename "$PROJECT")" \
      --ollama-host "$OLLAMA_HOST" \
      --source-root "$SOURCE_ROOT" \
      --no-browser
    ;;
  shell)
    exec /bin/sh
    ;;
  *)
    exec "$@"
    ;;
esac
