# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Routing gaps: same-name conflicts in a catalog, queries of left-out skills, all-vs-all estimate,
and every missing required flag of --all-vs-all reported at once."""

from __future__ import annotations

import json

import pytest

import dokimi.commands.novelty as novelty_cmd
from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient
from dokimi.utils import InputError


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _skill(root, rel, name, description, body="# B\n\n## A section here\n"):
    skill_dir = root / rel / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}", encoding="utf-8")


def _ponytail(root):
    """ponytail's shape: every name in skills/ and .openclaw/skills/, with different content."""
    _skill(root, "skills", "review", "review a change for correctness")
    _skill(root, "skills", "audit", "audit the repository for leftovers")
    _skill(root, ".openclaw/skills", "review", "review a change, copied for openclaw")
    _skill(root, ".openclaw/skills", "audit", "audit a repository, openclaw copy")
    return root


def _queries(tmp_path, by_skill):
    path = tmp_path / "queries_by_skill.json"
    path.write_text(json.dumps(by_skill), encoding="utf-8")
    return path


def _no_client(monkeypatch):
    monkeypatch.setattr(novelty_cmd, "_client",
                        lambda *a, **k: pytest.fail("a run made a model call"))
    monkeypatch.setattr(LLMClient, "text",
                        lambda self, *a, **k: pytest.fail("the estimate made a model call"))


# Gap 1: same-name copies that differ leave the field, and the error says so.

def test_a_field_emptied_by_name_conflicts_says_so_and_suggests_a_subdir(tmp_path):
    root = _ponytail(tmp_path / "ponytail")
    args = _parse("novelty", "routing", "--catalog", str(root),
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings")
    with pytest.raises(InputError) as exc:
        args.func(args)
    message = str(exc.value)
    assert "different content" in message
    assert "`audit`" in message
    assert f"--catalog {root}/skills" in message
    assert f"--catalog {root}/.openclaw/skills" in message
    assert "--prefer-path" in message


def test_prefer_path_keeps_the_copy_under_that_path_for_a_same_name_conflict(tmp_path, capsys):
    root = _ponytail(tmp_path / "ponytail")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--catalog", str(root), "--prefer-path", "skills",
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings",
                  "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["field"]["competitors"] == ["audit"]
    assert any("kept catalog skill 'audit'" in note for note in payload["field"]["left_out"])
    assert "vs 1 competitors" in capsys.readouterr().out


def test_prefer_path_keeps_the_copy_under_the_other_directory_when_asked(tmp_path):
    root = _ponytail(tmp_path / "ponytail")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--catalog", str(root), "--prefer-path", ".openclaw/skills",
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings",
                  "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["field"]["competitors"] == ["audit"]
    assert any("openclaw" in note for note in payload["field"]["left_out"])


def test_prefer_path_that_matches_no_copy_leaves_the_name_a_conflict(tmp_path):
    root = _ponytail(tmp_path / "ponytail")
    (root / "docs").mkdir()
    args = _parse("novelty", "routing", "--catalog", str(root), "--prefer-path", "docs",
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings")
    with pytest.raises(InputError, match="copies with different content"):
        args.func(args)


def test_prefer_path_must_be_a_directory_under_the_catalog(tmp_path):
    root = _ponytail(tmp_path / "ponytail")
    args = _parse("novelty", "routing", "--catalog", str(root), "--prefer-path", "nope",
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings")
    with pytest.raises(InputError, match="--prefer-path is not a directory under --catalog"):
        args.func(args)


def test_prefer_path_needs_a_catalog(tmp_path):
    _skill(tmp_path, "skills", "review", "review a change for correctness")
    args = _parse("novelty", "routing", "--prefer-path", "skills",
                  "--artifact", f"skill:{tmp_path}/skills/review", "--queries-from-headings")
    with pytest.raises(InputError, match="--prefer-path needs --catalog"):
        args.func(args)


def test_the_catalog_subdir_the_error_names_routes_the_field(tmp_path, capsys):
    root = _ponytail(tmp_path / "ponytail")
    args = _parse("novelty", "routing", "--catalog", str(root / "skills"),
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings")
    args.func(args)
    assert "1 competitors" in capsys.readouterr().err


# Gap 2: a queries file naming a skill the field left out drops those queries, with a warning.

def test_all_vs_all_drops_the_queries_of_a_spec_error_skill_and_runs_the_rest(tmp_path, capsys):
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    _skill(root, "skills", "db", "x" * 1100)  # over 1024 characters: a spec error
    queries = _queries(tmp_path, {"cache": ["cache my lookups"], "queue": ["queue my jobs"],
                                  "db": ["tune my database"]})
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out))
    args.func(args)
    err = capsys.readouterr().err
    assert "WARNING" in err and "`db`" in err and "--include-invalid" in err
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["result"]["n_queries"] == 2
    assert set(payload["result"]["lexical"]["confusion"]) == {"cache", "queue"}
    assert any("db" in note and "dropped" in note for note in payload["field"]["left_out"])


def test_all_vs_all_names_the_flag_that_keeps_each_dropped_skill(tmp_path, capsys):
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    _skill(root, "skills", "old", "old helper")
    _skill(root, "skills", "db", "tune a database index")
    old = root / "skills" / "old" / "SKILL.md"
    old.write_text("---\nname: old\ndescription: old helper\n"
                   "disable-model-invocation: true\n---\n\n# B\n", encoding="utf-8")
    _skill(root / "plugins", "x", "queue", "a different queue skill")
    queries = _queries(tmp_path, {"cache": ["cache my lookups"], "queue": ["queue my jobs"],
                                  "old": ["an old helper"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    args.func(args)
    err = capsys.readouterr().err
    assert "`old`" in err and "--include-disabled" in err
    assert "`queue`" in err and "--prefer-path" in err


def test_all_vs_all_refuses_only_when_no_query_remains(tmp_path):
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    _skill(root, "skills", "db", "x" * 1100)
    queries = _queries(tmp_path, {"db": ["tune my database"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    with pytest.raises(InputError, match="no skill in the field has queries.*--include-invalid"):
        args.func(args)
    assert not (tmp_path / "o.json").exists()


def test_all_vs_all_still_refuses_a_name_no_rule_left_out(tmp_path):
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    queries = _queries(tmp_path, {"cache": ["cache my lookups"], "cahce": ["typo"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    with pytest.raises(InputError, match="not in the field: `cahce`"):
        args.func(args)


# Gap 3: --all-vs-all --estimate-only prices every call and makes none.

def test_all_vs_all_estimate_prices_each_call_and_sends_nothing(tmp_path, capsys, monkeypatch):
    import dokimi.llm_pricing as pricing

    _no_client(monkeypatch)
    monkeypatch.setattr(pricing, "list_cost_usd",
                        lambda model, provider, prompt, completion: prompt * 1e-6 + completion * 2e-6)
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    _skill(root, "skills", "db", "tune a database index")
    queries = _queries(tmp_path, {"cache": ["cache my lookups", "the a of"],
                                  "queue": ["queue my jobs"], "db": ["index a table"]})
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--model", "m", "--repeats", "2",
                  "--estimate-only")
    args.func(args)
    estimate = json.loads(capsys.readouterr().out)
    assert set(estimate) == {"estimate_only", "artifact", "model", "provider", "n_candidates",
                             "n_queries", "n_queries_sent", "n_calls", "input_tokens",
                             "output_tokens", "expected_usd"}
    assert estimate["estimate_only"] is True and estimate["artifact"] is None
    assert estimate["n_candidates"] == 3
    # Four queries given; the contentless "the a of" is not sent by the all-vs-all model arm.
    assert estimate["n_queries"] == 4 and estimate["n_queries_sent"] == 3
    assert estimate["n_calls"] == 6
    assert estimate["expected_usd"] == round(
        estimate["input_tokens"] * 1e-6 + estimate["output_tokens"] * 2e-6, 6)
    assert not out.exists()


def test_all_vs_all_estimate_counts_one_call_per_query_of_each_skill_over_the_whole_field(
        tmp_path, capsys, monkeypatch):
    import dokimi.llm_pricing as pricing

    _no_client(monkeypatch)
    seen = []
    monkeypatch.setattr(pricing, "list_cost_usd",
                        lambda model, provider, prompt, completion: seen.append(prompt) or 0.0)
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    queries = _queries(tmp_path, {"cache": ["cache my lookups"], "queue": ["queue my jobs"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--model", "m", "--estimate-only")
    args.func(args)
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["n_calls"] == 2 and len(seen) == 2
    assert estimate["n_queries"] == 2 and estimate["n_queries_sent"] == 2


def test_all_vs_all_estimate_without_a_model_is_lexical_and_free(tmp_path, capsys, monkeypatch):
    _no_client(monkeypatch)
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    queries = _queries(tmp_path, {"cache": ["cache my lookups"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--estimate-only")
    args.func(args)
    estimate = json.loads(capsys.readouterr().out)
    assert (estimate["n_calls"], estimate["input_tokens"], estimate["expected_usd"]) == (0, 0, 0.0)


def test_all_vs_all_estimate_drops_left_out_queries_like_the_run(tmp_path, capsys, monkeypatch):
    _no_client(monkeypatch)
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    _skill(root, "skills", "db", "x" * 1100)
    queries = _queries(tmp_path, {"db": ["tune my database"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--estimate-only")
    with pytest.raises(InputError, match="no skill in the field has queries"):
        args.func(args)
    queries = _queries(tmp_path, {"cache": ["cache my lookups"], "db": ["x"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--estimate-only")
    args.func(args)
    assert json.loads(capsys.readouterr().out)["n_queries"] == 1


# Gap 4: every missing required flag of --all-vs-all is named in one error.

def test_all_vs_all_names_every_missing_required_flag_in_one_error(tmp_path):
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(tmp_path))
    with pytest.raises(InputError) as exc:
        args.func(args)
    message = str(exc.value)
    assert "--queries-by-skill" in message and "--out" in message
    assert message.count("--all-vs-all needs") == 1


def test_all_vs_all_names_the_one_missing_flag_when_only_one_is_missing(tmp_path):
    queries = _queries(tmp_path, {"cache": ["cache my lookups"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(tmp_path),
                  "--queries-by-skill", str(queries))
    with pytest.raises(InputError, match="--all-vs-all needs --out <file>") as exc:
        args.func(args)
    assert "--queries-by-skill" not in str(exc.value)
