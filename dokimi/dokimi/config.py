# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Products config loading + cross-registry drift detection.

The repo has two config files: ``products.yaml`` (the canonical
product-identity registry, loaded by
:class:`dokimi.registry.ProductRegistry`) and
``config/products.yaml`` (LLM/runtime config for the eval track). The
committed runtime config declares no products, but a caller may add a
``products:`` block holding per-product eval-track overrides — and any
identity restated there is a drift risk, so this module treats
``products.yaml`` as canonical and detects when the two disagree on the GitHub
repo for a shared product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PRODUCTS_PATH = Path(__file__).resolve().parent / "config" / "products.yaml"
# A ``config/products.yaml`` in the working directory overrides the bundled one.
LOCAL_PRODUCTS_PATH = Path("config") / "products.yaml"


def runtime_config_path() -> Path:
    """The eval-track config a command reads: the local override, else the bundled file."""
    return LOCAL_PRODUCTS_PATH if LOCAL_PRODUCTS_PATH.exists() else DEFAULT_PRODUCTS_PATH


@dataclass
class ProductsConfig:
    """Validated view of ``config/products.yaml``."""

    products: dict = field(default_factory=dict)
    llm: dict = field(default_factory=dict)
    context7: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def load_products_config(path: Path | None = None) -> ProductsConfig:
    """Load and lightly validate products.yaml."""
    path = Path(path or DEFAULT_PRODUCTS_PATH)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"products config root must be a mapping: {path}")
    products = data.get("products", {})
    if not isinstance(products, dict):
        raise ValueError(f"products config 'products' must be a mapping: {path}")
    for name, cfg in products.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"product '{name}' must be a mapping in {path}")
    return ProductsConfig(
        products=products,
        llm=data.get("llm", {}),
        context7=data.get("context7", {}),
        raw=data,
    )


def detect_registry_drift(
    registry=None, products: ProductsConfig | None = None
) -> list[str]:
    """Return human-readable drift issues between the two registries.

    Only products present in *both* registries are compared, on the canonical
    identity field (GitHub repo, case-insensitive). ``products.yaml`` wins.
    """
    from dokimi.registry import ProductRegistry

    registry = registry or ProductRegistry()
    products = products or load_products_config()

    issues: list[str] = []
    for name, cfg in products.products.items():
        if name.lower() not in registry:
            continue  # eval-only product; not a drift, just unregistered
        entry = registry.get(name)
        repo_p = (cfg.get("github_repo") or "").strip()
        repo_l = (entry.repo or "").strip()
        if repo_p and repo_l and repo_p.lower() != repo_l.lower():
            issues.append(
                f"{name}: github_repo drift — config/products.yaml={repo_p!r} vs "
                f"products.yaml={repo_l!r} (the products.yaml registry is canonical)"
            )
    return issues
