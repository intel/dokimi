# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The author's actual path, end to end through the CLI, with every model call stubbed.

The unit tests cover each command in isolation and the library-level `analyze`. The thing
nobody was exercising is the *sequence* an author runs: can it be selected, what will it
cost, measure it once, reuse the atoms per model, read what carried the knowledge, shorten,
re-render. A break between two steps -- a file one command writes and the next cannot read
-- shows up here and nowhere else.
"""

from __future__ import annotations

import json
from decimal import ROUND_FLOOR, Decimal
import re
import shlex
from pathlib import Path

import pytest
from test_novelty_analyze import SKILL, _scripted

from dokimi.cli import build_parser
from dokimi.novelty import client as client_mod


@pytest.fixture
def stub_models(monkeypatch):
    """Make every client the commands build answer from the scripted transport."""
    transport = _scripted("informed")
    real = client_mod.LLMClient

    def factory(**kwargs):
        kwargs.setdefault("transport", transport)
        return real(**kwargs)

    monkeypatch.setattr(client_mod, "LLMClient", factory)
    return transport


@pytest.fixture
def skill(tmp_path):
    root = tmp_path / "accel-tuning"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(SKILL, encoding="utf-8")
    # Deliberately larger than the body: the entry-point-only path must then say so.
    (root / "references" / "tuning.md").write_text(
        "# Tuning\n\nSet `--threads` to the core count.\n" + ("filler word " * 200),
        encoding="utf-8")
    return root


def _run(*argv):
    args = build_parser().parse_args(list(argv))
    args.func(args)
    return args


@pytest.mark.outcome("cjm-novelty-authoring-path")
def test_the_authoring_path_runs_end_to_end(tmp_path, skill, stub_models, capsys):
    rival = tmp_path / "rival"
    rival.mkdir()
    (rival / "SKILL.md").write_text(
        "---\nname: rival\ndescription: Tune a database query plan\n---\n\n# R\n",
        encoding="utf-8")
    queries = tmp_path / "queries.json"
    queries.write_text(json.dumps([
        "how do I switch the accelerator on for a training run",
        "which driver versions refuse the accelerator",
    ]), encoding="utf-8")

    # 1. Can it be selected at all? Free, no model.
    routing_out = tmp_path / "routing.json"
    _run("novelty", "routing", "--artifact", f"skill:{skill}",
         "--competitors", f"skill:{rival}", "--queries", str(queries),
         "--out", str(routing_out))
    routing = json.loads(routing_out.read_text(encoding="utf-8"))
    assert routing["lexical"]["n_queries"] == 2
    assert routing["queries_self_derived"] is False
    capsys.readouterr()                      # drop step 1's chatter

    # 2. What will the measurement cost? Still no model. The walkthrough's own form.
    _run("novelty", "analyze", "--artifact", f"skill:{skill}",
         "--tier", "study", "--estimate-only")
    printed = capsys.readouterr().out
    estimate = json.loads(printed[printed.index("{"):])
    assert estimate["estimate"]["total_calls"] > 0
    assert estimate["tier"]["name"] == "study"

    # 3. Extract the answer key once, over the whole bundle.
    atoms_out = tmp_path / "atoms.json"
    _run("novelty", "atoms", "--artifact", f"skill:{skill}", "--include-resources",
         "--extract-model", "extractor-m", "--out", str(atoms_out))
    atoms = json.loads(atoms_out.read_text(encoding="utf-8"))
    assert atoms["atoms"] and atoms["bundle"]["include_resources"] is True

    # 4. Probe it per model, reusing that key. Two models, so two cells.
    probe_files = []
    for model in ("model-a", "model-b"):
        out = tmp_path / f"{model}.json"
        _run("novelty", "probe", "--artifact", f"skill:{skill}", "--atoms", str(atoms_out),
             "--model", model, "--judge-model", "judge-m", "--out", str(out))
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["runs"] and payload["model"] == model
        probe_files.append(str(out))

    # 5. Which probes measured anything? Two cells is below the discrimination floor, and
    #    the command must say so rather than print a number.
    items_out = tmp_path / "items.json"
    _run("novelty", "items", "--probes", *probe_files, "--out", str(items_out))
    items = json.loads(items_out.read_text(encoding="utf-8"))
    assert items["discrimination"]["lenient"]["measured"] is False
    assert "at least 3" in items["discrimination"]["lenient"]["reason"]

    # 6. Shorten. The atoms came from a bundle, so this is restricted to the entry point.
    shortened = tmp_path / "short" / "SKILL.md"
    _run("novelty", "shorten", "--artifact", f"skill:{skill}", "--atoms", str(atoms_out),
         "--lexical-only", "--out", str(shortened))
    captured = capsys.readouterr()
    assert "bundle spans" in captured.err
    # Unconditionally: the handoff this test exists to pin is that step 7 writes a file
    # step 8 can read, and `if shortened.exists()` would have passed if it wrote nothing.
    assert shortened.exists(), captured.out
    assert shortened.read_text(encoding="utf-8").startswith("---")

    # 7. The full run, and the views and artifact it writes.
    out_dir = tmp_path / "run"
    _run("novelty", "analyze", "--artifact", f"skill:{skill}", "--tier", "study",
         "--model", "model-a", "--judge-model", "judge-m", "--out-dir", str(out_dir))
    novelty_json = out_dir / "novelty.json"
    artifact = json.loads(novelty_json.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "novelty.v1"
    assert artifact["run_config"]["tier"]["name"] == "study"
    assert (out_dir / "novelty.md").exists()
    # Step 6 of the walkthrough is about this file.
    view = out_dir / "annotated.html"
    assert view.exists() and view.stat().st_size > 0

    # 8. Re-render the annotated view from what was saved.
    view_out = tmp_path / "view.html"
    _run("novelty", "view", "--novelty", str(novelty_json), "--atoms", str(atoms_out),
         "--out", str(view_out))
    assert view_out.exists() and view_out.stat().st_size > 0


def test_a_cheap_tier_run_writes_its_forfeits_into_the_artifact(tmp_path, skill, stub_models):
    out_dir = tmp_path / "triage"
    _run("novelty", "analyze", "--artifact", f"skill:{skill}", "--tier", "triage",
         "--model", "model-a", "--judge-model", "judge-m", "--out-dir", str(out_dir))
    artifact = json.loads((out_dir / "novelty.json").read_text(encoding="utf-8"))
    assert artifact["run_config"]["tier"]["name"] == "triage"
    assert any(w.startswith("tier `triage` forfeits:") for w in artifact["warnings"])
    # The cheap run measured the entry point only, and the artifact says how much it skipped.
    assert artifact["bundle"]["include_resources"] is False
    assert any("--include-resources" in w for w in artifact["warnings"])


def test_the_stdout_headline_says_how_much_of_the_artifact_was_read(tmp_path, skill,
                                                                     stub_models, capsys):
    # The fixture's reference is larger than the body: an entry-point run reads under half.
    entry_out = tmp_path / "entry"
    _run("novelty", "analyze", "--artifact", f"skill:{skill}", "--tier", "triage",
         "--model", "model-a", "--judge-model", "judge-m", "--out-dir", str(entry_out))
    printed = capsys.readouterr().out
    measured = json.loads((entry_out / "novelty.json").read_text(encoding="utf-8"))[
        "bundle"]["measured_share"]
    assert measured < 0.5
    assert f"coverage: {int(Decimal(str(measured)).scaleb(2).to_integral_value(ROUND_FLOOR))}% of the artifact's readable words were measured " \
           "(entry point only)" in printed
    assert "WARNING: only" in printed and "pass --include-resources" in printed

    # A bundle run that reads everything it can says so, and does not warn.
    bundle_out = tmp_path / "bundle"
    _run("novelty", "analyze", "--artifact", f"skill:{skill}", "--tier", "triage",
         "--include-resources", "--model", "model-a", "--judge-model", "judge-m",
         "--out-dir", str(bundle_out))
    printed = capsys.readouterr().out
    assert "coverage: 100% of the artifact's readable words were measured\n" in printed
    assert "WARNING: only" not in printed


@pytest.mark.outcome("cjm-novelty-stale-inventory")
def test_probing_atoms_from_a_changed_artifact_is_refused(tmp_path, skill, stub_models):
    atoms_out = tmp_path / "atoms.json"
    _run("novelty", "atoms", "--artifact", f"skill:{skill}", "--include-resources",
         "--extract-model", "extractor-m", "--out", str(atoms_out))
    (skill / "references" / "new.md").write_text("Another reference.\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="files changed") as exc:
        _run("novelty", "probe", "--artifact", f"skill:{skill}", "--atoms", str(atoms_out),
             "--model", "model-a", "--judge-model", "judge-m",
             "--out", str(tmp_path / "p.json"))
    assert exc.value.code == 2  # an atoms file this artifact cannot use: an input refusal


@pytest.mark.parametrize("command", ["analyze", "probe"])
def test_a_judge_that_is_the_answering_model_is_refused_with_exit_2(command, tmp_path, skill, stub_models, capsys):
    atoms_out = tmp_path / "atoms.json"
    _run("novelty", "atoms", "--artifact", f"skill:{skill}", "--extract-model", "extractor-m",
         "--out", str(atoms_out))
    extra = ["--atoms", str(atoms_out), "--out", str(tmp_path / "p.json")] if command == "probe" else \
        ["--out-dir", str(tmp_path / "out")]
    with pytest.raises(SystemExit) as exc:
        _run("novelty", command, "--artifact", f"skill:{skill}", "--model", "model-a",
             "--judge-model", "model-a", *extra)
    assert exc.value.code == 2
    assert "is the answering model" in capsys.readouterr().err


def test_probe_refuses_an_unknown_arm_before_any_model_call(tmp_path, skill, stub_models, monkeypatch, capsys):
    atoms_out = tmp_path / "atoms.json"
    _run("novelty", "atoms", "--artifact", f"skill:{skill}", "--extract-model", "extractor-m",
         "--out", str(atoms_out))
    # Count the prompts the probe sends from here on: the atoms step above is not part of the refusal.
    prompts = []
    scripted = client_mod.LLMClient  # the factory stub_models installed

    def recording_client(**kwargs):
        def transport(prompt, system):
            prompts.append(prompt)
            return stub_models(prompt, system)
        return scripted(**{**kwargs, "transport": transport})

    monkeypatch.setattr(client_mod, "LLMClient", recording_client)
    with pytest.raises(SystemExit) as exc:
        _run("novelty", "probe", "--artifact", f"skill:{skill}", "--atoms", str(atoms_out),
             "--model", "model-a", "--judge-model", "judge-m", "--arms", "no_artifact,full,no-such-arm",
             "--out", str(tmp_path / "p.json"))
    assert exc.value.code == 2
    assert "unknown novelty arms" in capsys.readouterr().err
    assert prompts == [], "a model was called before the arm check refused"


@pytest.mark.outcome("cjm-novelty-remeasure")
def test_the_re_measure_step_probes_a_shortened_entry_point(tmp_path, skill, stub_models):
    """Step 8 of the walkthrough, which the end-to-end test stops short of.

    The shortened `SKILL.md` goes into a copy of the skill directory, so the bundled files
    behind the answer key are the same bytes; `probe` then accepts the edited entry point
    and the two runs share one answer key, which is what makes them comparable.
    """
    import shutil

    atoms_out = tmp_path / "atoms.json"
    _run("novelty", "atoms", "--artifact", f"skill:{skill}", "--include-resources",
         "--extract-model", "extractor-m", "--out", str(atoms_out))

    shortened = tmp_path / "shortened"
    shutil.copytree(skill, shortened)
    body = (shortened / "SKILL.md").read_text(encoding="utf-8")
    (shortened / "SKILL.md").write_text(
        body.replace("## Background\n\nThe project began as a research prototype with "
                     "many contributors.\n", ""), encoding="utf-8")
    assert "Background" not in (shortened / "SKILL.md").read_text(encoding="utf-8")

    out = tmp_path / "after.json"
    _run("novelty", "probe", "--artifact", f"skill:{shortened}", "--atoms", str(atoms_out),
         "--model", "model-a", "--judge-model", "judge-m", "--out", str(out))
    after = json.loads(out.read_text(encoding="utf-8"))
    assert after["runs"]
    assert after["paired"]["lenient"]["n_paired"] > 0


def _documented_novelty_commands():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "skill-novelty-walkthrough.md")
    text = doc.read_text(encoding="utf-8")
    commands = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        for line in re.sub(r"\\s*\n", " ", block).splitlines():
            if line.strip().startswith("python cli.py novelty"):
                line = re.sub(r"<[^>]+>", "X", line).replace(" ...", "")
                commands.append(shlex.split(line)[2:])
    return commands


def test_every_documented_novelty_command_parses():
    """A flag renamed or removed in the code, or misspelled in the doc, goes red here."""
    commands = _documented_novelty_commands()
    assert len(commands) >= 8
    for argv in commands:
        build_parser().parse_args(argv)


@pytest.mark.outcome("cjm-novelty-estimate")
def test_the_estimate_prices_the_bundled_files_when_they_are_included(tmp_path, capsys):
    skill = tmp_path / "s"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: s\ndescription: d\n---\n\n# H\n\nUse the tool. See references/guide.md.\n",
        encoding="utf-8")
    (skill / "references" / "guide.md").write_text(
        "\n\n".join(f"Fact {i}: the tool must set flag {i} before it runs." for i in range(30)),
        encoding="utf-8")

    def estimate(*extra):
        args = build_parser().parse_args(
            ["novelty", "analyze", "--artifact", f"skill:{skill / 'SKILL.md'}", "--estimate-only", *extra])
        args.func(args)
        return json.loads(capsys.readouterr().out)

    base, bundled = estimate(), estimate("--include-resources")
    assert bundled["spans"] > base["spans"]
    assert bundled["estimate"]["total_calls"] > base["estimate"]["total_calls"]


def test_documented_estimate_flags_match_the_run_it_prices():
    """The walkthrough's estimate commands carry the flags of the run they price (doc reference)."""
    estimates = [c for c in _documented_novelty_commands() if "--estimate-only" in c]
    assert estimates and all("--include-resources" in c for c in estimates)
