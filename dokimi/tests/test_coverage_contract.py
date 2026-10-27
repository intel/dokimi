# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the questions-and-tasks coverage contract."""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.coverage import (
    STATUS_COVERED,
    STATUS_PLANNED,
    STATUS_QUESTIONS_ONLY,
    ProductCoverage,
    _load_products,
    coverage_issues,
    coverage_matrix_rows,
    resolve_coverage,
)


def _write_task(tasks_dir: Path, name: str, tags: list[str]) -> Path:
    task = tasks_dir / name
    task.mkdir(parents=True)
    tag_list = ", ".join(f'"{t}"' for t in tags)
    (task / "task.toml").write_text(
        f"[metadata]\ntags = [{tag_list}]\n", encoding="utf-8"
    )
    return task


def _registry() -> dict:
    """The committed products.yaml mapping (never empty)."""
    products = _load_products()
    assert products, "products.yaml resolved to zero products"
    return products


def _synthetic_content_root(tmp_path: Path, products: dict) -> tuple[Path, Path]:
    """Stand-in for an external content checkout that covers every product.

    Evaluation content (golden question sets, executable tasks) is no longer
    bundled in this repository, so the registry-wide tests below build a root
    that satisfies both halves for every registered product and check that the
    committed declarations resolve against it.
    """
    tasks_dir = tmp_path / "tasks"
    questions_dir = tmp_path / "questions"
    questions_dir.mkdir(parents=True)
    for key in products:
        _write_task(tasks_dir, f"{key}-task", [key])
        (questions_dir / f"{key}_golden.json").write_text(
            json.dumps({"questions": []}), encoding="utf-8"
        )
    return tasks_dir, questions_dir


def _expected_tasks(key: str, products: dict) -> list[str]:
    """Tasks from _synthetic_content_root that must count as work for `key`.

    One task per product tag, plus any task whose product tag is also a
    ``tag_aliases:`` entry of this product.
    """
    cfg = products.get(key) or {}
    aliases = cfg.get("tag_aliases") or [] if isinstance(cfg, dict) else []
    wanted = {key} | {str(a) for a in aliases}
    return sorted(f"{tag}-task" for tag in wanted if tag in products)


def test_committed_coverage_has_no_issues():
    """The real products.yaml must carry no broken coverage declarations."""
    assert coverage_issues() == []


def test_committed_registry_resolves_against_a_populated_content_root(tmp_path):
    """Every committed declaration must hold against a root that covers it."""
    products = _registry()
    tasks_dir, questions_dir = _synthetic_content_root(tmp_path, products)

    covs = resolve_coverage(
        products, tasks_dir=tasks_dir, questions_dir=questions_dir
    )

    assert len(covs) == len(products)
    for cov in covs:
        # awareness = <questions-root>/<key>_golden.json, work = tagged task dir
        assert cov.has_awareness, f"{cov.key}: awareness half did not resolve"
        assert cov.has_work, f"{cov.key}: work half did not resolve"
        # the work signal is derived from task tags, not hand-listed
        assert cov.tasks == _expected_tasks(cov.key, products)
        assert cov.effective_status == STATUS_COVERED
    assert coverage_issues(covs) == []


def test_registry_tag_aliases_widen_the_work_half(tmp_path):
    """A product's committed `tag_aliases:` really do count as its work.

    oneDAL lists `sklearnex` because sklearnex tasks exercise the oneDAL library
    they accelerate. Here the alias is the *only* tag on the task, so the test
    fails if the alias is dropped from products.yaml or ignored by the resolver.
    """
    products = _registry()
    aliased = {
        key: cfg["tag_aliases"]
        for key, cfg in products.items()
        if isinstance(cfg, dict) and cfg.get("tag_aliases")
    }
    assert aliased, "products.yaml declares no tag_aliases to exercise"
    assert "onedal" in aliased and "sklearnex" in aliased["onedal"]

    tasks_dir = tmp_path / "tasks"
    for key, aliases in aliased.items():
        _write_task(tasks_dir, f"{key}-via-alias", [str(aliases[0])])

    covs = {c.key: c for c in resolve_coverage(products, tasks_dir=tasks_dir)}
    for key, aliases in aliased.items():
        assert covs[key].tasks == [f"{key}-via-alias"], (
            f"{key}: task tagged only {aliases[0]!r} did not count as work"
        )


def test_a_single_string_tag_counts_as_work(tmp_path):
    """`tags = "onednn"` names one tag; it must not be read as the letters o, n, e..."""
    tasks_dir = tmp_path / "tasks"
    task = tasks_dir / "scalar-tag"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\ntags = "OneDNN"\n', encoding="utf-8")

    covs = {c.key: c for c in resolve_coverage({"onednn": {}, "openvino": {}}, tasks_dir=tasks_dir)}

    assert covs["onednn"].tasks == ["scalar-tag"]
    assert covs["openvino"].tasks == []


def test_a_mixed_case_registry_key_matches_lowercased_task_tags(tmp_path):
    """Task tags are compared lowercased, so the product key must be too."""
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "dal-task", ["oneDAL"])

    covs = {c.key: c for c in resolve_coverage({"oneDAL": {}}, tasks_dir=tasks_dir)}

    assert covs["oneDAL"].tasks == ["dal-task"]


def test_roots_are_injectable(tmp_path):
    """Both halves resolve against caller-supplied roots (no in-repo default)."""
    tasks_dir = tmp_path / "tasks"
    questions_dir = tmp_path / "questions"
    questions_dir.mkdir()
    _write_task(tasks_dir, "demo-task", ["demo"])
    (questions_dir / "demo_golden.json").write_text(
        json.dumps({"questions": []}), encoding="utf-8"
    )

    covs = resolve_coverage(
        {"demo": {"coverage_status": STATUS_COVERED}},
        tasks_dir=tasks_dir,
        questions_dir=questions_dir,
    )

    assert len(covs) == 1
    demo = covs[0]
    assert demo.tasks == ["demo-task"]
    assert demo.has_awareness and demo.has_work
    assert demo.effective_status == STATUS_COVERED
    assert coverage_issues(covs) == []


def test_tag_aliases_come_from_the_product_entry(tmp_path):
    """A product's `tag_aliases:` widens the work signal (no aliases in code)."""
    tasks_dir = tmp_path / "tasks"
    questions_dir = tmp_path / "questions"
    questions_dir.mkdir()
    _write_task(tasks_dir, "alias-task", ["alias"])
    (questions_dir / "demo_golden.json").write_text("[]", encoding="utf-8")

    products = {"demo": {"coverage_status": STATUS_COVERED, "tag_aliases": ["alias"]}}
    covs = resolve_coverage(products, tasks_dir=tasks_dir, questions_dir=questions_dir)
    assert covs[0].tasks == ["alias-task"]
    assert coverage_issues(covs) == []

    # Without the alias the same task does not count as work.
    products_no_alias = {"demo": {"coverage_status": STATUS_COVERED}}
    covs = resolve_coverage(
        products_no_alias, tasks_dir=tasks_dir, questions_dir=questions_dir
    )
    assert covs[0].tasks == []
    assert any("work half missing" in i for i in coverage_issues(covs))


def test_questions_root_is_honoured(tmp_path):
    """The awareness half is looked up under questions_dir, not a fixed path."""
    empty = tmp_path / "empty"
    empty.mkdir()
    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "demo_golden.json").write_text("[]", encoding="utf-8")

    products = {"demo": {}}
    assert resolve_coverage(products, questions_dir=empty)[0].questions == []
    resolved = resolve_coverage(products, questions_dir=populated)[0]
    assert resolved.questions == [str(populated / "demo_golden.json")]
    assert resolved.effective_status == STATUS_QUESTIONS_ONLY


def test_declared_relative_questions_path_resolves_against_the_repo(tmp_path):
    """An explicit `questions:` entry ignores questions_dir; missing = broken."""
    products = {"demo": {"questions": ["content/questions/does_not_exist.json"]}}
    cov = resolve_coverage(products, questions_dir=tmp_path)[0]
    assert cov.questions == []
    assert cov.missing_questions == ["content/questions/does_not_exist.json"]
    assert any("not found" in i for i in coverage_issues([cov]))


def test_missing_roots_leave_halves_unresolved_not_empty(tmp_path):
    """Without roots a `covered` declaration is unverified, not contradicted."""
    covs = resolve_coverage({"demo": {"coverage_status": STATUS_COVERED}})
    assert covs[0].questions == [] and covs[0].tasks == []
    assert coverage_issues(covs) == []

    # Naming one root re-enables that half's check only.
    covs = resolve_coverage(
        {"demo": {"coverage_status": STATUS_COVERED}}, tasks_dir=tmp_path
    )
    issues = coverage_issues(covs)
    assert any("work half missing" in i for i in issues)
    assert not any("awareness half missing" in i for i in issues)


def test_cli_accepts_root_flags_and_prints_the_matrix(tmp_path, capsys):
    from dokimi.coverage import _parse_args, _print_matrix

    tasks_dir = tmp_path / "tasks"
    questions_dir = tmp_path / "questions"
    questions_dir.mkdir()
    _write_task(tasks_dir, "onetbb-toy", ["onetbb"])
    (questions_dir / "onetbb_golden.json").write_text("[]", encoding="utf-8")

    args = _parse_args(
        ["--tasks-root", str(tasks_dir), "--questions-root", str(questions_dir)]
    )
    assert args.tasks_root == str(tasks_dir)
    assert args.questions_root == str(questions_dir)

    _print_matrix(tasks_dir=tasks_dir, questions_dir=questions_dir)
    out = capsys.readouterr().out
    assert "| `onetbb` | yes | 1 | covered |" in out
    assert "unresolved" not in out


def test_cli_without_roots_says_the_halves_are_unresolved(capsys):
    from dokimi.coverage import _print_matrix

    _print_matrix()
    out = capsys.readouterr().out
    assert "no --tasks-root given; the work half is unresolved" in out
    assert "no --questions-root given; the awareness half is unresolved" in out


def test_effective_status_transitions():
    aware_and_work = ProductCoverage("x", questions=["q.json"], tasks=["t"])
    assert aware_and_work.effective_status == STATUS_COVERED

    aware_only = ProductCoverage("x", questions=["q.json"], tasks=[])
    assert aware_only.effective_status == STATUS_QUESTIONS_ONLY

    neither = ProductCoverage("x", questions=[], tasks=[])
    assert neither.effective_status == STATUS_PLANNED


def test_covered_declaration_without_task_is_an_issue():
    cov = ProductCoverage(
        "ghost", questions=["content/questions/ghost.json"], tasks=[],
        declared_status=STATUS_COVERED,
    )
    issues = coverage_issues([cov])
    assert any("work half missing" in i for i in issues)


def test_covered_declaration_without_questions_is_an_issue():
    cov = ProductCoverage(
        "ghost", questions=[], tasks=["some-task"],
        declared_status=STATUS_COVERED,
    )
    issues = coverage_issues([cov])
    assert any("awareness half missing" in i for i in issues)


def test_declared_missing_questions_file_is_an_issue():
    cov = ProductCoverage(
        "ghost", questions=[], tasks=["t"],
        declared_status=STATUS_QUESTIONS_ONLY,
        missing_questions=["content/questions/nope.json"],
    )
    issues = coverage_issues([cov])
    assert any("not found" in i for i in issues)


def test_invalid_status_value_is_an_issue():
    cov = ProductCoverage("ghost", declared_status="totally-bogus")
    issues = coverage_issues([cov])
    assert any("invalid coverage_status" in i for i in issues)


def test_questions_only_missing_work_is_not_an_issue():
    """A visible questions-only gap must NOT fail CI (soft enforcement, O1)."""
    cov = ProductCoverage(
        "x", questions=["q.json"], tasks=[], declared_status=STATUS_QUESTIONS_ONLY,
    )
    assert coverage_issues([cov]) == []


def test_matrix_rows_cover_every_product(tmp_path):
    registry = _registry()
    tasks_dir, questions_dir = _synthetic_content_root(tmp_path, registry)
    covs = resolve_coverage(
        registry, tasks_dir=tasks_dir, questions_dir=questions_dir
    )
    rows = coverage_matrix_rows(covs)
    products = {c.key for c in covs}
    assert products, "products.yaml resolved to zero products"
    assert {r["product"] for r in rows} == products
    # every product's row reports the work the content root actually holds
    by_product = {r["product"]: r for r in rows}
    for key in products:
        assert by_product[key]["work"] == len(_expected_tasks(key, registry))
        assert by_product[key]["work"] >= 1
        assert by_product[key]["awareness"] == "yes"


def test_matrix_rows_report_the_injected_roots(tmp_path):
    """coverage_matrix_rows resolves against the roots it is handed."""
    tasks_dir = tmp_path / "tasks"
    questions_dir = tmp_path / "questions"
    questions_dir.mkdir()
    _write_task(tasks_dir, "demo-task", ["demo"])
    (questions_dir / "demo_golden.json").write_text("[]", encoding="utf-8")

    rows = coverage_matrix_rows(
        resolve_coverage(
            {"demo": {}, "other": {}},
            tasks_dir=tasks_dir,
            questions_dir=questions_dir,
        )
    )
    by_product = {r["product"]: r for r in rows}
    assert by_product["demo"]["work"] == 1
    assert by_product["demo"]["awareness"] == "yes"
    assert by_product["other"]["work"] == 0
    assert by_product["other"]["status"] == STATUS_PLANNED
