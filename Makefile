# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Top-level Makefile for dokimi, A/B testing for agent context.
#
# The whole runnable project lives in dokimi/; every target here
# delegates into it. Run `make help` for the current target list.

BENCH_DIR := dokimi

.PHONY: help test benchmark-run benchmark-compare validate-benchmark-spec dev ci-local hooks

help:
	@echo "dokimi (A/B testing for agent context) — top-level targets:"
	@echo "  make test                    Run the dokimi test suite"
	@echo "  make benchmark-run           Run the static agent-quality benchmark"
	@echo "  make benchmark-compare       Compare benchmark snapshots (BASE=<your snapshot>)"
	@echo "  make validate-benchmark-spec Validate the benchmark spec schema"
	@echo "  make dev                     Build .venv (uv, CI's Python, the lock + every CI tool)"
	@echo "  make ci-local                Run the PR CI steps locally, read from the workflow files"
	@echo "                               (CI_LOCAL_ARGS='--offline', '--jobs static,typecheck', '--with-docker')"
	@echo "  make hooks                   Install a git pre-push hook running the fast offline subset"
	@echo ""
	@echo "All targets delegate into $(BENCH_DIR)/."
	@echo "See $(BENCH_DIR)/Makefile for the full benchmark target list."

# Run the benchmark test suite from its own directory.
test:
	$(MAKE) -C $(BENCH_DIR) test 2>/dev/null || (cd $(BENCH_DIR) && python -m pytest -q)

# Delegate benchmark targets into the benchmark project's own Makefile.
benchmark-run benchmark-compare validate-benchmark-spec dev ci-local hooks:
	$(MAKE) -C $(BENCH_DIR) $@
