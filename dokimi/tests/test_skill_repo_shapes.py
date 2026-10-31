# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill-repo shapes met in public catalogs: `catalog scan` and `novelty routing` over each.

Every shape is invented (tests/skill_repo_shapes.py). The sweep asserts no command crashes;
the expectation table asserts the status each shape should get. A shape whose bug is being
fixed in another PR is an expected failure, strict, so the mark comes off when the fix lands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient
from tests.skill_repo_shapes import PROBE_QUERIES, SHAPES, build_shape, probe_artifact

FIXED_BY_CATALOG = "fixed by fix/rescan-catalog"
FIXED_BY_ROUTING = "fixed by fix/rescan-routing"


@dataclass(frozen=True)
class Expect:
    scan: frozenset[int]  # `catalog scan --format json` exit codes
    strict: frozenset[int]  # the same with --strict
    routing: int  # `novelty routing --catalog` exit code
    field: tuple[str, ...] | None  # competitor names when routing exits 0; None = not checked


def _ok(*field: str) -> Expect:
    return Expect(frozenset({0}), frozenset({0}), 0, tuple(sorted(field)))


_TWO = ("door-check", "widget-polish")

EXPECT: dict[str, Expect] = {
    "byte_identical_mirror": _ok(*_TWO),
    "drifted_mirror": Expect(frozenset({0}), frozenset({0}), 0, None),
    "same_name_distinct_dirs": Expect(frozenset({0}), frozenset({0}), 0, None),
    "disable_model_invocation": _ok(*_TWO),
    "template_dir": _ok(*_TWO),
    "vendored_dir": _ok(*_TWO),
    "zero_skill": Expect(frozenset({1, 2}), frozenset({1, 2}), 2, None),
    "plugins_nesting": _ok("door-check", "gizmo-lint", "widget-polish"),
    "description_too_long": Expect(frozenset({0}), frozenset({1}), 0, ("door-check",)),
    "name_not_dir": Expect(frozenset({0}), frozenset({1}), 0, ("door-check",)),
    "title_case_name": Expect(frozenset({0}), frozenset({1}), 0, ("door-check",)),
    "links_escape": _ok(*_TWO),
    "no_usable_headings": _ok(*_TWO),
}

# Shape -> (the dimensions a bug still breaks on intel/main, the fix that will change them).
# Dimensions: scan, strict, routing, field. Only those dimensions are expected failures.
OPEN_BUGS = {
    "drifted_mirror": (("routing",), FIXED_BY_CATALOG),
    "same_name_distinct_dirs": (("routing",), FIXED_BY_CATALOG),
    "zero_skill": (("scan", "strict"), FIXED_BY_CATALOG),
    "description_too_long": (("field",), FIXED_BY_ROUTING),
    "name_not_dir": (("field",), FIXED_BY_ROUTING),
    "title_case_name": (("field",), FIXED_BY_ROUTING),
}


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    """Run one dokimi command in-process; (exit code, stdout, stderr). Crashes propagate."""
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit as exc:
        code = 0 if exc.code is None else exc.code
    else:
        code = 0
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def fake_selector(monkeypatch):
    """The model arm of routing with a stub model that never answers: no network, no cost."""
    import dokimi.commands.novelty as novelty_cmd

    def client(model, provider, role, args):
        return LLMClient(model=model, provider=provider, role=role,
                         transport=lambda prompt, system: ("", None))

    monkeypatch.setattr(novelty_cmd, "_client", client)


@pytest.fixture
def shape_paths(tmp_path):
    """(catalog root, probe artifact dir, queries file) for one test."""
    queries = tmp_path / "queries.json"
    queries.write_text(json.dumps(PROBE_QUERIES), encoding="utf-8")
    return tmp_path / "catalog", probe_artifact(tmp_path / "artifact"), queries


def _scan(root: Path, capsys, *, strict: bool = False) -> tuple[int, str]:
    argv = ["catalog", "scan", "--root", str(root), "--format", "json"]
    if strict:
        argv.append("--strict")
    code, out, _ = _run(argv, capsys)
    return code, out


def _routing(root: Path, artifact: Path, queries: Path, tmp_path: Path, capsys,
             *extra: str) -> tuple[int, dict | None, str]:
    out_file = tmp_path / "routing.json"
    argv = ["novelty", "routing", "--artifact", f"skill:{artifact}", "--catalog", str(root),
            "--queries", str(queries), "--out", str(out_file), *extra]
    code, _, err = _run(argv, capsys)
    payload = json.loads(out_file.read_text(encoding="utf-8")) if code == 0 else None
    return code, payload, err


def _field(payload: dict) -> tuple[str, ...]:
    return tuple(sorted(payload["field"]["competitors"]))


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_shape_runs_without_a_crash(shape, shape_paths, tmp_path, capsys, fake_selector):
    # The sweep: no uncaught exception from either command, lexical and model routing alike.
    root, artifact, queries = shape_paths
    build_shape(shape, root)
    for strict in (False, True):
        code, _ = _scan(root, capsys, strict=strict)
        assert code in (0, 1, 2)
    code, _, _ = _routing(root, artifact, queries, tmp_path, capsys)
    assert code in (0, 2)
    code, _, _ = _routing(root, artifact, queries, tmp_path, capsys, "--model", "stub-selector")
    assert code in (0, 2)


def _observe(shape: str, shape_paths, tmp_path: Path, capsys) -> dict:
    """What the two commands did on one shape: exit codes and the routing field, or None."""
    root, artifact, queries = shape_paths
    build_shape(shape, root)
    scan_code, _ = _scan(root, capsys)
    strict_code, _ = _scan(root, capsys, strict=True)
    code, payload, err = _routing(root, artifact, queries, tmp_path, capsys)
    return {"scan": scan_code, "strict": strict_code, "routing": code,
            "field": _field(payload) if payload is not None else None, "err": err}


def _holds(shape: str, observed: dict) -> dict[str, bool]:
    """Per dimension, whether the observation meets the expectation for *shape*."""
    expect = EXPECT[shape]
    return {
        "scan": observed["scan"] in expect.scan,
        "strict": observed["strict"] in expect.strict,
        "routing": observed["routing"] == expect.routing,
        "field": expect.field is None or observed["field"] == expect.field,
    }


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_shape_gets_its_expected_status(shape, shape_paths, tmp_path, capsys, fake_selector):
    # Every dimension is strict except the one a bug in another PR still breaks (OPEN_BUGS).
    observed = _observe(shape, shape_paths, tmp_path, capsys)
    holds = _holds(shape, observed)
    open_dims = OPEN_BUGS[shape][0] if shape in OPEN_BUGS else ()
    failed = [dim for dim, ok in holds.items() if not ok and dim not in open_dims]
    assert not failed, f"{shape}: {failed} off expectation; observed {observed}"


@pytest.mark.parametrize("shape", [
    pytest.param(shape, id=shape, marks=pytest.mark.xfail(strict=True, reason=reason))
    for shape, (_, reason) in sorted(OPEN_BUGS.items())
])
def test_open_bug_dimension_meets_its_expectation(shape, shape_paths, tmp_path, capsys,
                                                 fake_selector):
    # Strict xfail on the open dimensions only: when that fix lands, this XPASSes and the mark comes off.
    observed = _observe(shape, shape_paths, tmp_path, capsys)
    holds = _holds(shape, observed)
    for dim in OPEN_BUGS[shape][0]:
        assert holds[dim], f"{shape}.{dim}: observed {observed}"


def test_a_mirror_is_listed_by_scan_but_counted_once_in_the_field(shape_paths, tmp_path, capsys,
                                                                 fake_selector):
    root, artifact, queries = shape_paths
    build_shape("byte_identical_mirror", root)
    _, out = _scan(root, capsys)
    rows = json.loads(out)["skills"]
    assert len(rows) == 3
    assert sum(r["duplicate_of"] is not None for r in rows) == 1
    code, payload, _ = _routing(root, artifact, queries, tmp_path, capsys)
    assert code == 0 and payload["n_candidates"] == 3  # the artifact and two competitors


def test_the_disabled_skill_is_left_out_of_the_routing_field_and_said_so(shape_paths, tmp_path,
                                                                        capsys, fake_selector):
    root, artifact, queries = shape_paths
    build_shape("disable_model_invocation", root)
    code, payload, err = _routing(root, artifact, queries, tmp_path, capsys)
    assert code == 0 and "gizmo-lint" not in _field(payload)
    assert "gizmo-lint" in err and "disable-model-invocation" in err


def test_all_vs_all_names_the_flag_when_queries_name_a_disabled_skill(shape_paths, tmp_path,
                                                                    capsys):
    root, _, _ = shape_paths
    build_shape("disable_model_invocation", root)
    by_skill = tmp_path / "by_skill.json"
    by_skill.write_text(json.dumps({"gizmo-lint": ["lint a gizmo config"],
                                    "door-check": ["check a door latch"]}), encoding="utf-8")
    argv = ["novelty", "routing", "--all-vs-all", "--catalog", str(root),
            "--queries-by-skill", str(by_skill), "--out", str(tmp_path / "avsa.json")]
    code, _, err = _run(argv, capsys)
    assert code == 2 and "--include-disabled" in err
    code, _, _ = _run([*argv, "--include-disabled"], capsys)
    assert code == 0


def test_headings_as_queries_on_a_skill_with_no_headings_is_a_usage_refusal(shape_paths, tmp_path,
                                                                           capsys):
    root, _, _ = shape_paths
    build_shape("no_usable_headings", root)
    artifact = root / "skills" / "door-check"  # its only body line is prose, no heading
    out_file = tmp_path / "routing.json"
    argv = ["novelty", "routing", "--artifact", f"skill:{artifact}", "--catalog", str(root),
            "--queries-from-headings", "--out", str(out_file)]
    code, _, err = _run(argv, capsys)
    assert code == 2 and "no usable queries" in err and "headings" in err
    # The refusal still leaves a record, so a batch run shows why it has no rate.
    record = json.loads(out_file.read_text(encoding="utf-8"))
    assert record["status"] == "no_queries" and record["queries_self_derived"] is True
