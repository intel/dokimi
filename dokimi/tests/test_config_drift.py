# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for products config loading and cross-registry drift detection."""

from __future__ import annotations

import textwrap

from dokimi.config import (
    detect_registry_drift,
    load_products_config,
)
from dokimi.registry import ProductRegistry


def test_committed_configs_have_no_drift():
    """The committed pair must agree, and the check must see a shared product.

    config/products.yaml declares no products of its own, so a bare
    ``detect_registry_drift()`` compares nothing. Adding one product that does
    agree with the registry proves the clean answer is a real comparison and
    not an empty loop.
    """
    assert detect_registry_drift() == []

    registry = ProductRegistry()
    with_repo = [e for e in registry.list() if e.repo]
    assert with_repo, "no registry entry declares a repo, nothing to compare"
    entry = with_repo[0]
    products = load_products_config()
    products.products = {entry.key: {"github_repo": entry.repo}}
    assert detect_registry_drift(products=products) == []


def test_load_products_config():
    cfg = load_products_config()
    assert cfg.llm  # answerer/judge/generator block present
    assert cfg.llm["answerer"]["default"]
    assert cfg.raw["retrieval"]["top_k"] >= 1
    # Product identity lives in the products.yaml registry, not here.
    assert cfg.products == {}


def _registry(tmp_path, body: str) -> ProductRegistry:
    p = tmp_path / "products.yaml"
    p.write_text(body)
    return ProductRegistry(path=p)


def test_drift_detected(tmp_path, monkeypatch):
    reg = _registry(
        tmp_path,
        textwrap.dedent(
            """
            products:
              foo:
                name: Foo
                description: x
                repo: org-a/foo
            """
        ),
    )
    products = load_products_config()
    # Synthesize a products config that disagrees on repo.
    products.products = {"foo": {"github_repo": "org-b/foo"}}
    issues = detect_registry_drift(registry=reg, products=products)
    assert len(issues) == 1
    assert "foo" in issues[0]


def test_unregistered_product_is_not_drift(tmp_path):
    reg = _registry(tmp_path, "products:\n  foo:\n    name: Foo\n    description: x\n    repo: org-a/foo\n")
    products = load_products_config()
    products.products = {"bar": {"github_repo": "org-b/bar"}}  # not in registry
    assert detect_registry_drift(registry=reg, products=products) == []


def test_config_check_cli_passes():
    from dokimi.config_check import main

    assert main() == 0


def _declare_covered(monkeypatch, key: str = "demo") -> None:
    """Make the registry the CLI reads declare one `covered` product.

    The committed products.yaml declares no product `covered` (its evaluation
    content lives in an external repository, so nothing in this checkout can
    satisfy the promise). These tests still have to prove the CLI threads the
    roots it is given into the coverage check, so they supply a registry that
    does make the promise.
    """
    monkeypatch.setattr(
        "dokimi.coverage._load_products",
        lambda path=None: {key: {"coverage_status": "covered"}},
    )


def test_config_check_cli_accepts_content_roots(tmp_path, monkeypatch):
    """The coverage half is checked against caller-named external roots."""
    from dokimi.config_check import main

    _declare_covered(monkeypatch)
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    questions_root = tmp_path / "questions"
    questions_root.mkdir()

    # Empty roots cannot satisfy a `covered` declaration, so the check must
    # notice rather than pass vacuously.
    assert main(["--tasks-root", str(tasks_root), "--questions-root", str(questions_root)]) == 1


def test_config_check_cli_reports_the_covered_products(tmp_path, capsys, monkeypatch):
    from dokimi.config_check import main

    _declare_covered(monkeypatch)
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    assert main(["--tasks-root", str(tasks_root)]) == 1
    err = capsys.readouterr().err
    assert "work half missing" in err
    # awareness is unresolved (no --questions-root), so it is not reported broken
    assert "awareness half missing" not in err


def test_config_check_cli_passes_with_populated_content_roots(tmp_path, monkeypatch):
    """A `covered` declaration the roots do satisfy exits 0."""
    from dokimi.config_check import main

    _declare_covered(monkeypatch)
    tasks_root = tmp_path / "tasks"
    (tasks_root / "demo-task").mkdir(parents=True)
    (tasks_root / "demo-task" / "task.toml").write_text(
        '[metadata]\ntags = ["demo"]\n', encoding="utf-8"
    )
    questions_root = tmp_path / "questions"
    questions_root.mkdir()
    (questions_root / "demo_golden.json").write_text("[]", encoding="utf-8")

    assert main(["--tasks-root", str(tasks_root), "--questions-root", str(questions_root)]) == 0
