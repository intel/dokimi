# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The free granularity hints of `catalog check` (#424 tier 1): candidates only, never a verdict."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external import granularity as g
from dokimi.skills.loader import Skill

PORT_TRIGGER = (
    "Use when the user asks to migrate NumPy code to dpnp, or when a call raises "
    "NotImplementedError after the import was swapped."
)
GOTCHAS = "## Gotchas\n\n" + " ".join(
    f"Check the fallback wrapper before the device call number {n}." for n in range(12)
) + "\n\n```python\nimport dpnp\nx = dpnp.arange(10)\nprint(x.sum())\n```\n"


def _skill(name: str, description: str, body: str) -> Skill:
    return Skill(name=name, description=description, body=body, path=Path(f"skills/{name}/SKILL.md"))


def _pool(words: str, times: int) -> str:
    """A paragraph of ``words`` repeated: its vocabulary is all the section has."""
    return " ".join(words.split() * times)


def _overlapping_pair() -> list[Skill]:
    alpha = _skill(
        "alpha-port",
        "Port NumPy code to dpnp on Intel GPUs. " + PORT_TRIGGER,
        "# Alpha\n\n## Overview\n\nCovers the port.\n\n" + GOTCHAS,
    )
    beta = _skill(
        "beta-port",
        "Move NumPy programs to dpnp on Intel GPUs. " + PORT_TRIGGER,
        "# Beta\n\n## Overview\n\nAnother write-up of the port.\n\n" + GOTCHAS,
    )
    return [alpha, beta]


def _control() -> Skill:
    return _skill(
        "gamma-control",
        "Format Kubernetes manifests for review. Use when the user edits YAML deployment files.",
        "# Gamma\n\nCheck the YAML indentation and the image tags.\n",
    )


def _multi_topic() -> Skill:
    """Three H2 topics with no shared vocabulary, no cross reference, and a body past the long-body cap."""
    kafka = _pool("kafka consumer partition offset rebalance broker topic lag retention", 70)
    pg = _pool("postgres vacuum index planner tablespace checkpoint replication wal", 70)
    helm = _pool("helm chart kubernetes ingress certificate namespace rollout probe", 70)
    body = (
        "# Operations\n\n## Kafka\n\n" + kafka + "\n\n## Postgres\n\n" + pg
        + "\n\n## Helm\n\n" + helm + "\n"
    )
    return _skill(
        "multi-topic-ops",
        "Operate Kafka, Postgres and Helm. Use when tuning Kafka consumer lag. "
        "Use when vacuuming Postgres tables. Use when rolling out Helm charts on Kubernetes.",
        body,
    )


def _single_topic_long() -> Skill:
    """Long, but one topic: every section uses the same words, so they link into one cluster."""
    words = "kafka consumer partition offset rebalance broker topic lag retention"
    body = "# Kafka\n\n" + "\n\n".join(f"## Step {n}\n\n{_pool(words, 40)}" for n in range(3)) + "\n"
    return _skill(
        "single-topic-kafka",
        "Tune Kafka consumers. Use when deploying a Kafka consumer group.",
        body,
    )


def test_trigger_clauses_split_and_normalise_when_clauses():
    clauses = g.trigger_clauses(
        "Port NumPy. Use when the user asks to migrate NumPy code, or when a call raises "
        "NotImplementedError after the import was swapped."
    )
    assert clauses == (
        "asks migrate numpy code",
        "call raises notimplementederror after import was swapped",
    )


def test_an_overlapping_pair_is_a_merge_candidate_with_its_shared_signals():
    result = g.hints(_overlapping_pair(), root="ROOT")

    assert len(result.merge) == 1
    pair = result.merge[0]
    assert (pair.a, pair.b) == ("alpha-port", "beta-port")
    assert pair.description_cosine >= g.DESCRIPTION_COSINE_STRONG
    assert "description-overlap" in pair.signals
    assert "trigger-clause" in pair.signals
    assert "section-duplicate" in pair.signals
    assert "code-duplicate" in pair.signals
    assert pair.duplicated_code_blocks == 1


def test_a_non_overlapping_control_is_in_no_pair_and_not_a_split_candidate():
    result = g.hints([*_overlapping_pair(), _control()], root="ROOT")

    names = {p.a for p in result.merge} | {p.b for p in result.merge}
    assert "gamma-control" not in names
    assert "gamma-control" not in {s.skill for s in result.split}


def test_a_long_multi_topic_skill_is_a_split_candidate_with_three_signals():
    result = g.hints([_multi_topic()], root="ROOT")

    assert len(result.split) == 1
    split = result.split[0]
    assert split.skill == "multi-topic-ops"
    assert split.body_chars >= g.LONG_BODY_CHARS
    assert set(split.signals) == {"long-body", "disjoint-when", "section-clusters"}
    assert split.disjoint_when_clauses >= g.DISJOINT_WHEN_MIN
    assert len(split.section_clusters) == 3


def test_a_long_single_topic_skill_is_not_a_split_candidate():
    result = g.hints([_single_topic_long()], root="ROOT")

    # long-body alone is one signal; it needs SPLIT_MIN_SIGNALS to be a candidate
    assert result.split == ()


def test_the_json_carries_hints_and_no_verdict_and_names_the_planned_paid_command():
    result = g.hints(_overlapping_pair(), root="/data/catalog")
    doc = result.to_dict()

    assert doc["hints_only"] is True
    assert "verdict" not in doc
    assert doc["thresholds"]["DESCRIPTION_COSINE_STRONG"] == g.DESCRIPTION_COSINE_STRONG
    nxt = doc["next"]
    assert nxt["status"] == "planned" and nxt["paid"] is True
    assert nxt["argv"][:4] == ["dokimi", "catalog", "granularity", "--root"]
    assert nxt["argv"][4] == "/data/catalog"
    assert nxt["est_usd"] > 0


def test_no_candidates_means_no_paid_hint_and_a_plain_text_line():
    result = g.hints([_control()], root="ROOT")

    assert result.paid is None
    assert result.to_dict()["next"] is None
    text = "\n".join(g.format_text(result))
    assert "hints only" in text
    assert "nothing to run" in text


def test_a_single_skill_or_an_empty_catalog_has_no_pairs():
    assert g.hints([], root="ROOT").merge == ()
    assert g.hints([_control()], root="ROOT").merge == ()


def test_fenced_code_is_not_counted_as_prose_words():
    fenced = "```\n" + "\n".join(["word " * 20] * 3) + "\n```\n"
    doc = g._describe(_skill("code-only", "Use when x.", fenced))

    assert doc.prose == ()
    assert doc.code == (tuple(["word"] * 60),)


def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8"
    )


@pytest.fixture
def overlapping_catalog(tmp_path):
    for skill in _overlapping_pair() + [_control()]:
        _write_skill(tmp_path, skill.name, skill.description, skill.body)
    return tmp_path


def test_catalog_check_prints_the_granularity_hints_and_keeps_exit_zero(overlapping_catalog, capsys):
    args = build_parser().parse_args(["catalog", "check", "--root", str(overlapping_catalog), "--kind", "skills"])
    args.func(args)  # no SystemExit: a candidate is a hint, not a failure

    out = capsys.readouterr().out
    assert "# granularity:" in out
    assert "merge\talpha-port + beta-port" in out
    assert "next (planned, [paid]): dokimi catalog granularity" in out


def test_catalog_check_json_has_the_granularity_block_and_no_verdict(overlapping_catalog, capsys):
    args = build_parser().parse_args(
        ["catalog", "check", "--root", str(overlapping_catalog), "--kind", "skills", "--format", "json"]
    )
    args.func(args)

    report = json.loads(capsys.readouterr().out)
    block = report["granularity"]
    assert block["hints_only"] is True and "verdict" not in block
    assert [(p["a"], p["b"]) for p in block["merge"]] == [("alpha-port", "beta-port")]
    assert block["next"]["status"] == "planned"


def test_catalog_check_exits_1_on_a_load_error_regardless_of_the_hints(overlapping_catalog, capsys):
    broken = overlapping_catalog / "skills" / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("---\nname: [unclosed\n---\n", encoding="utf-8")
    args = build_parser().parse_args(["catalog", "check", "--root", str(overlapping_catalog), "--kind", "skills"])

    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert "# granularity:" in capsys.readouterr().out
