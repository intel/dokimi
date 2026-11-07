# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The M3 moves: `catalog list|show --kind`, `skill check`, and `eval tasks generate --from replay` flags."""

from __future__ import annotations

import pytest

from dokimi import cli_routes
from dokimi.cli import build_parser
from dokimi.commands.catalog import cmd_catalog_list, cmd_catalog_show
from dokimi.utils import InputError


def test_catalog_list_requires_a_kind(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["catalog", "list"])
    assert exc.value.code == 2
    assert "--kind" in capsys.readouterr().err


def test_catalog_list_and_show_take_the_kind_and_dispatch(capsys):
    args = build_parser().parse_args(["catalog", "list", "--kind", "product"])
    assert args.catalog_cmd == "list" and args.kind == "product"
    assert args.func is cmd_catalog_list
    args = build_parser().parse_args(["catalog", "show", "--kind", "product", "onetbb"])
    assert args.kind == "product" and args.name == "onetbb"
    assert args.func is cmd_catalog_show


def test_catalog_kind_refuses_a_value_it_does_not_list(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["catalog", "show", "--kind", "skill", "x"])
    assert exc.value.code == 2


def test_skill_check_takes_one_skill_and_a_root():
    args = build_parser().parse_args(["skill", "check", "demo", "--root", "catalog-root"])
    assert args.skill_cmd == "check"
    assert args.skill == "demo"
    assert args.root == "catalog-root"


def test_skill_check_reports_only_the_named_skill(tmp_path, capsys):
    for name in ("uxl-alpha", "uxl-beta"):
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use for {name} things.\n---\n# {name}\n", encoding="utf-8"
        )
    (tmp_path / "skills.yaml").write_text(
        'schema_version: "1"\nskills:\n'
        '  - name: "uxl-alpha"\n    status: "incubating"\n    catalog_path: "skills/uxl-alpha"\n'
        '  - name: "uxl-beta"\n    status: "incubating"\n    catalog_path: "skills/uxl-beta"\n',
        encoding="utf-8",
    )
    args = build_parser().parse_args(["skill", "check", "uxl-alpha", "--root", str(tmp_path)])
    args.func(args)
    out = capsys.readouterr().out
    assert "uxl-alpha" in out
    assert "uxl-beta" not in out


def test_skill_check_names_a_skill_the_root_does_not_hold(tmp_path, capsys):
    args = build_parser().parse_args(["skill", "check", "no-such-skill", "--root", str(tmp_path)])
    with pytest.raises(InputError, match="no skill named 'no-such-skill'"):
        args.func(args)


def test_replay_needs_a_threads_file(tmp_path):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "replay", "--out", str(tmp_path / "t")]
    )
    with pytest.raises(InputError, match="--from replay needs --threads"):
        args.func(args)


def test_replay_refuses_a_code_index(tmp_path):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "replay", "--threads", "t.json",
         "--index", "i.scip", "--out", str(tmp_path / "t")]
    )
    with pytest.raises(InputError, match="--index is for --from graph"):
        args.func(args)


def test_graph_refuses_a_threads_file(tmp_path):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "graph", "--threads", "t.json",
         "--out", str(tmp_path / "t")]
    )
    with pytest.raises(InputError, match="--threads is for --from replay"):
        args.func(args)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (["library", "list", "--domain", "x"], ["catalog", "list", "--kind", "product", "--domain", "x"]),
        (["library", "show", "onetbb"], ["catalog", "show", "--kind", "product", "onetbb"]),
        (["product", "list"], ["catalog", "list", "--kind", "product"]),
        (["subjects", "list", "--subjects-root", "r"], ["catalog", "list", "--kind", "subject", "--subjects-root", "r"]),
        (["subjects", "show", "s.toml"], ["catalog", "show", "--kind", "subject", "s.toml"]),
        (
            ["repo", "tasks", "replay", "src", "--threads", "t.json"],
            ["eval", "tasks", "generate", "--from", "replay", "src", "--threads", "t.json"],
        ),
    ],
)
def test_the_real_m3_tombstones_print_the_new_argv(old, new):
    assert cli_routes.moved_argv(old) == new


def test_subject_show_maps_the_name_to_the_subject_path(tmp_path, monkeypatch):
    from dokimi.commands import subjects

    seen = {}
    monkeypatch.setattr(subjects, "cmd_subjects_show", lambda args: seen.update(subject=args.subject))
    args = build_parser().parse_args(["catalog", "show", "--kind", "subject", str(tmp_path / "s.toml")])
    args.func(args)
    assert seen["subject"] == str(tmp_path / "s.toml")


def test_replay_refuses_dry_run(tmp_path):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "replay", "--threads", "t.json",
         "--dry-run", "--out", str(tmp_path / "t")]
    )
    with pytest.raises(InputError, match="--dry-run is for --from graph"):
        args.func(args)
