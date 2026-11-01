# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A repeat that fails after a good one: the good pick is scored, the failure is said so."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _cat(root, names_descriptions):
    for name, description in names_descriptions.items():
        skill_dir = root / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n", encoding="utf-8")


def _write_queries(tmp_path, by_skill):
    path = tmp_path / "queries_by_skill.json"
    path.write_text(json.dumps(by_skill), encoding="utf-8")
    return path


def test_a_repeat_that_fails_after_a_good_one_keeps_the_good_pick_in_model_scoring(
        tmp_path, capsys, monkeypatch):
    import dokimi.commands.novelty as novelty_cmd

    answered = []

    def transport(prompt: str, system: str | None):
        if answered:
            raise RuntimeError("rate limited")
        answered.append(1)
        return '{"pick": 1}', None

    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: LLMClient(
        model=model, provider=provider, role=role, transport=transport))
    root = tmp_path / "catalog"
    _cat(root, {"perf": "profile a slow binary with perf", "db": "tune a slow database query"})
    queries = _write_queries(tmp_path, {"perf": ["profile it"]})
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out), "--model", "m",
                  "--repeats", "2")
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert "1 of 2 model call" in str(exc.value.code)
    assert "out of model-arm scoring" in str(exc.value.code)
    assert "repeat-noise rates" in str(exc.value.code)
    model = json.loads(out.read_text(encoding="utf-8"))["result"]["model"]
    # The good repeat is a scored call; the failed one is unscored. The query has a failed
    # repeat, so it is in no repeat-noise rate.
    assert model["n_scored"]["perf"] == 1 and model["n_unscored"] == {"perf": 1}
    assert model["own_rate"]["perf"] is not None
    assert model["noise"]["n_queries_compared"] == 0
    assert model["noise"]["selection_rate_by_repeat"] == [None, None]
