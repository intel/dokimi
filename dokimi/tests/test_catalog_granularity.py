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
    body = "# Kafka\n\n" + "\n\n".join(f"## Step {n}\n\n{_pool(words, 60)}" for n in range(3)) + "\n"
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
    skill = _single_topic_long()
    assert len(skill.body) >= g.LONG_BODY_CHARS
    result = g.hints([skill], root="ROOT")

    # long-body alone is one signal (its sections form one cluster); it needs SPLIT_MIN_SIGNALS
    assert result.split == ()


def test_the_json_carries_hints_and_no_verdict_and_names_the_planned_paid_command():
    result = g.hints(_overlapping_pair(), root="/data/catalog")
    doc = result.to_dict()

    assert doc["hints_only"] is True
    assert "verdict" not in doc
    assert doc["thresholds"]["description_cosine_strong"] == g.DESCRIPTION_COSINE_STRONG
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


# ---------------------------------------------------------------- self-review round 1


def test_the_text_names_the_hidden_rows_when_only_one_kind_passes_the_limit():
    desc = "Port NumPy code to dpnp on Intel GPUs. Use when the user asks to migrate NumPy code to dpnp."
    result = g.hints([_skill(f"s{i}", desc, "# x\n") for i in range(8)], root="ROOT")

    assert (len(result.merge), len(result.split)) == (28, 0)
    lines = g.format_text(result)
    assert sum(line.startswith("  merge") for line in lines) == g.DISPLAY_LIMIT
    assert "  ... 8 more candidate row(s) (--format json lists all)" in lines


def test_a_weak_description_cosine_counts_only_with_a_shared_trigger_vocabulary():
    weak = (g.DESCRIPTION_COSINE_MIN + g.DESCRIPTION_COSINE_STRONG) / 2
    assert g._signals(weak, 0.0, g._Acc()) == ()
    assert g._signals(weak, g.TRIGGER_VOCAB_JACCARD_MIN, g._Acc()) == ("description-and-trigger-overlap",)


def test_one_duplicated_code_block_is_enough_for_a_merge_candidate():
    block = "```bash\nvllm serve model --device xpu\ncurl localhost:8000/v1/models\nvllm bench serve --model model\n```\n"
    a = _skill("serve-a", "Serve models. Use when starting a vLLM server.", "# A\n\n" + block)
    b = _skill("bench-b", "Benchmark throughput. Use when measuring tokens per second.", "# B\n\n" + block)

    [pair] = g.hints([a, b], root="ROOT").merge
    assert pair.signals == ("code-duplicate",)


def test_a_heading_inside_a_fence_is_not_a_section_and_an_unclosed_fence_is_code():
    words = " ".join(["word"] * g.SECTION_MIN_WORDS)
    body = f"## Real\n\n{words}\n\n```\n## not a heading\n{words}\nend\n```\n\n```\none\ntwo\nthree\n"
    doc = g._describe(_skill("fenced", "Use when x.", body))

    assert [s.heading for s in doc.sections] == ["Real"]
    assert len(doc.code) == 2
    assert doc.code[1] == ("one", "two", "three")


def test_a_cross_reference_links_two_sections_with_no_shared_words():
    skill = _multi_topic()
    linked = skill.body.replace("## Postgres\n\n", "## Postgres\n\nSee [Kafka](#kafka) first. ")
    result = g.hints([_skill(skill.name, skill.description, linked)], root="ROOT")

    assert [len(s.section_clusters) for s in result.split] == [2]


def test_disjoint_when_clauses_are_counted_greedily_without_shared_words():
    assert g._disjoint_when(("tune kafka lag", "kafka offsets reset", "vacuum postgres tables")) == 2


def test_the_shingle_join_finds_exactly_the_brute_force_pairs():
    import random

    rnd = random.Random(7)
    for minimum in (g.BODY_JACCARD_MIN, g.SECTION_JACCARD_MIN, g.CODE_JACCARD_MIN, 0.5):
        common = frozenset((f"c{k}",) for k in range(5))  # boilerplate every item shares
        items = []
        for k in range(40):
            own = frozenset((f"w{rnd.randrange(60)}",) for _ in range(rnd.randrange(0, 25)))
            items.append((k % 13, own | (common if k % 3 else frozenset())))
        items += [(90, frozenset(((f"e{k}",) for k in range(20)))), (91, frozenset(((f"e{k}",) for k in range(3))))]
        brute = [
            (k, m, g._jaccard(items[k][1], items[m][1]))
            for k in range(len(items)) for m in range(k + 1, len(items))
            if items[k][0] != items[m][0] and g._jaccard(items[k][1], items[m][1]) >= minimum
        ]
        assert sorted(g._similar(items, minimum)) == sorted(brute), minimum


def _write_at(root: Path, rel: str, name: str, description: str, body: str, extra: str = "") -> None:
    skill_dir = root / rel
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}", encoding="utf-8"
    )


def _check(root: Path, capsys, *more: str) -> tuple[str, int]:
    args = build_parser().parse_args(["catalog", "check", "--root", str(root), "--kind", "skills", *more])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    return capsys.readouterr().out, code


def test_a_mirrored_copy_is_not_its_own_merge_candidate(tmp_path, capsys):
    for rel in ("skills/foo", ".claude/skills/foo"):
        _write_at(tmp_path, rel, "foo", _control().description, _control().body)

    out, _ = _check(tmp_path, capsys)
    assert "identical to skills/foo" in out
    assert "# granularity: 0 merge and 0 split candidate(s) among 1 skills" in out


def test_two_skills_with_one_directory_name_are_labelled_by_their_path(tmp_path, capsys):
    desc = "Review a pull request diff for bugs. Use when the user asks to review a pull request."
    for rel in ("plug-a/skills/review", "plug-b/skills/review"):
        _write_at(tmp_path, rel, "review", desc, f"# {rel}\n")

    out, _ = _check(tmp_path, capsys)
    assert "merge\tplug-a/skills/review + plug-b/skills/review\t" in out


def test_a_hidden_or_invalid_skill_is_left_out_of_the_hints(tmp_path, capsys):
    desc = "Port NumPy code to dpnp on Intel GPUs. Use when the user asks to migrate NumPy code to dpnp."
    _write_at(tmp_path, "skills/good-port", "good-port", desc, "# A\n")
    _write_at(tmp_path, "skills/hidden-port", "hidden-port", desc, "# B\n", extra="disable-model-invocation: true\n")
    _write_at(tmp_path, "skills/bad-port", "Bad_Port", desc, "# C\n")

    out, _ = _check(tmp_path, capsys)
    assert "# granularity: 0 merge and 0 split candidate(s) among 1 skills" in out


def test_an_empty_root_prints_no_granularity_section(tmp_path, capsys):
    out, code = _check(tmp_path, capsys)

    assert code == 1
    assert "# granularity" not in out



def test_only_the_text_before_the_first_sentence_after_a_trigger_is_a_clause():
    clauses = g.trigger_clauses(
        "Use when tuning Kafka consumer lag. Supports PostgreSQL table maintenance. "
        "Generates Helm deployment manifests."
    )
    assert clauses == ("tuning kafka consumer lag",)
