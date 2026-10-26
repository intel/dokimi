# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Intent registry — load and query the problem/intent space from intents.yaml.

``products.yaml`` answers "what products exist"; ``intents.yaml`` answers
"what user problems exist and which products serve them". The domain →
products mapping lives only in intents.yaml (single source of truth);
:func:`IntentRegistry.validate` checks that every referenced product key
exists in the product registry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Default registry bundled with the package
_DEFAULT_INTENTS = Path(__file__).parent / "intents.yaml"


@dataclass
class IntentDomain:
    key: str                          # registry key, e.g. "performance-optimization"
    name: str                         # human name, e.g. "Performance Optimization"
    description: str
    products: List[str] = field(default_factory=list)
    example_intents: List[str] = field(default_factory=list)


def _domain_from_config(raw_key: str, cfg: dict) -> Optional[IntentDomain]:
    """One ``domains:`` entry as an :class:`IntentDomain`, or ``None`` when its products are malformed.

    A blank ``products:`` or ``name:`` (YAML null) is an unset field, as an absent
    one is: a domain with no products yet is listed, and a blank name reads as the key.
    """
    products = cfg.get("products")
    if products is None:
        products = []
    if not isinstance(products, list):
        logger.warning(f"Intent domain '{raw_key}' products must be a list — skipping.")
        return None
    examples = cfg.get("example_intents", [])
    if not isinstance(examples, list):
        examples = []
    name = cfg.get("name")
    return IntentDomain(
        key=raw_key.lower(),
        name=raw_key if name is None else str(name),
        description=str(cfg.get("description") or "").strip(),
        products=[str(p).lower() for p in products],
        example_intents=[str(e) for e in examples],
    )


class IntentRegistry:
    """Load and query the intents.yaml domain/intent registry."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _DEFAULT_INTENTS
        self._domains: Dict[str, IntentDomain] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning(f"Intent registry not found: {self._path}")
            return
        data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or "domains" not in data:
            logger.warning(f"Intent registry {self._path} has no 'domains' key — skipping.")
            return
        domains = data["domains"]
        if not isinstance(domains, dict):
            logger.warning("Intent registry 'domains' must be a mapping — skipping.")
            return
        for raw_key, cfg in domains.items():
            if not isinstance(raw_key, str) or not isinstance(cfg, dict):
                logger.warning(f"Intent domain '{raw_key}' is malformed — skipping.")
                continue
            domain = _domain_from_config(raw_key, cfg)
            if domain is not None:
                self._domains[domain.key] = domain
        logger.info(f"Loaded {len(self._domains)} intent domains from {self._path}")

    def get(self, key: str) -> IntentDomain:
        """Return domain by key (case-insensitive). Raises KeyError if not found."""
        domain = self._domains.get(key.lower())
        if domain is None:
            available = ", ".join(sorted(self._domains))
            raise KeyError(f"Intent domain '{key}' not found. Available: {available}")
        return domain

    def list(self) -> List[IntentDomain]:
        return list(self._domains.values())

    def keys(self) -> List[str]:
        return sorted(self._domains.keys())

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._domains

    def products_for(self, domain_key: str) -> List[str]:
        """Product keys serving a domain."""
        return list(self.get(domain_key).products)

    def domains_for(self, product_key: str) -> List[str]:
        """Reverse lookup: domain keys that include a product."""
        product_key = product_key.lower()
        return sorted(d.key for d in self._domains.values() if product_key in d.products)

    def validate(self, product_registry=None) -> List[str]:
        """Return issues for product keys not present in the product registry."""
        from dokimi.registry import ProductRegistry

        product_registry = product_registry or ProductRegistry()
        issues: List[str] = []
        for domain in self._domains.values():
            for product in domain.products:
                if product not in product_registry:
                    issues.append(
                        f"intents.yaml domain '{domain.key}' references unknown "
                        f"product '{product}' (not in products.yaml)"
                    )
        return issues
