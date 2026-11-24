# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Turn stored dokimi evidence into a ranked queue of fixes an agent can pick up.

Each
opportunity carries impact, effort, a quadrant, a stable ``source_key`` (the same problem
keeps its key across runs) and an ``execution_prompt`` that a coding agent can act on.
Inputs are dicts already on disk: a novelty artifact, a repo_static output, llms.txt findings.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from dokimi.llms_txt import Finding
from dokimi.utils import InputError

EFFORTS = ("low", "medium", "high")
QUADRANTS = ("act_now", "plan", "if_capacity", "deprioritize")


@dataclass(frozen=True)
class Opportunity:
    """One ranked fix: what is wrong, why it matters, the next action and the evidence."""

    kind: str
    title: str
    why: str
    next_action: str
    target: str
    impact: float  # 0..1, share of the measured signal the fix addresses
    effort: str
    evidence: dict = field(default_factory=dict)
    identity: str = ""  # what stays fixed across runs; the title (with counts) when empty
    identity_target: str | None = None

    @property
    def source_key(self) -> str:
        ident = self.identity or self.title
        return hashlib.sha256(f"{self.kind}|{self.identity_target or self.target}|{ident}".encode()).hexdigest()[:16]

    @property
    def quadrant(self) -> str:
        high = self.impact >= 0.2
        cheap = self.effort == "low"
        return QUADRANTS[(0 if cheap else 1) if high else (2 if cheap else 3)]

    def execution_prompt(self) -> str:
        return (
            f"Fix one dokimi finding in {self.target}.\n"
            f"Problem: {self.title}.\nWhy it matters: {self.why}\n"
            f"Do: {self.next_action}\n"
            "Make the smallest change, follow the file's conventions, then re-run the dokimi "
            "check that produced this finding and report its new result."
        )

    def as_dict(self) -> dict:
        data = asdict(self)
        del data["identity"]
        del data["identity_target"]
        return {**data, "source_key": self.source_key, "quadrant": self.quadrant,
                "execution_prompt": self.execution_prompt()}


def _share(count: int, total: int) -> float:
    return round(count / total, 3) if total else 0.0


def from_novelty(artifact: dict, *, scoring: str = "lenient") -> list[Opportunity]:
    """Degraded, still-missing and redundant atoms of one novelty artifact, as fixes."""
    scores = artifact.get("paired")
    paired = scores.get(scoring) if isinstance(scores, dict) else None
    if not isinstance(paired, dict):
        raise InputError(f"novelty paired.{scoring} must be an object")
    total = paired.get("n_paired")
    if type(total) is not int or total < 0:
        raise InputError("novelty n_paired must be a nonnegative integer")
    metadata = artifact.get("artifact")
    target = metadata.get("ref") if isinstance(metadata, dict) else None
    if not isinstance(target, str) or not target.strip():
        raise InputError("novelty artifact.ref must be a nonempty string")
    model = artifact.get("model", "?")
    plans = (
        ("degraded", "high-severity", "the context makes the model wrong where it was right",
         "Find the degrading claims in the novelty report and correct or remove them.", "medium", 2.0),
        ("still_missing", "content", "the model is wrong with and without the context",
         "Rewrite the claims the model still misses so they are explicit and checkable.", "medium", 1.0),
        ("redundant", "shortening", "tokens spent on what the model already knows",
         "Try the novelty shortening candidates and re-measure before cutting.", "low", 0.5),
    )
    found = []
    for outcome, kind, why, action, effort, weight in plans:
        rate = paired.get(outcome, 0)
        if type(rate) not in (float, int) or not 0 <= rate <= 1:
            raise InputError(f"novelty {outcome} must be a rate between 0 and 1")
        counts = paired.get("counts", {})
        if not isinstance(counts, dict):
            raise InputError("novelty counts must be an object")
        count = counts.get(outcome, round(rate * total))
        if type(count) is not int or not 0 <= count <= total:
            raise InputError(f"novelty {outcome} count must be between 0 and n_paired")
        if rate:
            found.append(Opportunity(
                kind=kind, title=f"{count} {outcome.replace('_', ' ')} paired probes for {model}",
                why=why + ".", next_action=action, target=target,
                impact=min(1.0, round(weight * rate, 3)), effort=effort,
                evidence={"outcome": outcome, "n": count, "rate": rate, "n_paired": total, "scoring": scoring},
                identity=f"{model}|{outcome}|{scoring}",
            ))
    return found


def from_repo_static(doc: dict, *, target: str = "repository", repository: str | None = None) -> list[Opportunity]:
    """Failed and unresolved repository claims, grouped per file."""
    claims = doc.get("claims", [])
    bad: dict[tuple[str, str], list[dict]] = {}
    for claim in claims:
        status = claim.get("status", "").split("_")[0]
        if status in ("fail", "unresolved"):
            if "file" not in claim or "line" not in claim:
                raise InputError(f"{target}: a failing claim has no file or line")
            bad.setdefault((claim["file"], status), []).append(claim)
    return [Opportunity(
        kind="repo-claim", title=f"{len(rows)} {'failing' if status == 'fail' else 'unresolved'} claims in {path}",
        why="guidance that names a missing command, path or link misleads the agent.",
        next_action=("Fix or delete each claim at lines " if status == "fail" else
                     "Verify each unresolved claim at lines ") + ", ".join(str(r["line"]) for r in rows) + ".",
        target=f"{target}:{path}", impact=_share(len(rows), len(claims)), effort="low",
        evidence={"lines": [r["line"] for r in rows], "statuses": sorted({r["status"] for r in rows})},
        identity=f"{path}|{status}", identity_target=repository,
    ) for (path, status), rows in sorted(bad.items())]


def from_llms_txt(findings: list[Finding], *, target: str = "llms.txt") -> list[Opportunity]:
    """Each llms.txt finding as a quick technical fix; a failed check counts more."""
    return [Opportunity(
        kind="discovery", title=f.message, why="agents read llms.txt before choosing a tool.",
        next_action=f.fix, target=target, impact=0.4 if f.severity == "fail" else 0.2,
        effort="low", evidence={"check": f.check, "line": f.line},
        identity=f.identity or (f.message if f.check == "links" else f.check),
    ) for f in findings]


def rank(items: list[Opportunity]) -> list[dict]:
    """Quadrant first, then impact, then the cheaper effort; ``rank`` numbers from 1."""
    ordered = sorted(items, key=lambda o: (QUADRANTS.index(o.quadrant), -o.impact,
                                           EFFORTS.index(o.effort), o.source_key))
    return [{"rank": i, **o.as_dict()} for i, o in enumerate(ordered, 1)]


def _cell(text: str) -> str:
    return text.replace("|", r"\|").replace("\n", " ")


def render_markdown(ranked: list[dict], *, top: int = 10) -> str:
    """A short table for a PR comment or a terminal."""
    rows = ["| # | quadrant | impact | effort | kind | title | target |", "|---|---|---|---|---|---|---|"]
    rows += [f"| {r['rank']} | {r['quadrant']} | {r['impact']} | {r['effort']} | {r['kind']} "
             f"| {_cell(r['title'])} | {_cell(r['target'])} |" for r in ranked[:top]]
    return "\n".join(rows) + "\n"
