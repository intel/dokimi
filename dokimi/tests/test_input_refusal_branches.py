# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Each input-refusal branch the converted commands raise (InputError) exits 2 with `Error: <reason>`.

Covers the branches the converted modules add that the per-command tests do not reach.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from tests.cli_refusal import refusal

_REPO_SUBJECT = """
[subject]
id = "r"
kind = "repo"
ref = "local:repo"

[suite]
products = ["a"]
questions = ["q.json"]
""".strip()

_AWARENESS_SUBJECT = """
baseline = "baseline"

[subject]
id = "s"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["a"]
questions = ["q.json"]
""".strip()


def _personas_file(tmp_path: Path, body) -> str:
    path = tmp_path / "personas.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return str(path)


def _subject_file(tmp_path: Path, text: str) -> str:
    path = tmp_path / "subject.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    ("argv_for", "expect"),
    [
        pytest.param(lambda d: ["eval", "personas", "validate", "--file", _personas_file(d, [])],
         "Invalid persona file. Expected a JSON object", id="personas-validate-not-object"),
        pytest.param(lambda d: ["eval", "personas", "validate", "--file", _personas_file(d, {"product": "p"})],
         "Invalid persona file. Missing keys", id="personas-validate-missing-keys"),
        pytest.param(lambda d: ["eval", "personas", "validate", "--file",
                    _personas_file(d, {"product": "p", "personas": "x"})],
         "`personas` must be a list", id="personas-validate-personas-not-list"),
        pytest.param(lambda d: ["eval", "personas", "validate", "--file",
                    _personas_file(d, {"product": "p", "personas": [7]})],
         "Each persona must be an object", id="personas-validate-entry-not-object"),
        pytest.param(lambda d: ["eval", "personas", "validate", "--file",
                    _personas_file(d, {"product": "p", "personas": [{"id": "x"}]})],
         "missing fields", id="personas-validate-entry-missing-fields"),
        pytest.param(lambda d: ["dashboard", "generate", "--results-dir", str(d / "empty"), "--output-dir", str(d)],
         "No evaluation results found. Run benchmark first.", id="dashboard-generate-no-results"),
        pytest.param(lambda d: ["catalog", "show", "--kind", "product", "no-such-product"],
         "not found in registry", id="library-show-unknown-product"),
        pytest.param(lambda d: ["evaluate", "--product", "p"],
         "either --repo or --description must be provided.", id="evaluate-no-repo-or-description"),
        pytest.param(lambda d: ["catalog", "show", "--kind", "subject", str(d / "missing.toml")],
         "cannot load subject", id="subjects-show-missing-descriptor"),
        pytest.param(lambda d: ["subjects", "run", str(d / "missing.toml")],
         "cannot load subject", id="subjects-run-missing-descriptor"),
        pytest.param(lambda d: ["subjects", "run", _subject_file(d, _REPO_SUBJECT)],
         "repo subjects are not run by `subjects run` yet", id="subjects-run-repo-kind"),
        pytest.param(lambda d: ["subjects", "run", _subject_file(d, _AWARENESS_SUBJECT),
                    "--novelty-artifact", str(d / "missing-novelty.json")],
         "cannot load novelty artifact", id="subjects-run-missing-novelty-artifact"),
    ],
)
def test_an_input_refusal_branch_exits_2_with_its_reason(tmp_path, monkeypatch, argv_for, expect):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(argv_for(tmp_path))
    with refusal() as out:
        args.func(args)
    assert out.code == 2, out.err
    assert out.err.startswith("Error: ") and expect in out.err, out.err
