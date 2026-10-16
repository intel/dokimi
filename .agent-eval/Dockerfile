# Build image for dokimi's own repository-mode evals: CI's Python, the lock, every CI tool,
# and a warm uv cache so `make dev` inside a run works without downloading anything.
FROM python:3.11-slim-bookworm
# Debian's point releases replace these package versions, so pinning them would break the build.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends git make ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/* && ln -sf /usr/local/bin/python3 /usr/bin/python3 && ln -sf /usr/local/bin/python3 /usr/bin/python
RUN pip install --no-cache-dir uv==0.11.9
# A configured identity, as on a contributor's machine: agents commit (and sign off) as it.
RUN git config --system user.name "Eval Contributor" && git config --system user.email "contributor@example.com"
COPY dokimi/requirements.lock.txt dokimi/requirements.txt dokimi/requirements-test.txt /opt/dokimi-req/
# The tools CI installs, all pinned so a rerun grades alike; ruff and mypy at the versions ci.yml pins.
RUN pip install --no-cache-dir -r /opt/dokimi-req/requirements.lock.txt \
    && pip install --no-cache-dir ruff==0.17.0 mypy==2.4.0 types-PyYAML==6.0.12.20260906 \
       types-jsonschema==4.26.0.20261006 zizmor==1.30.1 bandit==1.9.4 pip-audit==2.10.1 diff-cover==10.6.0
ENV UV_CACHE_DIR=/opt/uv-cache UV_PYTHON_PREFERENCE=only-system UV_LINK_MODE=copy
RUN uv venv --python 3.11 --seed /tmp/warm && uv pip install --python /tmp/warm/bin/python \
       -r /opt/dokimi-req/requirements.lock.txt ruff==0.17.0 mypy==2.4.0 types-PyYAML==6.0.12.20260906 \
       types-jsonschema==4.26.0.20261006 zizmor==1.30.1 bandit==1.9.4 \
    && rm -rf /tmp/warm && chmod -R a+rwX /opt/uv-cache
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
