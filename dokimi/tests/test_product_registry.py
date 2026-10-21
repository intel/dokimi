# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ProductRegistry."""
from pathlib import Path

import pytest
import yaml

from dokimi.registry import LibraryRegistry, ProductRegistry


def _write_registry(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "products.yaml"
    p.write_text(yaml.dump(data))
    return p


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_loads_all_entries(tmp_path):
    reg_path = _write_registry(tmp_path, {
        "products": {
            "onetbb": {
                "name": "oneTBB",
                "description": "Parallel library",
                "repo": "uxlfoundation/oneTBB",
                "context7_id": "uxlfoundation/onetbb",
                "doc_sources": ["context7"],
            },
            "onemkl": {
                "name": "oneMKL",
                "description": "Math library",
                "doc_sources": ["url:https://example.com/mkl"],
            },
        }
    })
    reg = ProductRegistry(path=reg_path)
    assert len(reg.list()) == 2
    assert set(reg.keys()) == {"onetbb", "onemkl"}


def test_legacy_libraries_key_still_loads(tmp_path):
    """Registries written with the pre-rename 'libraries:' key keep working."""
    reg_path = _write_registry(tmp_path, {
        "libraries": {"onetbb": {"name": "oneTBB", "description": "x"}}
    })
    reg = ProductRegistry(path=reg_path)
    assert set(reg.keys()) == {"onetbb"}


def test_legacy_class_alias():
    """LibraryRegistry remains importable as an alias of ProductRegistry."""
    assert LibraryRegistry is ProductRegistry


def test_missing_file_does_not_raise(tmp_path):
    reg = ProductRegistry(path=tmp_path / "nonexistent.yaml")
    assert reg.list() == []


def test_empty_file_does_not_raise(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    reg = ProductRegistry(path=p)
    assert reg.list() == []


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

def test_get_returns_entry(tmp_path):
    reg_path = _write_registry(tmp_path, {
        "products": {
            "onetbb": {
                "name": "oneTBB",
                "description": "desc",
                "repo": "uxlfoundation/oneTBB",
                "context7_id": "uxlfoundation/onetbb",
                "doc_sources": ["context7"],
            }
        }
    })
    reg = ProductRegistry(path=reg_path)
    entry = reg.get("onetbb")
    assert entry.name == "oneTBB"
    assert entry.repo == "uxlfoundation/oneTBB"
    assert entry.context7_id == "uxlfoundation/onetbb"
    assert entry.doc_sources == ["context7"]


def test_get_case_insensitive(tmp_path):
    reg_path = _write_registry(tmp_path, {
        "products": {"onetbb": {"name": "oneTBB", "description": "x"}}
    })
    reg = ProductRegistry(path=reg_path)
    assert reg.get("ONETBB").key == "onetbb"
    assert reg.get("OneTBB").key == "onetbb"


def test_get_missing_raises_key_error(tmp_path):
    reg_path = _write_registry(tmp_path, {
        "products": {"onetbb": {"name": "oneTBB", "description": "x"}}
    })
    reg = ProductRegistry(path=reg_path)
    with pytest.raises(KeyError, match="notexist"):
        reg.get("notexist")


def test_get_key_error_lists_available(tmp_path):
    reg_path = _write_registry(tmp_path, {
        "products": {
            "onetbb": {"name": "oneTBB", "description": "x"},
            "onemkl": {"name": "oneMKL", "description": "y"},
        }
    })
    reg = ProductRegistry(path=reg_path)
    with pytest.raises(KeyError) as exc_info:
        reg.get("nope")
    assert "onetbb" in str(exc_info.value)
    assert "onemkl" in str(exc_info.value)


# ---------------------------------------------------------------------------
# contains / defaults
# ---------------------------------------------------------------------------

def test_contains(tmp_path):
    reg_path = _write_registry(tmp_path, {
        "products": {"onetbb": {"name": "oneTBB", "description": "x"}}
    })
    reg = ProductRegistry(path=reg_path)
    assert "onetbb" in reg
    assert "missing" not in reg


def test_default_doc_sources(tmp_path):
    """Entry without explicit doc_sources defaults to ['context7']."""
    reg_path = _write_registry(tmp_path, {
        "products": {"onetbb": {"name": "oneTBB", "description": "x"}}
    })
    reg = ProductRegistry(path=reg_path)
    assert reg.get("onetbb").doc_sources == ["context7"]


def test_multiple_doc_sources(tmp_path):
    reg_path = _write_registry(tmp_path, {
        "products": {
            "onemkl": {
                "name": "oneMKL",
                "description": "x",
                "doc_sources": ["context7", "url:https://example.com/mkl"],
            }
        }
    })
    reg = ProductRegistry(path=reg_path)
    entry = reg.get("onemkl")
    assert len(entry.doc_sources) == 2
    assert "url:https://example.com/mkl" in entry.doc_sources


# ---------------------------------------------------------------------------
# Default registry (bundled products.yaml)
# ---------------------------------------------------------------------------

def test_default_registry_loads():
    reg = ProductRegistry()
    keys = reg.keys()
    assert "onetbb" in keys
    assert len(keys) >= 4


def test_default_registry_onetbb_has_repo():
    import dokimi.registry

    bundled = Path(dokimi.registry.__file__).with_name("products.yaml")
    raw = yaml.safe_load(bundled.read_text(encoding="utf-8"))["products"]["onetbb"]
    entry = ProductRegistry().get("onetbb")
    # The entry carries the bundled file's identity fields, each of which onetbb declares.
    assert (entry.repo, entry.context7_id, entry.doc_sources) == (
        raw["repo"], raw["context7_id"], raw["doc_sources"])
    assert entry.repo == "uxlfoundation/oneTBB"
