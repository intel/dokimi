# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`questions generate`: an invalid runtime config exits 1 with a message, not a traceback."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser


def _generate(tmp_path, monkeypatch, config_text):
    config = tmp_path / "products.yaml"
    config.write_text(config_text, encoding="utf-8")
    monkeypatch.setattr("dokimi.config.runtime_config_path", lambda: config)
    personas = tmp_path / "personas.json"
    personas.write_text('{"personas": []}', encoding="utf-8")
    args = build_parser().parse_args([
        "questions", "generate", "--product", "p", "--personas", str(personas),
        "--output", str(tmp_path / "q.json"),
    ])
    args.func(args)


@pytest.mark.parametrize("text, message", [
    ("products: [unclosed\n", "invalid YAML"),
    ("- a\n- b\n", "expected a mapping"),
])
def test_an_invalid_runtime_config_exits_1_with_a_message(tmp_path, monkeypatch, capsys, text, message):
    with pytest.raises(SystemExit) as exc:
        _generate(tmp_path, monkeypatch, text)
    assert exc.value.code == 1
    assert message in capsys.readouterr().err
