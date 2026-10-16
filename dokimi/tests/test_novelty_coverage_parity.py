# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""No coverage figure the gate, the analyzer or the report prints differs from ``main``.

Moving the bundle record's rules into ``novelty/bundle.py`` is a refactor, and a refactor
changes no number (#4). Each case below builds a skill, runs the real ``analyze`` over it
with scripted clients, gates the artifact and renders the report; the coverage figures of
all three are compared with ``tests/fixtures/novelty_coverage_main.json``, which is this
module's ``_capture`` output on ``main`` at d223029. The module uses only interfaces that
exist on ``main``, so it runs there unchanged; to recapture, from a ``main`` checkout's
``dokimi/`` with this file and the JSON copied in::

    python -m tests.test_novelty_coverage_parity --write

Two hand-made records no writer has produced are pinned elsewhere, not here: a partial
count block (``test_novelty_bundle_guards``: the report says the counts are missing
instead of "nothing measurable sits outside it") and excluded rows without a ``kind``
(every row has carried one since bundles were first recorded; ``main``'s gate and report
classified such a row differently from each other, and now both classify it by kind).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from dokimi.gate.novelty_gate import evaluate_novelty
from dokimi.novelty.analyze import AnalyzeConfig, analyze
from dokimi.novelty.bundle import load_bundle
from dokimi.novelty.report import _render_bundle
from tests.test_novelty_analyze import SKILL, _clients
from tests.test_novelty_gate import _artifact

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "novelty_coverage_main.json"
RECORD_FIELDS = ("include_resources", "n_files", "entry_point_words", "resource_words_measured",
                 "resource_words_on_disk", "outside_share", "measurable_outside_share",
                 "policy_excluded_words", "unmeasured_words", "unreadable_files")
PNG = b"\x89PNG\r\n\x1a\n"


def _words(n: int, tag: str) -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def _front(name: str) -> str:
    return f"---\nname: {name}\ndescription: d\n---\n"


ENTRY = SKILL + "\n" + _words(60, "pad") + "\n"
# name -> (SKILL.md, {path: text | bytes | "->symlink target"}, load_bundle kwargs).
LIVE: dict[str, tuple[str, dict, dict]] = {
    # gate_example.py in the #44 thread: 400 entry words, a 500-word reference and 1,000
    # words of evals; the bundle run adds a 5,000-word reference over the per-file budget.
    "demo/entry": (_front("demo-skill") + _words(400, "e"),
                   {"references/ref.md": _words(500, "r"), "evals/evals.md": _words(1000, "v")},
                   {}),
    "demo/bundle": (_front("demo-skill") + _words(400, "e"),
                    {"references/ref.md": _words(500, "r"), "evals/evals.md": _words(1000, "v"),
                     "references/big.md": _words(5000, "b")},
                    {"include_resources": True}),
    "demo/entry+big": (_front("demo-skill") + _words(400, "e"),
                       {"references/ref.md": _words(500, "r"),
                        "evals/evals.md": _words(1000, "v"),
                        "references/big.md": _words(5000, "b")},
                       {}),
    "evals-only/entry": (ENTRY, {"evals/cases.md": _words(500, "v")}, {}),
    "evals-only/bundle": (ENTRY, {"evals/cases.md": _words(500, "v")},
                          {"include_resources": True}),
    **{f"mixed/{mode}": (ENTRY, {"references/r.md": _words(50, "r"),
                                 "references/big.md": _words(30, "b"),
                                 "evals/cases.md": _words(80, "v"), "logo.png": PNG,
                                 "references/linked.md": "->references/r.md"}, kwargs)
       for mode, kwargs in (("entry", {}),
                            ("bundle", {"include_resources": True, "max_file_words": 40}),
                            ("bundle-roles", {"include_resources": True,
                                              "roles": ("references",)}))},
    **{f"unreadable/{mode}": (ENTRY, {"references/ok.md": _words(40, "o"),
                                      "references/bad.md": b"\xff\xfe\x00bad \xc3\x28"}, kwargs)
       for mode, kwargs in (("entry", {}), ("bundle", {"include_resources": True}))},
    # 12,501 of 25,000 words unread: 0.50004 is over the 0.5 ceiling, 0.5 once rounded.
    "boundary/entry": (_front("boundary") + _words(12499, "e"),
                       {"references/r.md": _words(12501, "r")}, {}),
    "plain/entry": (ENTRY, {}, {}),
}

_STORED_ROWS = [
    {"path": "references/a.md", "role": "references", "words": 400,
     "reason": "over the 4000-word per-file budget", "kind": "budget"},
    {"path": "evals/e.md", "role": "evals", "words": 300,
     "reason": "role evals not measured by default", "kind": "policy"},
    {"path": "img.png", "role": "assets", "reason": "not text", "kind": "not_text"},
    {"path": "link.md", "role": "references", "reason": "symlink", "kind": "symlink"},
]
# Records read back from disk, with no live bundle behind them.
STORED: dict[str, dict] = {
    "stored/no-bundle": {},
    "stored/with-kinds": {
        "include_resources": True, "n_files": 2, "entry_point_words": 100,
        "resource_words_on_disk": 900, "resource_words_measured": 200,
        "policy_excluded_words": 300, "unreadable_files": 0, "outside_share": 0.9,
        "measurable_outside_share": 0.8571, "excluded": _STORED_ROWS},
}


def _skill(root: Path, entry: str, files: dict) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(entry + "\n", encoding="utf-8")
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        elif content.startswith("->"):
            path.symlink_to(root / content[2:])
        else:
            path.write_text(content + "\n", encoding="utf-8")
    return root


def _clients_that_cite_real_spans():
    """``_clients()``, with the extractor's rows cut to the spans the prompt lists.

    The shared script cites ``s0002`` and ``s0003``; a one-sentence skill has no such span.
    ``main`` at d223029 dropped those rows silently; since #40 a reply none of whose rows is
    an atom of the document is an error. The parity cases measure coverage, not extraction,
    so the rows ``main`` kept are the rows this module keeps, whichever side of #40 it runs on.
    """
    extractor, answerer, judge = _clients()
    inner = extractor.transport

    def transport(prompt, system=None):
        reply, error = inner(prompt, system)
        if "Extract the checkable knowledge" not in prompt:
            return reply, error
        listed = set(re.findall(r"^\[(s\d+)\]", prompt, flags=re.M))
        rows = [row for row in json.loads(reply) if set(row["span_ids"]) <= listed]
        return json.dumps(rows), error

    extractor.transport = transport
    return extractor, answerer, judge


def _gate(artifact: dict) -> dict:
    check = next(c for c in evaluate_novelty(artifact).checks if c.name == "artifact_coverage")
    return {"passed": check.passed, "value": check.value, "severity": check.severity,
            "measured": check.measured, "message": check.message}


def _capture(name: str, tmp_path: Path) -> dict:
    """Every coverage figure the three surfaces print for one case."""
    if name in STORED:
        artifact = _artifact(bundle=STORED[name])
        return {"gate": _gate(artifact), "report": "\n".join(_render_bundle(artifact))}
    entry, files, kwargs = LIVE[name]
    root = _skill(tmp_path / name.replace("/", "-"), entry, files)
    bundle = load_bundle(f"skill:{root}", **kwargs)
    config = AnalyzeConfig(artifact_ref=f"skill:{root}", artifact_kind=bundle.kind,
                           name=bundle.name, description=bundle.description,
                           frontmatter=bundle.frontmatter, concurrency=1)
    extractor, answerer, judge = _clients_that_cite_real_spans()
    artifact = analyze(bundle.text, config, extractor=extractor, answerer=answerer,
                       judge=judge, artifact_bundle=bundle).artifact
    return {
        "record": {key: artifact["bundle"].get(key) for key in RECORD_FIELDS},
        "gate": _gate(artifact),
        "analyzer_warnings": [w for w in artifact["warnings"] if "bundled files" in w],
        "report": "\n".join(_render_bundle(artifact)),
    }


@pytest.mark.parametrize("name", [*LIVE, *STORED])
def test_no_coverage_figure_differs_from_main(name, tmp_path):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert _capture(name, tmp_path) == golden[name]


def test_the_golden_cases_are_the_ones_this_module_runs():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(golden) == sorted([*LIVE, *STORED])


if __name__ == "__main__" and sys.argv[1:] == ["--write"]:
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    captured = {name: _capture(name, tmp) for name in [*LIVE, *STORED]}
    GOLDEN.write_text(json.dumps(captured, indent=1, sort_keys=True, ensure_ascii=False)
                      + "\n", encoding="utf-8")
    print(f"{len(captured)} cases -> {GOLDEN}")
