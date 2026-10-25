# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`subjects run`: a descriptor that cannot be run exits 1 with a message, before writing anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.cli import build_parser


def _run(tmp_path: Path, monkeypatch, *, subject_id: str, products: str, questions: str):
    descriptor = tmp_path / "subject.toml"
    descriptor.write_text(
        f"""
[subject]
id = "{subject_id}"
kind = "doc-source"
ref = "local:docs"

[suite]
products = {products}
questions = {questions}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["subjects", "run", str(descriptor)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1


@pytest.mark.parametrize(
    ("subject_id", "products", "questions", "message"),
    [
        ("..", '["oneTBB"]', '["q.json"]', "subject.id must not resolve"),
        ("s", '["one/TBB", "one-TBB"]', '["q.json"]', "collide after output directory sanitization"),
        ("s", '["a", "b"]', '["q1.json", "q2.json", "q3.json"]', "suite.questions must contain either"),
    ],
)
def test_an_unrunnable_descriptor_exits_1_with_its_reason(
    tmp_path, monkeypatch, capsys, subject_id, products, questions, message
):
    _run(tmp_path, monkeypatch, subject_id=subject_id, products=products, questions=questions)

    assert message in capsys.readouterr().err.splitlines()[0]
    assert not (tmp_path / "results").exists()
