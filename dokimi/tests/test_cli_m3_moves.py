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


@pytest.mark.parametrize("flags", [["--max-turns", "7"], ["--references", "0"], ["--derives", "3"], ["--seed", "7"]])
def test_replay_refuses_a_graph_only_flag_even_at_its_default(tmp_path, flags):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "replay", "--threads", "t.json",
         "--out", str(tmp_path / "t"), *flags]
    )
    with pytest.raises(InputError, match=f"{flags[0]} is for --from graph"):
        args.func(args)


@pytest.mark.parametrize("flags", [["--window", "3"], ["--prompt-file", "p.txt"], ["--manifest", "m.yaml"],
                                   ["--manifest-file", "m.yaml"]])
def test_graph_refuses_a_replay_only_flag(tmp_path, flags):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "graph", "--index", "i.scip",
         "--out", str(tmp_path / "t"), *flags]
    )
    with pytest.raises(InputError, match=f"{flags[0]} is for --from replay"):
        args.func(args)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["catalog", "list", "--kind", "product", "--subjects-root", "/nope"], "--subjects-root is for --kind subject"),
        (["catalog", "list", "--kind", "subject", "--domain", "x"], "--domain is for --kind product"),
        (["catalog", "show", "--kind", "subject", "s.toml", "--registry", "r.yaml"], "--registry is for --kind product"),
        (["catalog", "show", "--kind", "subject", "s.toml", "--intents", "i.yaml"], "--intents is for --kind product"),
    ],
)
def test_catalog_refuses_the_flags_of_the_other_kind(argv, message):
    args = build_parser().parse_args(argv)
    with pytest.raises(InputError, match=message):
        args.func(args)


def _skill_with_a_name_mismatch(tmp_path):
    """Two skills; uxl-alpha's frontmatter names another skill, a spec error that loads (exit 0 unless --strict)."""
    for name in ("uxl-alpha", "uxl-beta"):
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        declared = "uxl-gamma" if name == "uxl-alpha" else name
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {declared}\ndescription: Use for {name} things.\n---\n# {name}\n", encoding="utf-8"
        )
    (tmp_path / "skills.yaml").write_text(
        'schema_version: "1"\nskills:\n'
        '  - name: "uxl-alpha"\n    status: "incubating"\n    catalog_path: "skills/uxl-alpha"\n'
        '  - name: "uxl-beta"\n    status: "incubating"\n    catalog_path: "skills/uxl-beta"\n',
        encoding="utf-8",
    )


def test_skill_check_strict_makes_a_spec_error_exit_1(tmp_path):
    _skill_with_a_name_mismatch(tmp_path)
    args = build_parser().parse_args(["skill", "check", "uxl-alpha", "--root", str(tmp_path)])
    args.func(args)  # a spec error without --strict does not fail
    args = build_parser().parse_args(["skill", "check", "uxl-alpha", "--root", str(tmp_path), "--strict"])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert excinfo.value.code == 1


def test_skill_check_json_is_the_named_skills_row_only(tmp_path, capsys):
    import json

    _skill_with_a_name_mismatch(tmp_path)
    args = build_parser().parse_args(
        ["skill", "check", "uxl-alpha", "--root", str(tmp_path), "--format", "json"]
    )
    args.func(args)
    rows = json.loads(capsys.readouterr().out)["skills"]
    assert [(row["name"], row["state"]) for row in rows] == [("uxl-alpha", "error")]
