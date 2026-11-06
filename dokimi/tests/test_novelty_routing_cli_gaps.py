# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Routing: queries from description sentences, the seed help, and the estimate-only mode."""

from __future__ import annotations

import json
import os
import sys

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient
from dokimi.utils import InputError


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _skill(tmp_path, name, description, body="# B\n\n## A section here\n"):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}", encoding="utf-8")
    return f"skill:{root}"


def test_attractor_line_counts_calls_when_the_model_arm_repeats(capsys):
    from dokimi.commands.novelty_routing_report import print_attractors

    row = {"skill": "perf", "wrongly_picked": 3, "sibling_queries": 6, "from": ["db"]}
    print_attractors({"tied_groups": [], "model": {"attractors": [row], "confusion": {},
                                                   "n_unscored": {}, "repeats": 3}})
    assert "picked for 3 of 6 sibling calls" in capsys.readouterr().out


def test_a_skill_with_no_usable_heading_is_refused_even_with_a_description(tmp_path, capsys):
    # The description is not a fallback: only headings are triage queries.
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "grill", "Grill the user about a plan. "
                                       "Use when stress-testing a design.",
                                       body="# Grill\n\n## Steps\n"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries-from-headings", "--out", str(out))
    with pytest.raises(InputError):
        args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "no_queries"
    assert payload["queries_source"] == "none"
    assert "no section heading" in payload["reason"]


def test_no_derived_queries_refuses_without_claiming_a_derivation(tmp_path, capsys):
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "grill", "Stop.", body="# Grill\n\n## Steps\n"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries-from-headings", "--out", str(out))
    with pytest.raises(InputError):
        args.func(args)
    err = capsys.readouterr().err
    assert "derived from the artifact" not in err
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "no_queries"


def test_the_seed_help_says_what_it_fixes_and_does_not_call_the_run_reproducible(capsys):
    with pytest.raises(SystemExit):  # argparse prints the help and exits 0
        _parse("novelty", "routing", "--help")
    text = " ".join(capsys.readouterr().out.split())
    seed_help = text[text.rindex("--seed"):text.index("--repeats", text.rindex("--seed"))]
    assert "card order" in seed_help
    assert "reproducible" not in seed_help


def test_estimate_only_counts_the_calls_prices_them_and_sends_nothing(tmp_path, capsys,
                                                                     monkeypatch):
    import dokimi.llm_pricing as pricing

    monkeypatch.setattr(LLMClient, "text",
                        lambda self, *a, **k: pytest.fail("the estimate made a model call"))
    monkeypatch.setattr(pricing, "list_cost_usd",
                        lambda model, provider, prompt, completion: prompt * 1e-6 + completion * 2e-6)
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary", "parallelize my loops", "the a of"]),
                       encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "perf", "profile a binary with perf record"),
                  "--competitors", _skill(tmp_path, "tbb", "parallelize loops with task groups"),
                  "--queries", str(queries), "--model", "m", "--repeats", "2",
                  "--estimate-only", "--out", str(out))
    args.func(args)
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["estimate_only"] is True
    # Every query is sent, contentless "the a of" included, as routing sends it: 3 x 2 repeats.
    assert estimate["n_queries"] == 3 and estimate["n_queries_sent"] == 3
    assert estimate["n_calls"] == 6
    assert estimate["input_tokens"] > 4 * 50 and estimate["output_tokens"] > 0
    assert estimate["expected_usd"] == round(
        estimate["input_tokens"] * 1e-6 + estimate["output_tokens"] * 2e-6, 6)
    assert not out.exists()


def test_estimate_only_without_a_model_is_lexical_and_free(tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "perf", "profile a binary with perf record"),
                  "--competitors", _skill(tmp_path, "tbb", "parallelize loops"),
                  "--queries", str(queries), "--estimate-only")
    args.func(args)
    estimate = json.loads(capsys.readouterr().out)
    assert (estimate["n_calls"], estimate["input_tokens"], estimate["expected_usd"]) == (0, 0, 0.0)


def test_a_zero_repeat_estimate_sends_no_queries():
    from dokimi.novelty.routing import Candidate, selection_estimate

    field = [Candidate("perf", "profile a slow binary"), Candidate("db", "tune a database")]
    estimate = selection_estimate(field, ["profile my binary", "index a table"], repeats=0)
    assert estimate["n_queries"] == 2
    assert (estimate["n_queries_sent"], estimate["n_calls"], estimate["input_tokens"],
            estimate["output_tokens"]) == (0, 0, 0, 0)


def test_estimate_prices_each_call_so_a_long_context_tier_is_not_reached_by_the_run_total(
        tmp_path, capsys, monkeypatch):
    import dokimi.llm_pricing as pricing

    def tiered(model, provider, prompt, completion):
        # A long-context tier as litellm applies it: the rate follows one call's prompt size.
        rate = 1e-6 if prompt <= 200_000 else 2e-6
        return prompt * rate + completion * 2e-6

    monkeypatch.setattr(LLMClient, "text",
                        lambda self, *a, **k: pytest.fail("the estimate made a model call"))
    monkeypatch.setattr(pricing, "list_cost_usd", tiered)
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary", "profile a program"]), encoding="utf-8")
    # About 48k tokens per call and six calls: 288k in total, past the tier, no single call past it.
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "perf", "profile " + "filler " * 27_000),
                  "--competitors", _skill(tmp_path, "tbb", "parallelize loops"),
                  "--queries", str(queries), "--model", "m", "--repeats", "3",
                  "--estimate-only")
    args.func(args)
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["input_tokens"] > 200_000 and estimate["n_calls"] == 6
    assert estimate["expected_usd"] == pytest.approx(
        estimate["input_tokens"] * 1e-6 + estimate["output_tokens"] * 2e-6, rel=1e-6)


def test_estimate_only_gives_a_null_dollar_figure_for_a_routing_model_with_no_price(
        tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "perf", "profile a binary with perf record"),
                  "--competitors", _skill(tmp_path, "tbb", "parallelize loops"),
                  "--queries", str(queries), "--model", "foo-bar-9", "--provider", "openai",
                  "--estimate-only")
    args.func(args)
    captured = capsys.readouterr()
    estimate = json.loads(captured.out)
    assert estimate["n_calls"] == 1 and estimate["expected_usd"] is None
    assert "expected_usd is null" in captured.err


@pytest.mark.parametrize("queries", [None, ["the a of", "how do i"]],
                         ids=["no-query-lines", "only-stop-words"])
def test_estimate_only_refuses_the_run_the_real_run_refuses(tmp_path, capsys, queries):
    out = tmp_path / "routing.json"
    flags = ["--queries-from-headings"]
    if queries is not None:
        qfile = tmp_path / "q.json"
        qfile.write_text(json.dumps(queries), encoding="utf-8")
        flags = ["--queries", str(qfile)]
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "grill", "Stop.", body="# Grill\n\n## Steps\n"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  *flags, "--model", "m", "--estimate-only", "--out", str(out))
    with pytest.raises(InputError, match="no usable queries"):
        args.func(args)
    assert not out.exists()


def test_the_estimate_counts_the_prompts_the_run_sends():
    # The estimate and the run each build the selection prompt. Every prompt the run sends must
    # have the length the estimate counts for it, so a drift between the two shows here.
    from dokimi.novelty.routing import Candidate, model_routing, selection_prompt_tokens

    field = [Candidate("perf", "profile a slow binary with perf"),
             Candidate("db", "tune a database index"),
             Candidate("tbb", "parallelize loops with threads")]
    queries = ["profile my binary", "index a table", "parallelize the loops"]
    sent = []

    class Recorder:
        model = "m"

        def text(self, prompt, *, system=None, accept=None, draw=0):
            sent.append(prompt)
            return '{"pick": 1}'

    model_routing(field, queries, client=Recorder(), gold=0, repeats=1)
    assert [len(prompt) // 4 for prompt in sent] == selection_prompt_tokens(field, queries)


def _two_skill_field(tmp_path):
    return _skill(tmp_path, "perf", "profile a slow binary"), _skill(
        tmp_path, "db", "tune a slow database query")


def test_routing_refuses_an_out_directory_before_it_calls_the_model(tmp_path, monkeypatch):
    import dokimi.commands.novelty as novelty_cmd

    monkeypatch.setattr(novelty_cmd, "_client",
                        lambda *a, **k: pytest.fail("a model call was made before --out was checked"))
    artifact, competitor = _two_skill_field(tmp_path)
    queries = tmp_path / "queries.txt"
    queries.write_text("profile my slow binary\n", encoding="utf-8")
    args = _parse("novelty", "routing", "--artifact", artifact, "--competitors", competitor,
                  "--queries", str(queries), "--model", "m", "--out", str(tmp_path))
    with pytest.raises(InputError, match="is a directory"):
        args.func(args)


def test_a_missing_out_directory_is_accepted_and_left_for_the_writer_to_create(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    missing = tmp_path / "new" / "deeper" / "routing.json"
    assert checked_out_path(str(missing), "routing") == missing
    assert not missing.parent.exists()


def test_an_out_under_a_file_is_refused_before_the_call(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(InputError, match="is not a directory"):
        checked_out_path(str(blocker / "sub" / "routing.json"), "routing")


def test_a_dangling_out_symlink_into_a_missing_directory_is_refused_before_the_call(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    link = tmp_path / "routing.json"
    link.symlink_to(tmp_path / "missing" / "result.json")
    with pytest.raises(InputError, match="does not exist"):
        checked_out_path(str(link), "routing")


def test_an_out_under_a_dangling_directory_symlink_is_refused_before_the_call(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    (tmp_path / "outdir").symlink_to(tmp_path / "missing")
    with pytest.raises(InputError, match="dangling symlink"):
        checked_out_path(str(tmp_path / "outdir" / "routing.json"), "routing")


def test_an_out_symlink_under_a_file_is_refused_before_the_call(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    link = tmp_path / "routing.json"
    link.symlink_to(blocker / "result.json")
    with pytest.raises(InputError, match="is not a directory"):
        checked_out_path(str(link), "routing")


def test_an_out_symlink_to_a_missing_file_in_an_existing_directory_is_accepted(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    (tmp_path / "out").mkdir()
    link = tmp_path / "routing.json"
    link.symlink_to(tmp_path / "out" / "result.json")
    assert checked_out_path(str(link), "routing") == link


def test_an_out_file_that_cannot_be_written_is_refused_up_front(tmp_path, monkeypatch):
    import os

    from dokimi.commands.novelty_routing_inputs import checked_out_path

    monkeypatch.setattr(os, "access", lambda path, mode: False)
    with pytest.raises(InputError, match="not writable"):
        checked_out_path(str(tmp_path / "routing.json"), "routing")


# Real permissions, not a patched os.access: a regression to paying for every call needs the
# checks themselves to be exercised. Root ignores file modes, so these skip there.
_CANNOT_CHMOD = sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0)


@pytest.mark.skipif(_CANNOT_CHMOD, reason="file modes are not enforced here")
def test_an_existing_read_only_out_file_is_refused_before_any_call(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    out = tmp_path / "routing.json"
    out.write_text("{}", encoding="utf-8")
    out.chmod(0o444)
    try:
        with pytest.raises(InputError, match="not writable"):
            checked_out_path(str(out), "routing")
    finally:
        out.chmod(0o644)


@pytest.mark.skipif(_CANNOT_CHMOD, reason="file modes are not enforced here")
def test_a_new_out_file_in_a_read_only_directory_is_refused_before_any_call(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    folder = tmp_path / "ro"
    folder.mkdir()
    folder.chmod(0o555)
    try:
        with pytest.raises(InputError, match="not writable"):
            checked_out_path(str(folder / "routing.json"), "routing")
    finally:
        folder.chmod(0o755)


def test_a_valid_out_file_is_returned_as_a_path(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    assert checked_out_path(str(tmp_path / "routing.json"), "routing") == tmp_path / "routing.json"


def test_all_vs_all_refuses_an_out_directory_before_it_calls_the_model(tmp_path, monkeypatch):
    import dokimi.commands.novelty as novelty_cmd

    monkeypatch.setattr(novelty_cmd, "_client",
                        lambda *a, **k: pytest.fail("a model call was made before --out was checked"))
    root = tmp_path / "catalog"
    for name, text in {"perf": "profile a slow binary", "db": "tune a slow database query"}.items():
        (root / "skills" / name).mkdir(parents=True)
        (root / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {text}\n---\n\n# B\n", encoding="utf-8")
    queries = tmp_path / "queries.json"
    queries.write_text('{"perf": ["profile my slow binary"]}', encoding="utf-8")
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path), "--model", "m")
    with pytest.raises(InputError, match="is a directory"):
        args.func(args)


def test_the_estimate_prices_the_reply_the_model_sends_not_one_json_pick():
    from dokimi.novelty.routing import Candidate, selection_estimate

    field = [Candidate(name="a", description="profile a slow binary"),
             Candidate(name="b", description="tune a slow database")]
    # 360 calls: the measured run drew 23.3k output tokens for them, where one pick per call
    # (about 10 tokens) would have estimated 3.6k.
    estimate = selection_estimate(field, ["profile my slow binary"] * 360, repeats=1)
    assert estimate["n_calls"] == 360
    assert estimate["output_tokens"] == 360 * estimate["output_tokens_per_call"]
    assert estimate["output_tokens"] == pytest.approx(23_300, rel=0.05)



def test_all_vs_all_with_one_repeat_keeps_the_noise_block_and_prints_no_noise_line(
        tmp_path, capsys, monkeypatch):
    import dokimi.commands.novelty as novelty_cmd

    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: LLMClient(
        model=model, provider=provider, role=role,
        transport=lambda prompt, system: (json.dumps({"pick": 1}), None)))
    root = tmp_path / "catalog"
    for name, text in {"perf": "profile a slow binary", "db": "tune a slow database query"}.items():
        (root / "skills" / name).mkdir(parents=True)
        (root / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {text}\n---\n\n# B\n", encoding="utf-8")
    queries = tmp_path / "queries.json"
    queries.write_text('{"perf": ["profile my slow binary"]}', encoding="utf-8")
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out), "--model", "m")
    args.func(args)
    noise = json.loads(out.read_text(encoding="utf-8"))["result"]["model"]["noise"]
    assert (noise["n_repeats"], noise["mean_agreement"], noise["half_range"],
            noise["unstable_queries"]) == (1, None, None, None)
    assert "noise    " not in capsys.readouterr().out


def test_a_refused_default_out_names_the_default_and_asks_for_out(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path
    default = tmp_path / "routing.json"
    default.mkdir()
    with pytest.raises(InputError) as err:
        checked_out_path(None, "routing", default=str(default))
    message = str(err.value)
    assert message.startswith(f"the default output {default} is a directory")
    assert "pass --out <file>" in message
    assert "routing.json/routing.json" not in message


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_fifo_out_is_refused_before_the_call(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path
    fifo = tmp_path / "routing.json"
    os.mkfifo(fifo)
    with pytest.raises(InputError, match="not a regular file"):
        checked_out_path(str(fifo), "routing")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_an_out_symlink_loop_is_refused_before_the_call(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.symlink_to(b)
    b.symlink_to(a)
    with pytest.raises(InputError, match="symlink loop"):
        checked_out_path(str(a), "routing")
