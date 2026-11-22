# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Turn stored dokimi evidence into a ranked queue of fixes an agent can pick up.

A prototype (docs/proposals/lightsage-inspired-features.md at the repository root). Each
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

    @property
    def source_key(self) -> str:
        ident = self.identity or self.title
        return hashlib.sha256(f"{self.kind}|{self.target}|{ident}".encode()).hexdigest()[:16]

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
        return {**data, "source_key": self.source_key, "quadrant": self.quadrant,
                "execution_prompt": self.execution_prompt()}


def _share(count: int, total: int) -> float:
    return round(count / total, 3) if total else 0.0


def from_novelty(artifact: dict, *, scoring: str = "lenient") -> list[Opportunity]:
    """Degraded, still-missing and redundant atoms of one novelty artifact, as fixes."""
    paired = artifact.get("paired", {}).get(scoring, {})
    total = paired.get("n_paired", 0)
    target = artifact.get("artifact", {}).get("ref", "?")
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
        n = paired.get(outcome, 0)
        if n:
            found.append(Opportunity(
                kind=kind, title=f"{n} {outcome.replace('_', ' ')} atoms for {model}",
                why=why + ".", next_action=action, target=target,
                impact=min(1.0, round(weight * _share(n, total), 3)), effort=effort,
                evidence={"outcome": outcome, "n": n, "n_paired": total, "scoring": scoring},
                identity=f"{model}|{outcome}|{scoring}",
            ))
    return found


def from_repo_static(doc: dict, *, target: str = "repository") -> list[Opportunity]:
    """Failed and unresolved repository claims, grouped per file."""
    claims = doc.get("claims", [])
    bad: dict[str, list[dict]] = {}
    for claim in claims:
        if claim.get("status", "").split("_")[0] in ("fail", "unresolved"):
            if "file" not in claim or "line" not in claim:
                raise InputError(f"{target}: a failing claim has no file or line")
            bad.setdefault(claim["file"], []).append(claim)
    return [Opportunity(
        kind="repo-claim", title=f"{len(rows)} failing claims in {path}",
        why="guidance that names a missing command, path or link misleads the agent.",
        next_action="Fix or delete each claim at lines "
                    + ", ".join(str(r["line"]) for r in rows) + ".",
        target=f"{target}:{path}", impact=_share(len(rows), len(claims)), effort="low",
        evidence={"lines": [r["line"] for r in rows], "statuses": sorted({r["status"] for r in rows})},
        identity=path,
    ) for path, rows in sorted(bad.items())]


def from_catalog_eval(doc: dict) -> list[Opportunity]:
    """Error and warning findings of ``catalog check``, grouped per skill and code."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for skill in doc.get("skills", []):
        for f in skill.get("checks", {}).get("findings", []):
            if f.get("level") in ("error", "warn"):
                groups.setdefault((skill.get("path", skill.get("name", "?")), f["code"]), []).append(f)
    total = sum(len(v) for v in groups.values())
    return [Opportunity(
        kind="catalog-check", title=f"{len(rows)} {code} findings in {path}",
        why=rows[0]["message"], next_action=f"Fix the {code} findings: " + "; ".join(
            sorted({r.get("script", r["path"]) for r in rows})) + ".",
        target=path, impact=round(_share(len(rows), total) / 2
                                  + (0.5 if any(r["level"] == "error" for r in rows) else 0), 3),
        effort="low", evidence={"code": code, "levels": sorted({r["level"] for r in rows})},
        identity=code,
    ) for (path, code), rows in sorted(groups.items())]


def from_judge_validity(doc: dict) -> list[Opportunity]:
    """A judge that fails or cannot settle its validity gate: every score it gave is in doubt."""
    s = doc.get("summary", {})
    judge = s.get("judge", "judge")
    bad = sorted(set(s.get("inconclusive_variants", [])) | set(s.get("unmeasured_variants", [])))
    if s.get("passed") and s.get("conclusive"):
        return []
    failed = not s.get("passed")
    return [Opportunity(
        kind="judge-validity",
        title=f"judge {judge} {'fails' if failed else 'is inconclusive on'} its validity gate",
        why=f"accuracy {s.get('gate_accuracy', 0):.2f} against a minimum of "
            f"{s.get('min_accuracy', 0)}; scores from this judge are not evidence yet.",
        next_action="Change the judge model or prompt, or add pairs"
                    + (f" for {', '.join(bad)}" if bad else "") + ", then re-run judge validity.",
        target=str(judge), impact=1.0 if failed else 0.5, effort="medium",
        evidence={k: s.get(k) for k in ("n_pairs", "gate_accuracy", "accuracy_ci", "passed",
                                       "conclusive")},
        identity="gate",
    )]


def from_llms_txt(findings: list[Finding], *, target: str = "llms.txt") -> list[Opportunity]:
    """Each llms.txt finding as a quick technical fix; a failed check counts more."""
    return [Opportunity(
        kind="discovery", title=f.message, why="agents read llms.txt before choosing a tool.",
        next_action=f.fix, target=target, impact=0.4 if f.severity == "fail" else 0.2,
        effort="low", evidence={"check": f.check, "line": f.line},
        identity=f.message if f.check == "links" else f.check,
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
