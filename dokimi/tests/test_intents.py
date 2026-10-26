# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for IntentRegistry (intents.yaml problem/intent space)."""
from pathlib import Path

import pytest
import yaml

from dokimi.intents import IntentRegistry
from dokimi.registry import ProductRegistry


def _write_intents(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "intents.yaml"
    p.write_text(yaml.dump(data))
    return p


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_loads_domains(tmp_path):
    path = _write_intents(tmp_path, {
        "domains": {
            "debugging": {
                "name": "Debugging",
                "description": "Find bugs",
                "products": ["inspector", "gdb_intel"],
                "example_intents": ["Find the data race"],
            },
            "math": {
                "name": "Math",
                "description": "Numerics",
                "products": ["onemkl"],
            },
        }
    })
    reg = IntentRegistry(path=path)
    assert set(reg.keys()) == {"debugging", "math"}
    domain = reg.get("debugging")
    assert domain.name == "Debugging"
    assert domain.products == ["inspector", "gdb_intel"]
    assert domain.example_intents == ["Find the data race"]


def test_blank_products_and_name_are_unset_not_malformed(tmp_path, capsys):
    from dokimi.cli import build_parser

    path = tmp_path / "intents.yaml"
    path.write_text(
        "domains:\n"
        "  planned-domain:\n"
        "    name:\n"
        "    description: not served yet\n"
        "    products:\n"
        "  bad-domain:\n"
        "    products: onemkl\n",
        encoding="utf-8",
    )

    args = build_parser().parse_args(["library", "intents", "--intents", str(path)])
    args.func(args)

    out = capsys.readouterr().out
    assert "planned-domain — planned-domain" in out
    assert "products: —" in out
    assert "bad-domain" not in out
    assert "1 intent domains registered." in out


def test_missing_file_does_not_raise(tmp_path):
    reg = IntentRegistry(path=tmp_path / "nonexistent.yaml")
    assert reg.list() == []


def test_empty_file_does_not_raise(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    reg = IntentRegistry(path=p)
    assert reg.list() == []


def test_get_missing_raises_key_error(tmp_path):
    path = _write_intents(tmp_path, {
        "domains": {"math": {"name": "Math", "description": "x", "products": []}}
    })
    reg = IntentRegistry(path=path)
    with pytest.raises(KeyError, match="nope"):
        reg.get("nope")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def test_products_for_and_reverse_lookup(tmp_path):
    path = _write_intents(tmp_path, {
        "domains": {
            "debugging": {"name": "D", "description": "x", "products": ["inspector", "gdb_intel"]},
            "gpu": {"name": "G", "description": "x", "products": ["gdb_intel", "dpnp"]},
        }
    })
    reg = IntentRegistry(path=path)
    assert reg.products_for("debugging") == ["inspector", "gdb_intel"]
    assert reg.domains_for("gdb_intel") == ["debugging", "gpu"]
    assert reg.domains_for("dpnp") == ["gpu"]
    assert reg.domains_for("unknown") == []


# ---------------------------------------------------------------------------
# Validation against the product registry
# ---------------------------------------------------------------------------

def test_validate_flags_unknown_products(tmp_path):
    products_path = tmp_path / "products.yaml"
    products_path.write_text(yaml.dump({
        "products": {"onetbb": {"name": "oneTBB", "description": "x"}}
    }))
    intents_path = _write_intents(tmp_path, {
        "domains": {
            "parallel": {"name": "P", "description": "x", "products": ["onetbb", "ghost"]},
        }
    })
    reg = IntentRegistry(path=intents_path)
    issues = reg.validate(ProductRegistry(path=products_path))
    assert len(issues) == 1
    assert "ghost" in issues[0]
    assert "parallel" in issues[0]


# ---------------------------------------------------------------------------
# Bundled intents.yaml
# ---------------------------------------------------------------------------

def test_default_intents_load():
    reg = IntentRegistry()
    assert len(reg.keys()) >= 5
    assert "performance-optimization" in reg


def test_default_intents_reference_only_registered_products():
    assert IntentRegistry().validate() == []


# ---------------------------------------------------------------------------
# `library intents --intents <path>`
# ---------------------------------------------------------------------------

def test_library_intents_reads_a_custom_registry(tmp_path, capsys):
    from dokimi.cli import build_parser

    path = _write_intents(tmp_path, {
        "domains": {
            "synthetic-domain": {
                "name": "Synthetic Domain",
                "description": "d",
                "products": ["demo"],
                "example_intents": ["Do the thing"],
            }
        }
    })

    args = build_parser().parse_args(["library", "intents", "--intents", str(path)])
    args.func(args)

    out = capsys.readouterr().out
    assert "synthetic-domain — Synthetic Domain" in out
    assert "products: demo" in out
    assert "1 intent domains registered." in out


def test_library_list_filters_by_domain_from_a_custom_intents_file(tmp_path, capsys):
    from dokimi.cli import build_parser

    products_path = tmp_path / "products.yaml"
    products_path.write_text(
        "products:\n"
        "  demo:\n    name: Demo\n    description: d\n"
        "  other:\n    name: Other\n    description: o\n",
        encoding="utf-8",
    )
    intents_path = _write_intents(tmp_path, {
        "domains": {
            "synthetic-domain": {
                "name": "Synthetic Domain",
                "description": "d",
                "products": ["demo"],
            }
        }
    })

    args = build_parser().parse_args([
        "library", "list",
        "--registry", str(products_path),
        "--intents", str(intents_path),
        "--domain", "synthetic-domain",
    ])
    args.func(args)

    out = capsys.readouterr().out
    assert "demo" in out
    assert "other" not in out
    assert "1 products in domain 'synthetic-domain'." in out
