# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Product registry — load and query known products from products.yaml."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Default registry bundled with the package
_DEFAULT_REGISTRY = Path(__file__).parent / "products.yaml"


@dataclass
class ProductEntry:
    key: str                          # registry key, e.g. "onetbb"
    name: str                         # human name, e.g. "oneTBB"
    description: str
    repo: Optional[str] = None
    context7_id: Optional[str] = None
    doc_sources: List[str] = field(default_factory=lambda: ["context7"])


class ProductRegistry:
    """Load and query the products.yaml registry.

    Accepts the canonical ``products:`` top-level key, plus the legacy
    ``libraries:`` key for older registry files.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _DEFAULT_REGISTRY
        self._entries: Dict[str, ProductEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning(f"Registry not found: {self._path}")
            return
        data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or not ("products" in data or "libraries" in data):
            logger.warning(f"Registry {self._path} has no 'products' key — skipping.")
            return
        products = data.get("products", data.get("libraries"))
        if not isinstance(products, dict):
            logger.warning("Registry 'products' must be a mapping — skipping.")
            return
        for raw_key, cfg in products.items():
            if not isinstance(raw_key, str):
                logger.warning(f"Registry key '{raw_key}' is not a string — skipping.")
                continue
            key = raw_key.lower()
            if key in self._entries:
                logger.warning(
                    f"Registry key collision: '{raw_key}' normalizes to '{key}' "
                    f"which is already registered. Skipping duplicate."
                )
                continue
            if not isinstance(cfg, dict):
                logger.warning(f"Registry entry '{raw_key}' is not a mapping — skipping.")
                continue
            doc_sources = cfg.get("doc_sources", ["context7"])
            if not isinstance(doc_sources, list) or not doc_sources:
                logger.warning(f"Registry entry '{raw_key}' has empty doc_sources — defaulting to context7.")
                doc_sources = ["context7"]
            self._entries[key] = ProductEntry(
                key=key,
                name=cfg.get("name", raw_key),
                description=str(cfg.get("description") or "").strip(),
                repo=cfg.get("repo"),
                context7_id=cfg.get("context7_id"),
                doc_sources=doc_sources,
            )
        logger.info(f"Loaded {len(self._entries)} products from {self._path}")

    def get(self, key: str) -> ProductEntry:
        """Return entry by key (case-insensitive). Raises KeyError if not found."""
        entry = self._entries.get(key.lower())
        if entry is None:
            available = ", ".join(sorted(self._entries))
            raise KeyError(f"Product '{key}' not found in registry. Available: {available}")
        return entry

    def list(self) -> List[ProductEntry]:
        return list(self._entries.values())

    def keys(self) -> List[str]:
        return sorted(self._entries.keys())

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._entries


# Backward-compatible aliases (pre-rename names, when products were "libraries")
LibraryEntry = ProductEntry
LibraryRegistry = ProductRegistry
