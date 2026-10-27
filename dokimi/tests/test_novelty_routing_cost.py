"""The usage and cost block ``novelty routing --model`` writes and prints."""

from __future__ import annotations

import json

from dokimi.cli import build_parser


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _skill(tmp_path, name, description, body="# B\n\n## A section here\n"):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}", encoding="utf-8")
    return f"skill:{root}"


def _stub_provider(monkeypatch, usage):
    """Replace the provider call the --model path makes; every reply picks card 1."""
    from dokimi import llm

    def stub(prompt, model, **_kwargs):
        return '{"pick": 1}', usage

    monkeypatch.setattr(llm, "llm_call_with_usage", stub)


def _model_run(tmp_path):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary", "tune a slow query"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary with perf"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--model", "selector-m", "--out", str(out))
    args.func(args)
    return json.loads(out.read_text(encoding="utf-8"))


def test_a_model_run_records_its_calls_tokens_and_cost(tmp_path, monkeypatch, capsys):
    from dokimi.metrics.usage import UsageRecord

    _stub_provider(monkeypatch, UsageRecord(prompt_tokens=100, completion_tokens=5,
                                            cache_read_tokens=60, cache_write_tokens=30,
                                            cost_usd=0.002))
    payload = _model_run(tmp_path)
    assert payload["cost"]["llm_calls"] == 2
    assert payload["cost"]["input_tokens"] == 200
    assert payload["cost"]["output_tokens"] == 10
    assert payload["cost"]["cache_read_tokens"] == 120
    assert payload["cost"]["cache_write_tokens"] == 60
    assert payload["cost"]["usd"] == 0.004
    out = capsys.readouterr().out
    assert ("cost     2 calls, 200 input / 10 output tokens, cache read 120 / write 60, "
            "$0.0040") in out.splitlines()
    assert "replayed" not in out


def test_a_model_run_with_an_unpriced_call_reports_no_usd_figure(tmp_path, monkeypatch, capsys):
    from dokimi.metrics.usage import UsageRecord

    _stub_provider(monkeypatch, UsageRecord(prompt_tokens=10, completion_tokens=1))
    payload = _model_run(tmp_path)
    assert payload["cost"]["usd"] is None   # one unpriced call must not be read as free
    assert "USD not known: a call had no price or no usage" in capsys.readouterr().out


def test_a_lexical_run_spends_nothing_and_has_no_cost_block(tmp_path):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary with perf"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(out))
    args.func(args)
    assert "cost" not in json.loads(out.read_text(encoding="utf-8"))


def test_a_model_run_whose_every_call_fails_prints_no_price_blame(tmp_path, monkeypatch, capsys):
    from dokimi import llm

    def failing(prompt, model, **_kwargs):
        raise RuntimeError("provider refused")

    monkeypatch.setattr(llm, "llm_call_with_usage", failing)
    payload = _model_run(tmp_path)
    assert payload["cost"]["llm_calls"] == 0
    assert payload["cost"]["usd"] is None   # no calls: nothing known, not "free"
    out = capsys.readouterr().out
    assert "cost     0 calls\n" in out
    assert "USD not known" not in out


def test_a_call_with_no_usage_makes_the_total_unknown_not_the_priced_subset(
        tmp_path, monkeypatch, capsys):
    from dokimi import llm
    from dokimi.metrics.usage import UsageRecord

    replies = iter([None, UsageRecord(prompt_tokens=10, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(llm, "llm_call_with_usage",
                        lambda prompt, model, **_kw: ('{"pick": 1}', next(replies)))
    payload = _model_run(tmp_path)
    assert payload["cost"]["llm_calls"] == 2
    assert payload["cost"]["usd"] is None
    assert "$0.0010" not in capsys.readouterr().out


def test_replayed_calls_are_counted_apart_and_not_billed(tmp_path, monkeypatch, capsys):
    from dokimi.metrics.usage import UsageRecord

    _stub_provider(monkeypatch, UsageRecord(prompt_tokens=100, completion_tokens=5,
                                            cache_read_tokens=60, cache_write_tokens=30,
                                            cost_usd=0.002, replayed=True))
    payload = _model_run(tmp_path)
    cost = payload["cost"]
    assert (cost["llm_calls"], cost["replayed_calls"]) == (2, 2)
    assert (cost["input_tokens"], cost["output_tokens"], cost["usd"]) == (0, 0, 0.0)
    assert (cost["cache_read_tokens"], cost["cache_write_tokens"]) == (0, 0)
    assert "2 replayed from the cache and not counted" in capsys.readouterr().out
