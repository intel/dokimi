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
from dokimi.novelty.routing import catalog_competitors
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
    assert "--keep-copies-under" in message


def test_keep_copies_under_keeps_the_copy_under_that_path_for_a_same_name_conflict(tmp_path, capsys):
    root = _ponytail(tmp_path / "ponytail")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--catalog", str(root), "--keep-copies-under", "skills",
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings",
                  "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["field"]["competitors"] == ["audit"]
    assert any("kept catalog skill 'audit'" in note for note in payload["field"]["left_out"])
    assert payload["field"]["keep_copies_under"] == "skills"
    assert "vs 1 competitors" in capsys.readouterr().out


def test_keep_copies_under_keeps_the_copy_under_the_other_directory_when_asked(tmp_path):
    root = _ponytail(tmp_path / "ponytail")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--catalog", str(root), "--keep-copies-under", ".openclaw/skills",
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings",
                  "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["field"]["competitors"] == ["audit"]
    assert any("openclaw" in note for note in payload["field"]["left_out"])


def test_keep_copies_under_puts_the_kept_copy_in_the_field_not_the_other(tmp_path):
    # Competitor names are the same for both copies, so only the description shows which copy is listed.
    root = _ponytail(tmp_path / "ponytail")
    kept = catalog_competitors(str(root), {"review"}, keep_copies_under="skills")
    assert [c.description for c in kept] == ["audit the repository for leftovers"]
    other = catalog_competitors(str(root), {"review"}, keep_copies_under=".openclaw/skills")
    assert [c.description for c in other] == ["audit a repository, openclaw copy"]


def test_keep_copies_under_that_matches_no_copy_leaves_the_name_a_conflict(tmp_path):
    root = _ponytail(tmp_path / "ponytail")
    (root / "docs").mkdir()
    args = _parse("novelty", "routing", "--catalog", str(root), "--keep-copies-under", "docs",
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings")
    with pytest.raises(InputError, match="copies with different content"):
        args.func(args)


@pytest.mark.parametrize("value, match", [
    ("", "is empty"),
    ("nope", "is not a directory under --catalog"),
    ("../outside", "leaves --catalog"),
    ("ABSOLUTE", "is an absolute path"),
])
def test_keep_copies_under_must_name_a_directory_inside_the_catalog(tmp_path, value, match):
    root = _ponytail(tmp_path / "ponytail")
    (tmp_path / "outside").mkdir()
    if value == "ABSOLUTE":
        value = str(root / "skills")
    args = _parse("novelty", "routing", "--catalog", str(root), "--keep-copies-under", value,
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings")
    with pytest.raises(InputError, match=f"--keep-copies-under .*{match}"):
        args.func(args)


def test_keep_copies_under_a_symlink_loop_is_a_usage_error_not_a_traceback(tmp_path):
    root = _ponytail(tmp_path / "ponytail")
    (root / "loop").symlink_to("loop")
    args = _parse("novelty", "routing", "--catalog", str(root), "--keep-copies-under", "loop",
                  "--artifact", f"skill:{root}/skills/review", "--queries-from-headings")
    # Python 3.11 and 3.12 name the loop; 3.13 resolves it to a path that is not a directory.
    with pytest.raises(InputError, match="--keep-copies-under .*(symlink loop|not a directory)"):
        args.func(args)


def test_keep_copies_under_needs_a_catalog(tmp_path):
    _skill(tmp_path, "skills", "review", "review a change for correctness")
    args = _parse("novelty", "routing", "--keep-copies-under", "skills",
                  "--artifact", f"skill:{tmp_path}/skills/review", "--queries-from-headings")
    with pytest.raises(InputError, match="--keep-copies-under needs --catalog"):
        args.func(args)


def test_the_catalog_subdir_the_error_names_routes_the_field(tmp_path, capsys):
    root = _ponytail(tmp_path / "ponytail")
    artifact = f"skill:{root}/skills/review"
    args = _parse("novelty", "routing", "--catalog", str(root), "--artifact", artifact,
                  "--queries-from-headings")
    with pytest.raises(InputError) as exc:
        args.func(args)
    suggested = f"{root}/skills"
    assert f"--catalog {suggested}" in str(exc.value)
    # the suggested command is the one that routes: the copies under that directory are the field
    args = _parse("novelty", "routing", "--catalog", suggested, "--artifact", artifact,
                  "--queries-from-headings", "--out", str(tmp_path / "routing.json"))
    args.func(args)
    assert "1 competitors" in capsys.readouterr().err


def test_the_hint_for_a_copy_at_the_catalog_root_names_the_root_not_a_dot_path(tmp_path, capsys):
    root = tmp_path / "catalog"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: review a change, copied at the root\n---\n\n# B\n",
        encoding="utf-8")
    _skill(root, "skills", "review", "review a change, under skills")
    _skill(root, "skills", "cache", "cache slow lookups")
    args = _parse("novelty", "routing", "--catalog", str(root),
                  "--artifact", f"skill:{root}/skills/cache", "--queries-from-headings")
    with pytest.raises(InputError) as exc:
        args.func(args)
    message = str(exc.value)
    assert f"--catalog {root}/." not in message
    assert "the catalog root" in message
    assert "--keep-copies-under skills" in message
    assert f"--catalog {root}/skills" in message
    # the warning that names the conflict says how to keep one copy
    assert "--keep-copies-under" in capsys.readouterr().err


def test_all_vs_all_does_not_name_a_skill_the_field_still_has_as_left_out(tmp_path):
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    off = root / "off" / "cache"
    off.mkdir(parents=True)
    (off / "SKILL.md").write_text("---\nname: cache\ndescription: cache another way\n"
                                  "disable-model-invocation: true\n---\n\n# B\n", encoding="utf-8")
    queries = _queries(tmp_path, {"cache": ["cache my lookups"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    with pytest.raises(InputError, match="at least two skills") as exc:
        args.func(args)
    # the disabled copy is left out, but the enabled one is still in the field: not "left out"
    assert "`cache`" not in str(exc.value)


def test_all_vs_all_says_a_queried_skill_failed_to_load_and_why(tmp_path):
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    broken = root / "skills" / "broken"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("---\nname: broken\ndescription: [unclosed\n---\n\n# B\n",
                                     encoding="utf-8")
    queries = _queries(tmp_path, {"cache": ["cache my lookups"], "queue": ["queue my jobs"],
                                  "broken": ["break my build"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    with pytest.raises(InputError) as exc:
        args.func(args)
    message = str(exc.value)
    assert "`broken`" in message and "failed to load" in message
    assert "no catalog skill" not in message


# Gap 2: a queries file naming a skill the field left out drops those queries, with a warning.

def test_all_vs_all_drops_the_queries_of_a_spec_error_skill_and_runs_the_rest(tmp_path, capsys):
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    _skill(root, "skills", "db", "x" * 1100)  # over 1024 characters: a spec error
    queries = _queries(tmp_path, {"cache": ["cache my lookups"], "queue": ["queue my jobs"],
                                  "db": ["tune my database", "index my tables"]})
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out))
    args.func(args)
    err = capsys.readouterr().err
    assert "WARNING" in err and "`db`" in err and "--include-invalid" in err
    assert "dropped 2 queries of" in err
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["result"]["n_queries"] == 2
    assert set(payload["result"]["lexical"]["confusion"]) == {"cache", "queue"}
    assert payload["field"]["dropped_queries"] == [
        {"skill": "db", "reason": "invalid", "keep_flag": "--include-invalid", "queries": 2}]


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
    assert "`queue`" in err and "--keep-copies-under" in err


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


def test_all_vs_all_refuses_a_queried_name_whose_other_copy_failed_to_load(tmp_path):
    # `review` has a malformed copy (failed to load) and a disabled copy (left out by a rule): the
    # load error is the reason its queries cannot run, so the run refuses instead of dropping them.
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    (root / "broken" / "review").mkdir(parents=True)
    (root / "broken" / "review" / "SKILL.md").write_text("---\nname: [unclosed\n---\n# B\n",
                                                         encoding="utf-8")
    (root / "off" / "review").mkdir(parents=True)
    (root / "off" / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: review a change\ndisable-model-invocation: true\n---\n# B\n",
        encoding="utf-8")
    queries = _queries(tmp_path, {"cache": ["cache my lookups"], "review": ["review my change"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--estimate-only")
    with pytest.raises(InputError, match="`review` is a catalog skill that failed to load"):
        args.func(args)


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


def test_a_skill_directory_directly_under_the_catalog_is_kept_by_its_own_name(tmp_path):
    root = tmp_path / "catalog"
    _skill(root, ".", "review", "review a change, copied beside the skills")
    _skill(root, "skills", "review", "review a change, under skills")
    _skill(root, "skills", "cache", "cache slow lookups")
    args = _parse("novelty", "routing", "--catalog", str(root),
                  "--artifact", f"skill:{root}/skills/cache", "--queries-from-headings")
    with pytest.raises(InputError) as exc:
        args.func(args)
    message = str(exc.value)
    # review/SKILL.md is a skill directory beneath the catalog: its own name keeps it, and no
    # --catalog picks it. It is not a copy at the catalog root, so no dedupe advice.
    assert "--keep-copies-under review" in message
    assert "the catalog root" not in message and "dedupe" not in message
    assert f"--catalog {root}/." not in message


def test_the_suggested_keep_for_a_skill_directory_beneath_the_catalog_routes_the_field(tmp_path):
    root = tmp_path / "catalog"
    _skill(root, ".", "review", "review a change, copied beside the skills")
    _skill(root, "skills", "review", "review a change, under skills")
    _skill(root, "skills", "cache", "cache slow lookups")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--catalog", str(root), "--keep-copies-under", "review",
                  "--artifact", f"skill:{root}/skills/cache", "--queries-from-headings",
                  "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["field"]["competitors"] == ["review"]


def test_all_vs_all_estimate_refuses_a_competitor_named_twice_as_the_run_does(tmp_path, capsys,
                                                                             monkeypatch):
    _no_client(monkeypatch)
    root = tmp_path / "catalog"
    _skill(root, "skills", "cache", "cache slow lookups")
    _skill(root, "skills", "queue", "queue background jobs")
    queries = _queries(tmp_path, {"cache": ["cache my lookups"]})
    cache = f"skill:{root}/skills/cache"
    base = ["novelty", "routing", "--all-vs-all", "--competitors", cache, cache,
            "--queries-by-skill", str(queries)]
    # The estimate prices a run only when the run can start: both refuse the same field.
    estimate = _parse(*base, "--estimate-only")
    with pytest.raises(InputError, match="more than once"):
        estimate.func(estimate)
    assert capsys.readouterr().out == ""
    run = _parse(*base, "--out", str(tmp_path / "o.json"))
    with pytest.raises(InputError, match="more than once"):
        run.func(run)


# Gap 4: every missing required flag of --all-vs-all is named in one error.

def test_all_vs_all_names_every_missing_required_flag_in_one_error(tmp_path):
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(tmp_path))
    with pytest.raises(InputError) as exc:
        args.func(args)
    message = str(exc.value)
    assert "--queries-by-skill" in message and "--out" in message
    assert message.count("--all-vs-all needs") == 1


def _hint_message(root, artifact):
    args = _parse("novelty", "routing", "--catalog", str(root),
                  "--artifact", f"skill:{root}/{artifact}", "--queries-from-headings")
    with pytest.raises(InputError) as exc:
        args.func(args)
    return str(exc.value)


def test_the_hint_for_nested_copies_suggests_only_the_directory_that_holds_one_copy(tmp_path):
    # Thread: PRRT_kwDOVBg7g860Lia8
    root = tmp_path / "catalog"
    _skill(root, "skills", "review", "review a change")
    _skill(root, "skills/legacy", "review", "review a change, the legacy copy")
    _skill(root, "other", "cache", "cache slow lookups")
    message = _hint_message(root, "other/cache")
    assert "--keep-copies-under skills/legacy" in message
    assert f"--catalog {root}/skills/legacy" in message
    # skills holds both copies, so it keeps the conflict: not suggested
    assert "--keep-copies-under skills " not in message and "--keep-copies-under skills." not in message
    assert f"--catalog {root}/skills " not in message and f"--catalog {root}/skills)" not in message


def test_the_hint_gives_the_ways_out_per_name_when_two_names_conflict(tmp_path):
    # Thread: PRRT_kwDOVBg7g860LibM
    root = tmp_path / "catalog"
    _skill(root, "a", "db", "tune a database")
    _skill(root, "b", "db", "tune a database, b copy")
    _skill(root, "c", "lint", "lint the code")
    _skill(root, "d", "lint", "lint the code, d copy")
    _skill(root, "e", "cache", "cache slow lookups")
    message = _hint_message(root, "e/cache")
    assert "`db`: copies sit under a and under b" in message
    assert "`lint`: copies sit under c and under d" in message
    assert "--keep-copies-under a or --keep-copies-under b." in message
    assert "one run resolves only the names" in message
