# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`catalog list|show --kind product` and `catalog intents`: an unreadable --registry or --intents file exits 2 with a message."""

from __future__ import annotations

import pytest

from tests.cli_refusal import refusal

from dokimi.cli import build_parser

_PRODUCTS = "products:\n  demo:\n    name: Demo\n    description: d\n"


def _run(argv):
    args = build_parser().parse_args(argv)
    with refusal() as out:
        args.func(args)
    assert out.code == 2
    return out.err


def _bad_files(tmp_path):
    binary = tmp_path / "bin.yaml"
    binary.write_bytes(bytes([0xFF, 0xFE, 0x00]))
    broken = tmp_path / "broken.yaml"
    broken.write_text("products: [unclosed\n", encoding="utf-8")
    return {"binary": binary, "broken": broken}


@pytest.mark.parametrize("kind", ["binary", "broken"])
@pytest.mark.parametrize("command", ["list", "show demo"])
def test_a_registry_that_cannot_be_parsed_is_named(tmp_path, kind, command):
    bad = _bad_files(tmp_path)[kind]
    verb, *rest = command.split()
    err = _run(["catalog", verb, "--kind", "product", *rest, "--registry", str(bad)])
    assert f"Error: cannot read --registry {bad}" in err


@pytest.mark.parametrize("kind", ["binary", "broken"])
def test_an_intents_file_that_cannot_be_parsed_is_named(tmp_path, kind):
    bad = _bad_files(tmp_path)[kind]
    err = _run(["catalog", "intents", "--intents", str(bad)])
    assert f"Error: cannot read --intents {bad}" in err


def test_a_bad_intents_file_is_named_by_list_with_a_domain(tmp_path):
    products = tmp_path / "products.yaml"
    products.write_text(_PRODUCTS, encoding="utf-8")
    bad = _bad_files(tmp_path)["broken"]
    err = _run(["catalog", "list", "--kind", "product", "--registry", str(products), "--intents", str(bad),
                "--domain", "x"])
    assert f"Error: cannot read --intents {bad}" in err


def test_a_missing_registry_is_not_an_empty_registry(tmp_path):
    missing = tmp_path / "nope.yaml"
    err = _run(["catalog", "list", "--kind", "product", "--registry", str(missing)])
    assert f"Error: --registry {missing}: not a file" in err


def test_a_directory_as_intents_is_rejected(tmp_path):
    err = _run(["catalog", "intents", "--intents", str(tmp_path)])
    assert f"Error: --intents {tmp_path}: not a file" in err
