# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`library list|show|intents`: an unreadable --registry or --intents file exits 1 with a message."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser

_PRODUCTS = "products:\n  demo:\n    name: Demo\n    description: d\n"


def _run(argv, capsys):
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    return capsys.readouterr().err


def _bad_files(tmp_path):
    binary = tmp_path / "bin.yaml"
    binary.write_bytes(bytes([0xFF, 0xFE, 0x00]))
    broken = tmp_path / "broken.yaml"
    broken.write_text("products: [unclosed\n", encoding="utf-8")
    return {"binary": binary, "broken": broken}


@pytest.mark.parametrize("kind", ["binary", "broken"])
@pytest.mark.parametrize("command", ["list", "show demo"])
def test_a_registry_that_cannot_be_parsed_is_named(tmp_path, capsys, kind, command):
    bad = _bad_files(tmp_path)[kind]
    err = _run(["library", *command.split(), "--registry", str(bad)], capsys)
    assert f"Error: cannot read --registry {bad}" in err


@pytest.mark.parametrize("kind", ["binary", "broken"])
def test_an_intents_file_that_cannot_be_parsed_is_named(tmp_path, capsys, kind):
    bad = _bad_files(tmp_path)[kind]
    err = _run(["library", "intents", "--intents", str(bad)], capsys)
    assert f"Error: cannot read --intents {bad}" in err


def test_a_bad_intents_file_is_named_by_list_with_a_domain(tmp_path, capsys):
    products = tmp_path / "products.yaml"
    products.write_text(_PRODUCTS, encoding="utf-8")
    bad = _bad_files(tmp_path)["broken"]
    err = _run(["library", "list", "--registry", str(products), "--intents", str(bad),
                "--domain", "x"], capsys)
    assert f"Error: cannot read --intents {bad}" in err


def test_a_missing_registry_is_not_an_empty_registry(tmp_path, capsys):
    missing = tmp_path / "nope.yaml"
    err = _run(["library", "list", "--registry", str(missing)], capsys)
    assert f"Error: --registry {missing}: not a file" in err


def test_a_directory_as_intents_is_rejected(tmp_path, capsys):
    err = _run(["library", "intents", "--intents", str(tmp_path)], capsys)
    assert f"Error: --intents {tmp_path}: not a file" in err
