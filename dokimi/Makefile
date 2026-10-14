# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Base snapshot for `benchmark-compare`. The repository ships no committed
# baseline: take your own with `make benchmark-run` (writes the git-ignored
# baselines/current.json), copy it once as the base you want to track against,
# then compare later runs against it:
#
#   make benchmark-run
#   cp baselines/current.json baselines/baseline.json
#   # ... change docs ...
#   make benchmark-run
#   make benchmark-compare                      # or: make benchmark-compare BASE=<path>
BASE ?= baselines/baseline.json

.PHONY: validate-benchmark-spec benchmark-run benchmark-compare dev ci-local hooks

# `make dev` venv: at the repo root, outside this directory, so `bandit -r .`
# and the docs benchmark (both run from here) do not scan it. CI's Python:
# 3.13 until scikit-network ships cp314 wheels (see the top of ci.yml). An
# existing venv on another Python is converted in place by re-running `make dev`.
VENV ?= ../.venv
PYTHON_VERSION ?= 3.13
VENV_ABS := $(abspath $(VENV))
VPY := $(VENV_ABS)/bin/python

benchmark-run:
	python cli.py run --root . --spec benchmarks/spec.v1.yaml --out-json baselines/current.json --out-md reports/current.md

benchmark-compare:
	@test -f "$(BASE)" || { echo "ERROR: base snapshot $(BASE) not found. Take one with 'make benchmark-run', copy baselines/current.json to it, or pass BASE=<path>."; exit 1; }
	python cli.py compare --base $(BASE) --candidate baselines/current.json --out-json reports/compare.json --out-md reports/compare.md

validate-benchmark-spec:
	@command -v yq >/dev/null 2>&1 || { echo "ERROR: yq is not installed"; exit 1; }
	@echo "Checking YAML parse for benchmarks/spec.v1.yaml"
	@yq eval '.' benchmarks/spec.v1.yaml >/dev/null
	@echo "Converting YAML -> JSON and validating against dokimi/runner/spec.schema.json"
	@yq eval -o=json benchmarks/spec.v1.yaml > benchmarks/.spec.v1.tmp.json
	@if command -v ajv >/dev/null 2>&1; then \
		ajv validate -s dokimi/runner/spec.schema.json -d benchmarks/.spec.v1.tmp.json --spec=draft2020; \
	elif command -v npx >/dev/null 2>&1; then \
		mkdir -p .npm-cache; \
		npm_config_cache=$(PWD)/.npm-cache npx --yes ajv-cli validate -s dokimi/runner/spec.schema.json -d benchmarks/.spec.v1.tmp.json --spec=draft2020; \
	else \
		echo "ERROR: neither 'ajv' nor 'npx' is installed"; \
		echo "Install one of:"; \
		echo "  npm i -g ajv-cli"; \
		echo "  # or install Node.js/npm to use npx"; \
		exit 1; \
	fi
	@rm -f benchmarks/.spec.v1.tmp.json
	@echo "✅ Benchmark spec is valid"

# A venv matching CI: the same Python, the same lock, and every tool the CI
# install steps name (read from the workflows by scripts/ci_local.py, so a tool
# added to CI is picked up here). The lock goes first: ci_local.py needs PyYAML.
dev:
	uv venv --python $(PYTHON_VERSION) --seed --allow-existing $(VENV_ABS)
	uv pip install --python $(VPY) -r requirements.lock.txt
	uv pip install --python $(VPY) $$($(VPY) scripts/ci_local.py tools)

# Every step of the PR workflows, in file order, from each step's own cwd. It
# runs every step even after a failure and ends with a pass/fail/skip table.
ci-local:
	@test -x $(VPY) || { echo "ERROR: $(VENV_ABS) not found -- run 'make dev' first"; exit 1; }
	PATH="$(VENV_ABS)/bin:$$PATH" VIRTUAL_ENV="$(VENV_ABS)" \
		$(VPY) scripts/ci_local.py run $(CI_LOCAL_ARGS)

# pre-push: lint, mypy, schema/registry checks, workflow lint, bandit. No pytest
# (minutes), no network. Skip once with `git push --no-verify`.
hooks:
	@hook="$$(git rev-parse --git-path hooks)/pre-push"; \
	if [ -e "$$hook" ] && ! grep -q '^# ci-local pre-push hook' "$$hook"; then \
		echo "ERROR: $$hook exists and is not ours; remove it first"; exit 1; fi; \
	printf '#!/bin/sh\n# ci-local pre-push hook\nexec make -C "$$(git rev-parse --show-toplevel)/dokimi" ci-local CI_LOCAL_ARGS="--offline --jobs lint,typecheck,schema,workflow-lint,security"\n' \
		> "$$hook" && chmod +x "$$hook" && echo "installed $$hook"
