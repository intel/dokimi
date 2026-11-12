# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The M3 moves: `catalog list|show --kind`, `skill check`, and `eval tasks generate --from replay|prs` flags."""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi import cli_routes
from dokimi.cli import build_parser, parse_args
from dokimi.cli_routes import AmbiguousRetiredFlag, moved_argv
from dokimi.commands.catalog import cmd_catalog_list, cmd_catalog_show
from dokimi.utils import InputError
from tests.cli_refusal import refusal


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
    # the granularity footer after the rows may name a pair with uxl-beta; the row for it must not appear
    assert not any(line.startswith("uxl-beta\t") for line in out.splitlines())


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


def test_prs_needs_a_pull_request_file(tmp_path):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "prs", "--out", str(tmp_path / "t")]
    )
    with pytest.raises(InputError, match="--from prs needs --prs FILE"):
        args.func(args)


@pytest.mark.parametrize("source", [["--from", "graph", "--index", "i.scip"], ["--from", "replay", "--threads", "t.json"]])
def test_graph_and_replay_refuse_a_pull_request_file(tmp_path, source):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), *source, "--prs", "p.json", "--out", str(tmp_path / "t")]
    )
    with pytest.raises(InputError, match="--prs is for --from prs"):
        args.func(args)


def test_replay_refuses_max_lines(tmp_path):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "replay", "--threads", "t.json",
         "--max-lines", "400", "--out", str(tmp_path / "t")]
    )
    with pytest.raises(InputError, match="--max-lines is for --from prs"):
        args.func(args)


def test_prs_refuses_a_threads_file(tmp_path):
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "prs", "--prs", "p.json", "--threads", "t.json",
         "--out", str(tmp_path / "t")]
    )
    with pytest.raises(InputError, match="--threads is for --from replay"):
        args.func(args)


@pytest.mark.parametrize("flags", [["--from", "replay", "--threads"], ["--from", "prs", "--prs"]])
@pytest.mark.parametrize(("source", "message"), [
    ("", "Error: invalid repository source: ''"),
    ("missing", "Error: local source is not a directory: {missing}"),
    ("https://example.invalid/r", "Error: a remote source must be pinned to a revision: https://example.invalid/r@<sha>"),
])
def test_replay_and_prs_refuse_a_bad_source_as_a_usage_error(tmp_path, flags, source, message):
    """A source refused as typed exits 2, as with --from graph; 1 is for one that cannot be opened."""
    prs = tmp_path / "prs.json"
    prs.write_text('[{"number": 7, "reviewThreads": {"nodes": []}, "mergeCommit": null}]', encoding="utf-8")
    missing = tmp_path / "missing"
    spec = str(missing) if source == "missing" else source
    args = build_parser().parse_args(["eval", "tasks", "generate", spec, *flags, str(prs), "--out", str(tmp_path / "t")])
    with refusal() as outcome:
        args.func(args)
    assert outcome.code == 2
    assert outcome.err.strip() == message.format(missing=missing)


@pytest.mark.parametrize("flags", [["--from", "replay", "--threads"], ["--from", "prs", "--prs"]])
def test_replay_and_prs_report_a_source_the_filesystem_refuses_as_a_failure(tmp_path, capsys, monkeypatch, flags):
    """A directory the user cannot traverse is an unusable source (1) with the `✗` line, not a traceback."""
    prs = tmp_path / "prs.json"
    prs.write_text('[{"number": 7, "reviewThreads": {"nodes": []}, "mergeCommit": null}]', encoding="utf-8")

    def refuse(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "is_dir", refuse)
    args = build_parser().parse_args(["eval", "tasks", "generate", str(tmp_path / "locked"), *flags, str(prs),
                                      "--out", str(tmp_path / "t")])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert capsys.readouterr().err.startswith("✗ ")


def test_prs_takes_the_manifest_flags_and_builds_with_the_default_size_limit(tmp_path, monkeypatch):
    from dokimi.commands import repo_run

    seen = {}
    monkeypatch.setattr(repo_run, "cmd_repo_tasks_authoring", lambda args: seen.update(vars(args)))
    args = build_parser().parse_args(
        ["eval", "tasks", "generate", str(tmp_path), "--from", "prs", "--prs", "p.json", "--manifest", "m.yaml",
         "--manifest-file", "local.yaml", "--prompt-file", "p.txt", "--out", str(tmp_path / "t")]
    )
    args.func(args)
    assert (seen["prs"], seen["manifest"], seen["manifest_file"], seen["prompt_file"]) == (
        "p.json", "m.yaml", "local.yaml", "p.txt")
    assert seen["max_lines"] == 400 and seen["window"] is None


def test_repo_tasks_authoring_names_the_prs_argv_and_runs_nothing(capsys):
    flags = ["--prs", "p.json", "--out", "t", "--manifest-file", "m.yaml", "--max-lines", "50", "--prompt-file",
             "i.txt", "--force", "--cache-dir", "c"]
    with pytest.raises(SystemExit) as exc:
        parse_args(["repo", "tasks", "authoring", "src", *flags])
    assert exc.value.code == 2
    # `--out` names a directory on `eval tasks generate`, so the printed argv carries `--out-dir` (F1b)
    new = ["eval", "tasks", "generate", "--from", "prs", "src", *["--out-dir" if f == "--out" else f for f in flags]]
    assert capsys.readouterr().err == "moved: dokimi " + " ".join(new) + "\n"
    args = build_parser().parse_args(new)  # the printed argv parses as it is
    assert (args.from_kind, args.source, args.prs, args.out, args.manifest_file, args.max_lines, args.prompt_file,
            args.force, args.cache_dir) == ("prs", "src", "p.json", "t", "m.yaml", 50, "i.txt", True, "c")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (["library", "list", "--domain", "x"], ["catalog", "list", "--kind", "product", "--domain", "x"]),
        (["library", "show", "onetbb"], ["catalog", "show", "--kind", "product", "onetbb"]),
        (["library"], ["catalog"]),
        (["product", "list"], ["catalog", "list", "--kind", "product"]),
        (["product", "show", "onetbb"], ["catalog", "show", "--kind", "product", "onetbb"]),
        (["product", "--help"], ["catalog", "--help"]),
        (["subjects", "list", "--subjects-root", "r"], ["catalog", "list", "--kind", "subject", "--subjects-root", "r"]),
        (["subjects", "show", "s.toml"], ["catalog", "show", "--kind", "subject", "s.toml"]),
        (
            ["repo", "tasks", "replay", "src", "--threads", "t.json"],
            ["eval", "tasks", "generate", "--from", "replay", "src", "--threads", "t.json"],
        ),
        (["run", "--spec", "s.yaml", "--strict"], ["docs", "check", "--spec", "s.yaml", "--strict"]),
        (["compare", "--base", "a.json", "--candidate", "b.json"], ["docs", "compare", "--base", "a.json", "--candidate", "b.json"]),
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


def test_skill_check_header_says_it_reports_one_of_the_declared(tmp_path, capsys):
    _skill_with_a_name_mismatch(tmp_path)
    args = build_parser().parse_args(["skill", "check", "uxl-alpha", "--root", str(tmp_path)])
    args.func(args)
    header = capsys.readouterr().out.splitlines()[0]
    assert header.startswith(f"# skills (1 of 2 declared under {tmp_path}; ")


def test_skill_check_help_says_where_the_name_comes_from(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["skill", "check", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "fails to load may be named differently" in text
    assert "its skills.yaml name" in text
    assert "its directory if the manifest" in text
    assert "SKILL.md frontmatter name" in text


def test_the_docs_group_answers_its_old_names_with_exit_2(capsys):
    from dokimi.cli import parse_args

    with pytest.raises(SystemExit) as exc:
        parse_args(["run", "--spec", "s.yaml"])
    assert exc.value.code == 2
    assert "moved: dokimi docs check --spec s.yaml" in capsys.readouterr().err


def test_docs_check_and_compare_keep_the_old_flags():
    check = build_parser().parse_args(["docs", "check", "--spec", "s.yaml", "--strict"])
    assert (check.spec, check.strict, check.out_json) == ("s.yaml", True, "baselines/current.json")
    compare = build_parser().parse_args(["docs", "compare", "--base", "a", "--candidate", "b"])
    assert (compare.base, compare.candidate, compare.spec) == ("a", "b", None)


def test_skill_routing_takes_one_skill_and_an_against_root(tmp_path):
    args = build_parser().parse_args(
        ["skill", "routing", "skills/s", "--against", str(tmp_path), "--reps", "2"]
    )
    assert args.artifact == "skill:skills/s"  # a bare path is a skill: the field reads it as a ref
    assert args.catalog == str(tmp_path) and args.repeats == 2
    assert args.all_vs_all is False and callable(args.func)


def test_skill_routing_keeps_a_text_ref_as_it_is():
    args = build_parser().parse_args(["skill", "routing", "text:notes.md"])
    assert args.artifact == "text:notes.md"


def test_catalog_routing_takes_a_root_and_runs_all_vs_all(tmp_path):
    args = build_parser().parse_args(
        ["catalog", "routing", str(tmp_path), "--reps", "3", "--queries-by-skill", "q.json"]
    )
    assert args.catalog == str(tmp_path) and args.repeats == 3
    assert args.all_vs_all is True and args.artifact is None and args.queries_by_skill == "q.json"
    assert callable(args.func)


def test_catalog_routing_refuses_an_artifact(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["catalog", "routing", "skills/", "--artifact", "skill:x"])
    assert exc.value.code == 2


def test_novelty_routing_is_a_tombstone_for_one_skill():
    assert moved_argv(
        ["novelty", "routing", "--artifact", "skill:a", "--catalog", "skills/", "--repeats", "3", "--model", "m"]
    ) == ["skill", "routing", "skill:a", "--against", "skills/", "--reps", "3", "--model", "m"]


def test_novelty_routing_all_vs_all_is_a_tombstone_for_catalog_routing():
    assert moved_argv(
        ["novelty", "routing", "--all-vs-all", "--catalog", "skills/", "--queries-by-skill", "q.json",
         "--repeats=2", "--out", "o.json"]
    ) == ["catalog", "routing", "skills/", "--queries-by-skill", "q.json", "--reps=2", "--out", "o.json"]


def test_novelty_routing_tombstone_keeps_the_last_of_a_repeated_option():
    # argparse keeps the last value of a store option, so the printed command must name that one.
    assert moved_argv(["novelty", "routing", "--artifact", "skill:a", "--artifact", "skill:b",
                       "--catalog", "root1", "--catalog", "root2"]) == [
        "skill", "routing", "skill:b", "--against", "root2"]
    assert moved_argv(["novelty", "routing", "--all-vs-all", "--catalog", "root-a",
                       "--catalog=root-b"]) == ["catalog", "routing", "root-b"]


def test_novelty_routing_tombstone_keeps_a_bare_artifact_path_as_text():
    # A bare --artifact path was read as text (load_artifact_text), so the new SKILL must say so.
    assert moved_argv(["novelty", "routing", "--artifact", "AGENTS.md", "--competitors", "skill:r"]) == [
        "skill", "routing", "text:AGENTS.md", "--competitors", "skill:r"]
    assert moved_argv(["novelty", "routing", "--artifact", "text:n.md"]) == [
        "skill", "routing", "text:n.md"]


def test_novelty_routing_tombstone_reads_a_unique_flag_prefix_as_the_retired_flag():
    # The retired parser allowed any unique prefix (allow_abbrev), so the printed command spells it out.
    assert moved_argv(["novelty", "routing", "--artifact", "skill:a", "--cat", "skills/", "--rep", "3"]) == [
        "skill", "routing", "skill:a", "--against", "skills/", "--reps", "3"]
    assert moved_argv(["novelty", "routing", "--all", "--cat", "R", "--repeat=2"]) == [
        "catalog", "routing", "R", "--reps=2"]


def test_novelty_routing_tombstone_refuses_an_ambiguous_prefix(capsys):
    # `--c` is a prefix of --catalog and --competitors: the retired parser refused it, and the new
    # `skill routing` would read it as --competitors, so no command is printed.
    with pytest.raises(AmbiguousRetiredFlag):
        moved_argv(["novelty", "routing", "--artifact", "skill:a", "--c", "x"])
    with pytest.raises(SystemExit) as exc:
        parse_args(["novelty", "routing", "--artifact", "skill:a", "--a", "x"])
    assert exc.value.code == 2
    assert "ambiguous option: --a could match --all-vs-all, --artifact" in capsys.readouterr().err


def test_novelty_routing_tombstone_keeps_a_value_flag_with_no_value_for_the_new_leaf_to_refuse():
    # The retired parser refused a trailing --artifact or --catalog, and a flag as its value. The
    # printed argv keeps the flag, so the new leaf refuses it too instead of dropping it.
    for old, new in [
        (["novelty", "routing", "--all-vs-all", "--catalog", "c", "--artifact"],
         ["catalog", "routing", "c", "--artifact"]),
        (["novelty", "routing", "--artifact", "skill:a", "--catalog", "c", "--catalog"],
         ["skill", "routing", "skill:a", "--against", "c", "--catalog"]),
        (["novelty", "routing", "--artifact", "--catalog", "c"],
         ["skill", "routing", "--against", "c", "--artifact"]),
        (["novelty", "routing", "--artifact", "-x"],
         ["skill", "routing", "--artifact", "-x"]),
    ]:
        argv = moved_argv(old)
        assert argv == new
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(argv)
        assert exc.value.code == 2


def test_novelty_routing_tombstone_keeps_a_flag_the_new_leaf_refuses():
    argv = moved_argv(["novelty", "routing", "--all-vs-all", "--artifact", "skill:x", "--catalog", "skills/"])
    assert argv == ["catalog", "routing", "skills/", "--artifact", "skill:x"]


def test_novelty_routing_tombstone_refuses_all_vs_all_without_a_catalog(capsys):
    # The retired parser ran --all-vs-all on competitors alone; no catalog routing command can, and
    # a printed `catalog routing --competitors` needs a ROOT, so the tombstone says there is none.
    with pytest.raises(ValueError, match="no replacement"):
        moved_argv(["novelty", "routing", "--all-vs-all", "--competitors", "skill:a", "skill:b"])
    with pytest.raises(SystemExit) as exc:
        parse_args(["novelty", "routing", "--all-vs-all", "--competitors", "skill:a", "--queries-by-skill", "q.json"])
    assert exc.value.code == 2
    assert "no replacement" in capsys.readouterr().err


def test_novelty_routing_tombstone_refuses_a_stray_token_after_a_no_value_flag():
    # The retired parser had no positionals and these flags take no value: `y` after them was an
    # error. Left in the printed argv, `y` became the SKILL positional of `skill routing`.
    for old in (["novelty", "routing", "--queries-from-headings", "y", "--competitors", "a"],
                ["novelty", "routing", "--estimate-only", "y", "--artifact", "skill:a"],
                ["novelty", "routing", "--include-invalid", "y", "--artifact", "skill:a"],
                ["novelty", "routing", "--include-disabled", "y", "--artifact", "skill:a"]):
        with pytest.raises(ValueError, match="unrecognized arguments: y$"):
            moved_argv(old)


def test_novelty_routing_tombstone_refuses_a_stray_token_after_a_kept_value_flag():
    # --queries, --model and the other flags the new leaf keeps take one value: a second plain token
    # after it was an error in the retired parser, and must not become the SKILL positional.
    for old in (["novelty", "routing", "--queries", "q.txt", "y", "--competitors", "a"],
                ["novelty", "routing", "--model", "m", "y", "--artifact", "skill:a"],
                ["novelty", "routing", "--seed=1", "y", "--artifact", "skill:a"],
                ["novelty", "routing", "--repeats", "2", "-", "--artifact", "skill:a"]):
        with pytest.raises(ValueError, match="unrecognized arguments: (y|-)$"):
            moved_argv(old)


def test_novelty_routing_tombstone_refuses_a_positional_the_retired_parser_had_no_slot_for():
    # The retired parser had no positionals: a plain token anywhere the flags did not take it, or after
    # `--`, was an error, and so was a value after --all-vs-all. The tombstone must not print it into
    # the new command, where it becomes the SKILL positional.
    for old, pattern in [
        (["novelty", "routing", "y", "--competitors", "a"], "unrecognized arguments: y$"),
        (["novelty", "routing", "--competitors", "a", "--", "y"], "unrecognized arguments: y$"),
        (["novelty", "routing", "--seed", "-5", "y", "--competitors", "a"], "unrecognized arguments: y$"),
        (["novelty", "routing", "--all-vs-all=1", "--artifact", "skill:a", "--catalog", "R"],
         "ignored explicit argument '1'"),
    ]:
        with pytest.raises(ValueError, match=pattern):
            moved_argv(old)


def test_novelty_routing_tombstone_refuses_a_stray_token_after_a_value_flag():
    # The retired parser had no positionals: `y` after the value of --artifact or --catalog was an
    # error. Taking the flag out of the argv must not let `y` join --competitors.
    for old, stray in [
        (["novelty", "routing", "--competitors", "a", "b", "--artifact", "x", "y"], "y"),
        (["novelty", "routing", "--competitors", "a", "--catalog", "R", "y"], "y"),
        (["novelty", "routing", "--competitors", "a", "--catalog=R", "y"], "y"),
        (["novelty", "routing", "--artifact", "s", "--competitors", "a", "--catalog=R", "y"], "y"),
        (["novelty", "routing", "--catalog", "R", "--competitors", "a", "--all-vs-all", "y"], "y"),
        (["novelty", "routing", "--artifact", "skill:a", "-"], "-"),
        (["novelty", "routing", "--competitors=a", "b"], "b"),
    ]:
        with pytest.raises(ValueError, match=f"unrecognized arguments: {stray}$"):
            moved_argv(old)
    assert moved_argv(["novelty", "routing", "--artifact", "x", "--competitors", "a", "b"]) == [
        "skill", "routing", "text:x", "--competitors", "text:a", "text:b"]


def test_novelty_routing_tombstone_reads_a_bare_competitor_path_as_text():
    # A bare --competitors path was text on the retired command; the new leaf reads a bare path as a
    # skill, so the printed command says text: for it. skill: and text: pass through.
    assert moved_argv(["novelty", "routing", "--artifact", "skill:a", "--competitors", "skills/b",
                       "skill:c", "--queries", "q.txt"]) == [
        "skill", "routing", "skill:a", "--competitors", "text:skills/b", "skill:c", "--queries", "q.txt"]
    assert moved_argv(["novelty", "routing", "--artifact", "skill:a", "--competitors=skills/b"]) == [
        "skill", "routing", "skill:a", "--competitors=text:skills/b"]


def test_the_new_leaf_reads_a_bare_competitor_path_as_a_skill():
    args = build_parser().parse_args(["skill", "routing", "skill:a", "--competitors", "skills/b"])
    assert args.competitors == ["skill:skills/b"]


def test_novelty_routing_prints_the_new_command_and_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["novelty", "routing", "--artifact", "skill:a", "--catalog", "skills/"])
    assert exc.value.code == 2
    assert "moved: dokimi skill routing skill:a --against skills/" in capsys.readouterr().err


def test_novelty_view_still_lives_under_novelty():
    args = build_parser().parse_args(["novelty", "view", "--novelty", "n.json", "--atoms", "a.json"])
    assert args.novelty_cmd == "view"
