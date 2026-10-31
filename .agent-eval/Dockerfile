# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Build image for dokimi's own repository-mode evals: CI's Python, the lock, every CI tool,
# pinned by digest. To move the pin: `docker buildx imagetools inspect python:3.11-slim-bookworm`, then `eval check`.
FROM python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89
# Debian's point releases replace these package versions, so pinning them would break the build.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends git make ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/* && ln -sf /usr/local/bin/python3 /usr/bin/python3 && ln -sf /usr/local/bin/python3 /usr/bin/python
# A configured identity, as on a contributor's machine: agents commit (and sign off) as it.
RUN git config --system user.name "Eval Contributor" && git config --system user.email "contributor@example.com"
COPY dokimi/requirements.lock.txt dokimi/requirements-ci.lock.txt dokimi/requirements.txt \
     dokimi/requirements-test.txt /opt/dokimi-req/
# The lock and the tools CI installs (uv among them), hash-checked so a rerun grades alike.
RUN pip install --no-cache-dir --require-hashes -r /opt/dokimi-req/requirements.lock.txt \
       -r /opt/dokimi-req/requirements-ci.lock.txt
ENV UV_CACHE_DIR=/opt/uv-cache UV_PYTHON_PREFERENCE=only-system UV_LINK_MODE=copy
RUN uv venv --python 3.11 --seed /tmp/warm && uv pip install --python /tmp/warm/bin/python \
       --require-hashes -r /opt/dokimi-req/requirements.lock.txt -r /opt/dokimi-req/requirements-ci.lock.txt \
    && rm -rf /tmp/warm && chmod -R a+rwX /opt/uv-cache
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
