# moray-scribe — one image, whole toolchain: CLI + web viewer + graph-RAG chat.
#
# Build:  docker build -t moray-scribe .
# Run:    docker run --rm -p 8000:8000 -v "$(pwd)":/work:ro moray-scribe
#
# The viewer connects to Ollama on the HOST machine (no model inside the
# image) via host.docker.internal — set OLLAMA_HOST to override.

FROM python:3.12-slim

# Tsinghua PyPI mirror by default: fast and reachable from CN networks.
# Override with --build-arg PIP_INDEX_URL=https://pypi.org/simple on
# well-connected machines.
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Layer-cache the dependency install: copy metadata first, install, then the
# rest of the source so code edits don't re-download the world.
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

COPY web_viewer/ ./web_viewer/
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && useradd --create-home --uid 1000 moray \
    && mkdir -p /data && chown moray:moray /data

USER moray
WORKDIR /data

# /work  = the project to analyze (mount read-only)
# /data  = where graphs and docs are written (mount a volume)
VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["viewer"]
