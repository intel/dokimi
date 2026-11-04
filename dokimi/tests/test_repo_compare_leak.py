# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo compare` flags the replay tasks a `guidance:rev:` arm may have seen (issue #290). No docker, no LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.repos.compare import compare, format_compare

REV = "guidance:rev:" + "f" * 40
SHA = {name: ch * 40 for name, ch in (("root", "a"), ("sub", "b"), ("rule", "c"), ("named", "d"), ("other", "e"))}
PUBLIC = "2026-03-01T12:00:00Z"
EARLY, LATE = "2026-02-01T00:00:00Z", "2026-04-01T00:00:00Z"


def _cell(batch: Path, task: str, arm: str, *, meta: object = None, spec: object = None, rep: int = 1,
          score: float = 0.5) -> None:
    rd = batch / f"{task}__{arm.replace(':', '-')}__m-1__r{rep}"
    rd.mkdir(parents=True)
    grade = {"status": "graded", "pass": None, "success": None, "score": score, "score_only": True, "reasons": []}
    rec = {"task": task, "arm": arm, "model": "m-1", "rep": rep, "score_task": True, "grade": grade,
           "harness_version": "1.0", "score_max": 1.0, "model_resolved": None,
           "agent": {"cost_usd": 0.1, "num_turns": 10, "timed_out": False, "is_error": False, "rc": 0,
                     "over_budget": False, "network": "none"}}
    (rd / "meta.json").write_text(json.dumps({} if meta is None else meta))
    if spec is not None:
        (rd / "task.json").write_text(spec if isinstance(spec, str) else json.dumps(spec))
    (rd / "result.json").write_text(json.dumps(rec))
    (rd / "grade.json").write_text(json.dumps({"metrics": {}}))


def _commit(sha: str, committed: str, subject: str = "Update guidance", authored: str | None = None) -> dict:
    return {"sha": sha, "authored": authored or committed, "committed": committed, "subject": subject}


def _rev(latest: dict[str, dict], commits: list[dict] | None = None, truncated: bool = False) -> dict:
    """A ``meta.json`` of a ``guidance:rev:`` cell: ``latest`` maps each guidance file to its last commit."""
    commits = sorted([*latest.values(), *(commits or [])], key=lambda c: c["committed"], reverse=True)
    return {"arm": REV, "arm_rev": {"rev": "main", "sha": "f" * 40, "removed": [],
                                    "guidance_latest": {p: {k: c[k] for k in ("sha", "authored", "committed")}
                                                        for p, c in latest.items()},
                                    "guidance_commits": commits, "guidance_commits_truncated": truncated}}


def _spec(pr: int, *, public: str | None = PUBLIC, files: list[str] | None = None) -> dict:
    spec: dict = {"id": f"review_replay_pr{pr}", "family": "review", "source_pr": pr,
                  "source_url": f"https://github.com/numpy/dpnp/pull/{pr}"}
    if public is not None:
        spec["public_at"] = public
    if files is not None:
        spec["patch_files"] = files
    return spec


def _batch(root: Path, meta: dict, specs: dict[str, dict], arm: str = REV, name: str = "b1") -> Path:
    """A batch of ``guidance:none`` and ``arm`` cells for each task of ``specs``."""
    batch = root / name
    for i, (task, spec) in enumerate(specs.items()):
        _cell(batch, task, "guidance:none", spec=spec, score=0.2)
        _cell(batch, task, arm, meta=meta, spec=spec, score=0.2 + 0.1 * (i % 3))
    return batch


def _leak(summary: dict) -> dict:
    assert len(summary["leak"]) == 1, summary["leak"]
    return summary["leak"][0]


def test_a_commit_naming_a_pr_and_one_after_a_review_are_flagged_the_others_left_clean(tmp_path):
    """Issue #290's two cases: a later guidance commit names PR 3103; the root AGENTS.md is committed after
    PR 3200's review. The tasks reviewed after both, or not named, stay clean, and no number moves."""
    meta = _rev({"AGENTS.md": _commit(SHA["root"], "2026-03-01T11:30:00Z")},
                [_commit(SHA["named"], "2026-01-15T00:00:00Z", "Update test guidance for the fixes in #3103")])
    specs = {"review_replay_pr3103": _spec(3103, public="2026-03-02T00:00:00Z"),  # after every commit: named only
             # 20:00+09:00 is 11:00Z, before the commit's 11:30Z, though it sorts after it as a string
             "review_replay_pr3200": _spec(3200, public="2026-03-01T20:00:00+09:00"),
             "review_replay_pr3300": _spec(3300, public="2026-03-01T12:00:00+00:00"),
             "review_replay_pr3301": _spec(3301, public="2026-05-01T00:00:00Z")}
    summary = compare([_batch(tmp_path, meta, specs)], "guidance:none")
    leak = _leak(summary)
    assert leak["arm"] == REV and leak["kind"] == "guidance:rev"
    assert leak["revisions"] == [{"rev": "main", "sha": "f" * 40}]
    assert leak["checked"] == sorted(specs)
    assert leak["by_date"] == {"review_replay_pr3200": {"path": "AGENTS.md", "sha": SHA["root"],
                                                        "committed": "2026-03-01T11:30:00Z",
                                                        "public_at": "2026-03-01T20:00:00+09:00", "scoped": False}}
    assert leak["by_name"] == {"review_replay_pr3103": [SHA["named"]]}
    assert leak["unknown"] == {} and leak["names_truncated"] is False
    assert leak["clean"] == ["review_replay_pr3300", "review_replay_pr3301"]
    plain = compare([_batch(tmp_path / "plain", {}, specs)], "guidance:none")  # no arm_rev: nothing flagged
    assert _leak(plain)["unknown"] == dict.fromkeys(specs, "its meta.json has no arm_rev object")
    assert plain["comparisons"] == summary["comparisons"]


def test_the_text_gives_the_counts_the_named_tasks_and_the_clean_ones_as_task_options(tmp_path):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], LATE)}, [_commit(SHA["named"], EARLY, "Rules from #3103")])
    specs = {"review_replay_pr3103": _spec(3103, public="2026-01-01T00:00:00Z", files=["src/a.py"]),
             "review_replay_pr3200": _spec(3200, files=["src/b.py"]),
             "review_replay_pr3300": _spec(3300, public="2026-05-01T00:00:00Z", files=["src/c.py"]),
             "review_replay_pr3301": _spec(3301, public=None, files=["src/d.py"])}
    lines = format_compare(compare([_batch(tmp_path, meta, specs)], "guidance:none")).splitlines()
    head = lines.index(f"leak check {REV} (main {'f' * 12}): 4 checked, 2 by date, 1 by name, 1 unknown, 1 clean")
    assert lines[head + 1:head + 4] == [
        f"  by name: review_replay_pr3103 ({SHA['named'][:12]})",
        "  unknown, no public_at: review_replay_pr3301",
        "  clean: --task review_replay_pr3300"]


def test_long_lists_of_the_text_are_cut_at_ten(tmp_path):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], LATE)})
    specs = {f"review_replay_pr{n}": _spec(n) for n in range(100, 112)}
    lines = format_compare(compare([_batch(tmp_path, meta, specs)], "guidance:none")).splitlines()
    shown = ", ".join(f"review_replay_pr{n}" for n in range(100, 110))
    assert f"  by date, not scoped to the patch (no patch_files): {shown} and 2 more" in lines
    assert not any(line.startswith("  clean:") for line in lines)  # no task is clean: nothing to pass to --task


@pytest.mark.parametrize("guidance,flagged", [
    ("sub/AGENTS.md", ["review_replay_pr1"]),
    ("AGENTS.md", ["review_replay_pr1", "review_replay_pr2", "review_replay_pr3"]),
    ("sub/CLAUDE.md", ["review_replay_pr1"]),
    ("sub/.claude/CLAUDE.md", ["review_replay_pr1"]),  # .claude/ is its parent directory's
    ("sub/GEMINI.md", ["review_replay_pr1"]),
    (".claude/rules/CLAUDE.md", ["review_replay_pr1", "review_replay_pr2", "review_replay_pr3"]),  # a rule, not memory
    ("sub/.claude/rules/AGENTS.md", ["review_replay_pr1", "review_replay_pr2", "review_replay_pr3"]),
    (".claude/rules/x.md", ["review_replay_pr1", "review_replay_pr2", "review_replay_pr3"]),  # not scoped: strict
    ("sub/docs/guide.md", ["review_replay_pr1", "review_replay_pr2", "review_replay_pr3"]),
])
def test_a_directory_scoped_file_flags_only_the_tasks_patching_below_it(tmp_path, guidance, flagged):
    meta = _rev({guidance: _commit(SHA["sub"], LATE), "other/AGENTS.md": _commit(SHA["other"], LATE)})
    specs = {"review_replay_pr1": _spec(1, files=["sub/deep/x.py"]),
             "review_replay_pr2": _spec(2, files=["other2/y.py"]),
             "review_replay_pr3": _spec(3, files=["subway/z.py"])}  # sub/ is not a prefix of subway/
    leak = _leak(compare([_batch(tmp_path, meta, specs)], "guidance:none"))
    assert sorted(leak["by_date"]) == flagged
    assert all(hit["path"] == guidance and hit["scoped"] for hit in leak["by_date"].values())
    assert leak["clean"] == sorted(set(specs) - set(flagged))


def test_a_task_without_patch_files_is_read_strictly_and_says_so(tmp_path):
    meta = _rev({"sub/AGENTS.md": _commit(SHA["sub"], LATE)})
    specs = {"review_replay_pr1": _spec(1), "review_replay_pr2": _spec(2, files=["other/y.py"])}
    leak = _leak(compare([_batch(tmp_path, meta, specs)], "guidance:none"))
    assert leak["by_date"] == {"review_replay_pr1": {"path": "sub/AGENTS.md", "sha": SHA["sub"], "committed": LATE,
                                                     "public_at": PUBLIC, "scoped": False}}
    assert leak["clean"] == ["review_replay_pr2"]


@pytest.mark.parametrize("authored,committed,flagged", [
    (EARLY, LATE, True),   # amended or rebased after the review: the author date stays
    (LATE, EARLY, False),
])
def test_the_date_flag_reads_the_committer_date(tmp_path, authored, committed, flagged):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], committed, authored=authored)})
    leak = _leak(compare([_batch(tmp_path, meta, {"review_replay_pr1": _spec(1)})], "guidance:none"))
    assert (list(leak["by_date"]) == ["review_replay_pr1"]) is flagged
    assert leak["clean"] == ([] if flagged else ["review_replay_pr1"])


def test_the_latest_applicable_commit_is_the_one_recorded(tmp_path):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], "2026-03-05T00:00:00Z"),
                 "sub/AGENTS.md": _commit(SHA["sub"], "2026-03-09T00:00:00Z"),
                 "other/AGENTS.md": _commit(SHA["other"], "2026-03-20T00:00:00Z")})
    leak = _leak(compare([_batch(tmp_path, meta, {"review_replay_pr1": _spec(1, files=["sub/x.py"])})],
                         "guidance:none"))
    assert leak["by_date"]["review_replay_pr1"]["sha"] == SHA["sub"]


@pytest.mark.parametrize("subject,named", [
    ("Update test guidance for the fixes in #3103", True),
    ("Rules from numpy/dpnp#3103", True),
    ("Rules from NumPy/dpnp#3103.", True),
    ("See https://github.com/numpy/dpnp/pull/3103 for why", True),
    ("(#3103)", True),
    ("Fix #31030", False),
    ("Rules from other/repo-#3103", False),  # - and . are repository-name characters
    ("Rules from other/repo.#3103", False),
    ("Rules from other/repo#3103", False),
    ("Rules from https://example.invalid/tracker/#3103", False),  # a path's #3103 is not this PR's
    ("Rules from xnumpy/dpnp#3103", False),
    ("abc#3103", False),
    ("See https://github.com/numpy/dpnp/pull/31030", False),
    ("See https://github.com/other/repo/pull/3103", False),
    ("See https://notgithub.com/numpy/dpnp/pull/3103", False),  # host must match whole
    ("See https://evil.github.com/numpy/dpnp/pull/3103", False),
    ("See https://evil.invalid/github.com/numpy/dpnp/pull/3103", False),
    ("See github.com/numpy/dpnp/pull/3103", True),  # no scheme still names it
    ("See xhttps://github.com/numpy/dpnp/pull/3103", False),
])
def test_a_subject_names_the_pr_by_its_number_its_repo_or_its_url(tmp_path, subject, named):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], EARLY)}, [_commit(SHA["named"], EARLY, subject)])
    leak = _leak(compare([_batch(tmp_path, meta, {"review_replay_pr3103": _spec(3103)})], "guidance:none"))
    assert leak["by_name"] == ({"review_replay_pr3103": [SHA["named"]]} if named else {})
    assert leak["clean"] == ([] if named else ["review_replay_pr3103"])


def test_a_cut_history_is_reported_in_the_json_and_the_text(tmp_path):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], EARLY)}, truncated=True)
    summary = compare([_batch(tmp_path, meta, {"review_replay_pr1": _spec(1)})], "guidance:none")
    assert _leak(summary)["names_truncated"] is True
    assert "  by name covers only the newest 200 guidance commits (guidance_commits_truncated)" in \
        format_compare(summary).splitlines()


def test_a_patch_arm_has_every_replay_task_unknown_and_guidance_none_is_not_checked(tmp_path):
    arm = "patch:rules.patch@" + "1" * 12
    meta = {"arm": arm, "arm_patch": {"path": "/x/rules.patch", "sha256": "1" * 64, "added": [], "mcp_servers": []}}
    specs = {"review_replay_pr1": _spec(1), "review_replay_pr2": _spec(2)}
    summary = compare([_batch(tmp_path, meta, specs, arm=arm)], "guidance:none")
    leak = _leak(summary)
    assert leak["arm"] == arm and leak["kind"] == "patch" and leak["revisions"] == []
    assert leak["unknown"] == dict.fromkeys(specs, "a patch: arm has no guidance history")
    assert leak["clean"] == [] and leak["by_date"] == leak["by_name"] == {}
    assert "  unknown, a patch: arm has no guidance history: review_replay_pr1, review_replay_pr2" in \
        format_compare(summary)


def test_a_guidance_rev_baseline_is_checked_and_a_raw_treatment_is_not(tmp_path):
    batch = tmp_path / "b1"
    meta = _rev({"AGENTS.md": _commit(SHA["root"], LATE)})
    _cell(batch, "review_replay_pr1", REV, meta=meta, spec=_spec(1))
    _cell(batch, "review_replay_pr1", "guidance:raw", meta={"arm": "guidance:raw"}, spec=_spec(1))
    _cell(batch, "review_replay_pr1", "guidance:none", meta={"arm": "guidance:none"}, spec=_spec(1))
    assert [e["arm"] for e in compare([batch], REV)["leak"]] == [REV]
    assert compare([batch], "guidance:none", ["guidance:raw"])["leak"] == []


def test_a_task_with_no_public_at_is_unknown_by_date_and_still_checked_by_name(tmp_path):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], LATE)}, [_commit(SHA["named"], EARLY, "Fixes for #7")])
    specs = {"review_replay_pr7": _spec(7, public=None), "review_replay_pr8": _spec(8, public=None)}
    leak = _leak(compare([_batch(tmp_path, meta, specs)], "guidance:none"))
    assert leak["checked"] == sorted(specs) and leak["by_date"] == {}
    assert leak["by_name"] == {"review_replay_pr7": [SHA["named"]]}
    assert leak["unknown"] == dict.fromkeys(specs, "no public_at") and leak["clean"] == []


def test_a_batch_from_before_the_history_was_recorded_is_unknown(tmp_path):
    meta = {"arm": REV, "arm_rev": {"rev": "main", "sha": "f" * 40, "removed": []}}
    leak = _leak(compare([_batch(tmp_path, meta, {"review_replay_pr1": _spec(1)})], "guidance:none"))
    assert leak["unknown"] == {"review_replay_pr1": "no guidance history recorded"} and leak["clean"] == []


def test_a_task_that_is_not_replay_style_is_not_checked(tmp_path):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], LATE)})
    specs = {"fix_a": {"id": "fix_a", "family": "fix"}, "review_replay_pr1": _spec(1)}
    leak = _leak(compare([_batch(tmp_path, meta, specs)], "guidance:none"))
    assert leak["checked"] == ["review_replay_pr1"]
    assert compare([_batch(tmp_path / "x", meta, {"fix_a": specs["fix_a"]})], "guidance:none")["leak"] == []


def test_task_narrows_the_check_to_the_selected_tasks(tmp_path):
    meta = _rev({"AGENTS.md": _commit(SHA["root"], LATE)})
    specs = {f"review_replay_pr{n}": _spec(n, public=EARLY if n == 1 else "2026-05-01T00:00:00Z") for n in (1, 2, 3)}
    batch = _batch(tmp_path, meta, specs)
    leak = _leak(compare([batch], "guidance:none", tasks=["review_replay_pr1", "review_replay_pr3"]))
    assert leak["checked"] == ["review_replay_pr1", "review_replay_pr3"]
    assert list(leak["by_date"]) == ["review_replay_pr1"] and leak["clean"] == ["review_replay_pr3"]


def test_the_cells_of_one_task_flag_it_together(tmp_path):
    """Two batches pooled under one arm name: a flag either cell sets counts, and the named commits are both."""
    one = _rev({"AGENTS.md": _commit(SHA["root"], EARLY)}, [_commit(SHA["named"], EARLY, "From #5")])
    two = _rev({"AGENTS.md": _commit(SHA["root"], LATE)}, [_commit(SHA["other"], EARLY, "Also #5")])
    batches = [_batch(tmp_path, one, {"review_replay_pr5": _spec(5)}, name="b1"),
               _batch(tmp_path, two, {"review_replay_pr5": _spec(5)}, name="b2")]
    leak = _leak(compare(batches, "guidance:none"))
    assert list(leak["by_date"]) == ["review_replay_pr5"] and leak["by_date"]["review_replay_pr5"]["sha"] == SHA["root"]
    assert leak["by_name"] == {"review_replay_pr5": [SHA["named"], SHA["other"]]}


@pytest.mark.parametrize("meta,spec,reason", [
    ("[]", _spec(1), "its meta.json is not an object"),
    ("{", _spec(1), "its meta.json is not readable JSON"),
    (None, "[1]", "its task.json is not an object"),
    (None, "{", "its task.json is not readable JSON"),
    (None, _spec(1) | {"public_at": 20260301}, "public_at is not a date with a UTC offset"),
    (None, _spec(1) | {"public_at": "2026-03-01T12:00:00"}, "public_at is not a date with a UTC offset"),
    (None, _spec(1) | {"patch_files": "src/x.py"}, "patch_files is not a list of paths"),
    (None, _spec(1) | {"source_pr": "1x"}, "source_pr is not a PR number"),
    ({"arm": REV, "arm_rev": {"guidance_latest": {"AGENTS.md": {"sha": SHA["root"], "committed": 5}},
                              "guidance_commits": []}}, _spec(1), "guidance_latest has no committed date for AGENTS.md"),
    ({"arm": REV, "arm_rev": {"guidance_latest": [], "guidance_commits": []}}, _spec(1),
     "guidance_latest is not an object"),
    ({"arm": REV, "arm_rev": {"guidance_latest": {}, "guidance_commits": [7]}}, _spec(1),
     "a guidance_commits entry has no subject"),
    ({"arm": REV, "arm_rev": {"guidance_latest": {}}}, _spec(1), "no guidance_commits recorded"),
])
def test_a_cell_file_of_the_wrong_shape_makes_its_task_unknown(tmp_path, meta, spec, reason):
    batch = tmp_path / "b1"
    _cell(batch, "review_replay_pr1", "guidance:none", spec=_spec(1))
    _cell(batch, "review_replay_pr1", REV, meta=_rev({}) if meta is None else meta, spec=spec)
    if isinstance(meta, str):
        (next(batch.glob(f"*{REV.replace(':', '-')}*")) / "meta.json").write_text(meta)
    leak = _leak(compare([batch], "guidance:none"))
    assert leak["unknown"] == {"review_replay_pr1": reason} and leak["clean"] == []
