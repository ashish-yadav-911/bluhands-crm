# syntax=docker/dockerfile:1
# openhands-sdk requires Python >=3.12. The control plane itself stays on
# 3.11 -- only this isolated, per-job container needs the newer runtime.
FROM python:3.12-slim

WORKDIR /app

# OpenHands' TerminalTool falls back to a less-stable plain-subprocess shell
# without tmux -- install it for a persistent, more reliable terminal session.
RUN apt-get update && apt-get install -y --no-install-recommends tmux \
    && rm -rf /var/lib/apt/lists/*

# Pinned to the exact same version (they're released as a matched pair) --
# leaving either unpinned lets pip's resolver backtrack across dozens of
# litellm/browser-use version combinations. openhands-tools' dependency
# tree (via browser-use) is large enough that the install itself can take
# several minutes even after resolution -- the cache mount means a retried
# build reuses already-downloaded wheels instead of refetching everything.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install openhands-sdk==1.44.1 openhands-tools==1.44.1

COPY run_agent.py /app/run_agent.py

ENTRYPOINT ["python", "/app/run_agent.py"]
