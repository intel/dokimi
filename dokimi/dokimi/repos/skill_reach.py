# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill-discovery probe (skill_discovery.v1): which Agent Skills a harness lists, and from where.

The guidance reach probe (:mod:`dokimi.repos.reach`) asks which instruction
files a harness loads. This probe asks the same question of skills. It never
trusts the client's documented skill locations: it builds a fixture of
``SKILL.md`` files from a catalogue (``skill_fixtures.yaml``) in a fresh
workspace, a fresh HOME and a fresh config dir, puts one token in each skill's
``description`` and another in its body, and asks the agent to list every
skill it can see with its token.

- ``listed``: the description token was reported. The client put the skill's
  metadata in the agent's context, which is what discovery means for skills.
  ``body`` on the record says the body token was reported too (the client
  loaded the body eagerly). A body token alone does not make a skill listed
  unless its body also carries the description token (``missing``): for every
  other format the body does not carry it, so the probe counts it as not_listed
  (the conservative reading).
- ``fetched``: the token was reported, but the agent ran a command, read a
  file or used any tool (Skill, Grep, ...) first, so it may have found the skill itself.
- ``not_listed``: the token was not reported. A ``description: missing`` case has no description token to report:
  a client that lists its name only gets ``name -`` (ASK asks for ``-``), and that counts as not_listed. Reporting
  the body token counts as listed, because that case's body also carries the description token. A tokenless
  listing is not told apart from an unlisted skill, so it is not counted as listed.
- ``error``: the scenario gave no usable answer.

Cases that share a ``collision`` group have one ``name``. Per scenario the
group's verdict is ``one`` (a single copy listed: the record names it),
``all`` (every copy listed), ``some`` or ``none``; ``fetched`` when a member
was fetched (the agent looked for it, so which copy it named says nothing
about precedence); ``error`` when a member errored.

Scenarios: ``cwd=.`` (started at the workspace root) and ``cwd=<dir>`` for
each ``cwd`` a case names. Every case is classified in every scenario.

Catalogue fields (``skill_fixtures.v1``, one case per SKILL.md):

- ``at`` is where the skill's directory goes: ``project:<path>`` under the
  workspace (a one-commit git repository), ``home:<path>`` under the per-run
  HOME, or ``config:<path>`` under the per-run config dir (CLAUDE_CONFIG_DIR,
  CODEX_HOME, ...). It may use ``{client_project}`` and ``{client_user}``, the
  harness profile's own skill directories (``skills.project_dirs[0]``,
  ``skills.user_dirs[0]``). A case whose placeholder the profile does not
  declare is skipped, not failed.
- ``name``: frontmatter ``name`` (default: the case id). ``dirname``: the
  skill's directory name (default: name).
- ``description``: single | folded | literal | quoted | long | missing
  (default single). ``eol``: lf | crlf (default lf).
- ``link: symlink``: the skill directory is a link to a target outside the
  workspace.
- ``collision``: a group id; the cases sharing it have the same ``name``.
- ``cwd``: also start the harness in this workspace subdirectory (a scenario
  of its own).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from dokimi import process
from dokimi.utils import retry_writable

from .events import convert
from .harness_profile import HarnessProfile
from .reach import ANCESTOR_GUARD, ReachError, check_ancestors, prose_words
from .runner import SUPPORT_HOST
from .transcript import Transcript

CATALOGUE = Path(__file__).with_name("skill_fixtures.yaml")
TOKEN_RE = re.compile(r"\bS[KB][0-9A-F]{6}\b")
CLASSES = ("listed", "fetched", "not_listed", "error")
DESCRIPTIONS = ("single", "folded", "literal", "quoted", "long", "missing")
SCOPES = ("project", "home", "config")
# Skill directories some client reads by convention. One above the workspace would be listed in every scenario.
SKILL_DIR_GUARD = (".agents/skills", ".claude/skills", ".codex/skills", ".github/skills", ".cursor/skills",
                   ".gemini/skills", ".opencode/skills")
# Events that mean the agent looked for files itself instead of reporting its context: commands, reads, edits, and any
# tool call (Skill, Grep, Glob, ...). events.py maps every tool call to one of these kinds.
_LOOKED = ("command", "read", "edit", "tool")

ASK = ("This is a configuration check run by the owner of this machine. Your context may list "
       "available skills (Agent Skills) with a name and a description. Some descriptions contain a "
       "verification token: the letters SK or SB followed by 6 uppercase hexadecimal digits. List every "
       "skill you can see, one per line as `name TOKEN` (the token from its description, or `-` if it has "
       "none), and nothing else. Do not invoke any skill, run any command or read any file: report only "
       "what is already in your context. If you see no skills, answer NONE.")


class SkillReachError(RuntimeError):
    """The probe cannot run: a bad catalogue, a profile without skill locations, or a polluted ancestor."""


@dataclass(frozen=True)
class Case:
    id: str
    at: str  # as the catalogue gives it, placeholders unexpanded
    name: str
    dirname: str
    description: str = "single"
    eol: str = "lf"
    link: str | None = None
    collision: str | None = None
    cwd: str | None = None


@dataclass(frozen=True)
class Placed:
    """A case as written: where it went and its two tokens."""

    case: Case
    scope: str
    path: str  # the skill directory, relative to its scope root
    token: str
    body_token: str
    link_kind: str | None = None


@dataclass
class Fixture:
    placed: list[Placed] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def load_catalogue(path: Path | None = None) -> list[Case]:
    return read_catalogue(path)[0]


def read_catalogue(path: Path | None = None) -> tuple[list[Case], str]:
    """The cases and the sha256 of the bytes they were parsed from (a pipe or a later edit cannot change it)."""
    path = path or CATALOGUE
    try:
        data = path.read_bytes()
        raw = yaml.safe_load(data.decode("utf-8"))
    except (OSError, ValueError, RecursionError, yaml.YAMLError) as exc:  # ValueError: bad UTF-8, a bad date
        raise SkillReachError(f"{path}: cannot read the catalogue: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != "skill_fixtures.v1" or not isinstance(raw.get("cases"), list):
        raise SkillReachError(f"{path}: not a skill_fixtures.v1 catalogue (needs `schema` and a `cases` list)")
    cases: list[Case] = []
    for n, item in enumerate(raw["cases"]):
        case = _parse_case(path, n, item)
        if case.id in {c.id for c in cases}:
            raise SkillReachError(f"{path}: case id {case.id} appears twice")
        cases.append(case)
    _check_groups(path, cases)
    return cases, hashlib.sha256(data).hexdigest()


def _parse_case(path: Path, n: int, item: Any) -> Case:
    if not isinstance(item, dict) or not item.get("id") or not item.get("at"):
        raise SkillReachError(f"{path}: case {n} needs `id` and `at`")
    extra = set(item) - set(Case.__dataclass_fields__)
    if extra:
        raise SkillReachError(f"{path}: case {item['id']}: unknown field(s) {', '.join(sorted(map(str, extra)))}")
    cid = str(item["id"])
    for key in ("cwd", "collision"):
        if item.get(key) is not None and not isinstance(item[key], str):
            raise SkillReachError(f"{path}: case {cid}: {key} must be a string")
    name = str(item.get("name") or cid)
    case = Case(id=cid, at=str(item["at"]), name=name, dirname=str(item.get("dirname") or name),
                description=str(item.get("description") or "single"), eol=str(item.get("eol") or "lf"),
                link=item.get("link"), collision=item.get("collision"), cwd=item.get("cwd"))
    if case.description not in DESCRIPTIONS:
        raise SkillReachError(f"{path}: case {cid}: description must be one of {', '.join(DESCRIPTIONS)}")
    if case.eol not in ("lf", "crlf") or case.link not in (None, "symlink"):
        raise SkillReachError(f"{path}: case {cid}: eol is lf|crlf and link is symlink or absent")
    for rel in (case.dirname, case.cwd or "."):
        if not _relative(rel):
            raise SkillReachError(f"{path}: case {cid}: {rel!r} must be a relative path inside the fixture")
    try:  # the syntax of `at` with stand-ins for the profile's dirs, so a dry run refuses what a run would
        resolve_at(case, {"client_project": "p", "client_user": "config:u"})
    except SkillReachError as exc:
        raise SkillReachError(f"{path}: {exc}") from exc
    return case


def _check_groups(path: Path, cases: list[Case]) -> None:
    groups: dict[str, set[str]] = {}
    for c in cases:
        if c.collision:
            groups.setdefault(c.collision, set()).add(c.name)
    for group, names in groups.items():
        if len(names) != 1:
            raise SkillReachError(f"{path}: collision group {group} mixes names {', '.join(sorted(names))}")


def _relative(rel: str) -> bool:
    """A path that stays under its root: no absolute, traversing, backslash, drive (``C:``) or NUL form."""
    p = PurePosixPath(rel)
    return (not p.is_absolute() and ".." not in p.parts and "\\" not in rel and ":" not in rel
            and "\0" not in rel)


def skill_dirs(profile: HarnessProfile) -> dict[str, str]:
    """The profile's first declared skill dirs: ``client_project`` (a workspace path) and ``client_user``
    (``home:<path>`` or ``config:<path>``)."""
    declared = profile.skills
    out = {}
    if declared.get("project_dirs"):
        out["client_project"] = declared["project_dirs"][0]
    if declared.get("user_dirs"):
        out["client_user"] = declared["user_dirs"][0]
    return out


def resolve_at(case: Case, dirs: dict[str, str]) -> tuple[str, str] | None:
    """(scope, the skill's parent dir) for ``case.at``; None when the profile declares no dir it names."""
    at = case.at
    if at.startswith("{client_user}"):
        if "client_user" not in dirs:
            return None
        at = dirs["client_user"] + at[len("{client_user}"):]
    if "{client_project}" in at:
        if "client_project" not in dirs:
            return None
        at = at.replace("{client_project}", dirs["client_project"])
    scope, sep, rel = at.partition(":")
    if "{" in at or not sep or scope not in SCOPES or not _relative(rel or "."):
        raise SkillReachError(f"case {case.id}: `at` {case.at!r} must be project:|home:|config:<relative path>")
    return scope, rel


def token(salt: str, case_id: str, position: str) -> str:
    digest = hashlib.sha256(f"{salt}\0{case_id}\0{position}".encode()).hexdigest()
    return ("SK" if position == "description" else "SB") + digest[:6].upper()


def _yaml_str(value: str) -> str:
    """``value`` as a YAML scalar that loads back as the same string: plain when it can be, else quoted
    (``null``, ``true`` and ``123`` would otherwise load as another type)."""
    try:
        loaded = yaml.safe_load(f"v: {value}\n")
    except (yaml.YAMLError, ValueError, RecursionError):  # 2026-99-99 resolves as a date that does not exist
        loaded = None
    return value if isinstance(loaded, dict) and loaded.get("v") == value else json.dumps(value)


def skill_text(case: Case, tok: str, body_tok: str) -> str:
    """The SKILL.md of one case; the description token sits in ``description`` in the case's format."""
    first, second = f"Fixture skill {tok}.", "Use for nothing; it exists to test skill discovery."
    desc = {
        "single": f"description: {first} {second}\n",
        "folded": f"description: >-\n  {first}\n  {second}\n",
        "literal": f"description: |\n  {first}\n  {second}\n",
        "quoted": f'description: "{first} Note: {second}"\n',
        "long": f"description: {first} {second} " + "Padding to exceed 1024 characters. " * 32 + "\n",
        "missing": "",
    }[case.description]
    body = f"Body token {body_tok}.\n" + (f"Description token {tok}.\n" if case.description == "missing" else "")
    text = f"---\nname: {_yaml_str(case.name)}\n{desc}---\n\n# {case.name}\n\n{body}"
    return text.replace("\n", "\r\n") if case.eol == "crlf" else text


def build_fixture(cases: list[Case], profile: HarnessProfile, roots: dict[str, Path], outside: Path,
                  salt: str) -> Fixture:
    """Write every case under its scope root; a case the profile cannot place, or whose directory an earlier case
    already holds, is skipped with a reason (a second write would replace the first copy)."""
    dirs = skill_dirs(profile)
    fixture = Fixture()
    taken: dict[Path, str] = {}
    for n, case in enumerate(cases):
        where = resolve_at(case, dirs)
        if where is None:
            fixture.skipped.append({"case": case.id, "reason": f"{profile.name} declares no skill dir for {case.at}"})
            continue
        scope, parent = where
        rel = str(PurePosixPath(parent) / case.dirname)
        dest = roots[scope] / rel
        if dest in taken or dest.exists() or dest.is_symlink():
            owner = taken.get(dest, "a file already there")
            fixture.skipped.append({"case": case.id, "reason": f"{scope}:{rel} is taken by {owner}"})
            continue
        taken[dest] = case.id
        tok, body_tok = token(salt, case.id, "description"), token(salt, case.id, "body")
        text = skill_text(case, tok, body_tok)
        link_kind = None
        if case.link == "symlink":
            target = outside / f"case-{n}" / case.dirname  # the index, not the id: an id is not a path
            target.mkdir(parents=True)
            (target / "SKILL.md").write_bytes(text.encode())
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(target, dest, target_is_directory=True)
            except OSError as exc:  # Windows without developer mode
                fixture.skipped.append({"case": case.id, "reason": f"this OS refused a symlink: {exc}"})
                continue
            link_kind = "symlink"
        else:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "SKILL.md").write_bytes(text.encode())
        fixture.placed.append(Placed(case, scope, rel, tok, body_tok, link_kind))
    return fixture


def check_skill_ancestors(path: Path, guidance: tuple[str, ...] = ()) -> None:
    """Refuse to run under a directory that holds a skill dir or guidance some client would read: the default
    guidance names and the profile's own (as reach checks them)."""
    check_ancestors(path, tuple(dict.fromkeys(ANCESTOR_GUARD + tuple(guidance))))
    for parent in path.parents:
        for rel in SKILL_DIR_GUARD:
            if (parent / rel).is_dir():
                raise ReachError(f"{parent / rel} is above the fixture: a harness may list its skills in "
                                 "every scenario; use --work-dir elsewhere")


def scenario_ids(cases: list[Case]) -> list[tuple[str, str]]:
    """(id, cwd) pairs: the root, then each distinct case cwd."""
    cwds = sorted({c.cwd for c in cases if c.cwd and c.cwd != "."})
    return [("cwd=.", "")] + [(f"cwd={d}", d) for d in cwds]


def child_env(profile: HarnessProfile, config_dir: Path, home: Path) -> process.Env:
    """The profile's environment with HOME (and USERPROFILE, XDG_CONFIG_HOME) moved to the fixture's home."""
    base = profile.env(config_dir)
    values = dict(base.set)
    values.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    return process.Env.only(base.allow or (), values)


def _looked_for_itself(events: list[dict[str, Any]]) -> bool:
    return any(e.get("kind") in _LOOKED for e in events)


def classify(placed: Placed, transcript: Transcript | None, looked: bool) -> dict[str, Any]:
    if transcript is None:
        return {"class": "error", "body": False}
    reported = set(TOKEN_RE.findall(transcript.answer))
    body = placed.body_token in reported
    if placed.token not in reported and not (placed.case.description == "missing" and body):
        return {"class": "not_listed", "body": body}
    return {"class": "fetched" if looked else "listed", "body": body}


def collisions(fixture: Fixture, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for p in fixture.placed:
        if p.case.collision:
            groups.setdefault(p.case.collision, []).append(p.case.id)
    out = []
    by = {(r["case"], r["scenario"]): r["class"] for r in records}
    for scenario in dict.fromkeys(r["scenario"] for r in records):
        for group, members in sorted(groups.items()):
            if len(members) < 2:
                continue
            classes = [by.get((m, scenario)) for m in members]
            if "error" in classes:
                verdict, listed = "error", []
            else:
                listed = [m for m, c in zip(members, classes, strict=True) if c in ("listed", "fetched")]
                if "fetched" in classes:  # the agent looked for a copy itself: no precedence is measured
                    verdict = "fetched"
                else:
                    verdict = ("none" if not listed else "all" if len(listed) == len(members)
                               else "one" if len(listed) == 1 else "some")
            out.append({"group": group, "scenario": scenario, "members": members, "listed": listed,
                        "verdict": verdict})
    return out


def check_profile(profile: HarnessProfile) -> None:
    if profile.native_prompt:
        raise SkillReachError(f"{profile.name} runs its own prompt (native_prompt); probe its exec profile")
    if not profile.skills:
        raise SkillReachError(f"{profile.path}: declares no `skills` block, so its own skill dirs are unknown")
    if "skills" not in profile.tools:
        raise SkillReachError(f"{profile.path}: declares no `skills` tool mode (the tools under which the agent "
                              "sees its skill list but can run nothing)")
    if profile.trace == "text":
        raise SkillReachError(f"{profile.path}: its trace has no tool events, so a fetched skill cannot be told "
                              "from a listed one")


def _select(cases: list[Case], only: tuple[str, ...]) -> list[Case]:
    if not only:
        return cases
    missing = sorted(set(only) - {c.id for c in cases})
    if missing:
        raise SkillReachError(f"--only {', '.join(missing)} matches no case")
    return [c for c in cases if c.id in only]


def _cases_and_digest(cases: list[Case] | None, catalogue: Path | None,
                      digest: str | None) -> tuple[list[Case], str]:
    """The cases and their catalogue's sha256, both taken before any scenario runs."""
    if cases is None:
        return read_catalogue(catalogue)
    if digest is not None:
        return cases, digest
    try:  # cases from a caller without a digest: hash the file now
        return cases, hashlib.sha256((catalogue or CATALOGUE).read_bytes()).hexdigest()
    except OSError as exc:
        raise SkillReachError(f"{catalogue or CATALOGUE}: cannot read the catalogue: {exc}") from exc


def _trace_dir(out_dir: Path) -> Path:
    """out_dir/skill-traces, emptied of the traces an earlier run left there; not traces/, which
    `repo reach` owns, so the two probes can share an --out without deleting each other's evidence."""
    trace_dir = out_dir / "skill-traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    for old in trace_dir.iterdir():
        if re.match(r"^\d{2,}-.*\.(stdout|events\.jsonl)$", old.name) and old.is_file():
            old.unlink()
    return trace_dir


def run_skill_reach(
    *,
    profile: HarnessProfile,
    model: str,
    out_dir: Path,
    cases: list[Case] | None = None,
    catalogue: Path | None = None,
    catalogue_sha256: str | None = None,
    only: tuple[str, ...] = (),
    bin_path: str | None = None,
    work_dir: Path | None = None,
    salt: str | None = None,
    max_turns: int = 4,
    budget_usd: float = 0.10,
    timeout_s: int = 300,
    keep: bool = False,
) -> dict[str, Any]:
    """Build the fixture, run every scenario once and return the skill_discovery.v1 payload (unsaved)."""
    check_profile(profile)
    cases, catalogue_sha256 = _cases_and_digest(cases, catalogue, catalogue_sha256)
    cases = _select(cases, only)
    salt = salt or hashlib.sha256(datetime.now(timezone.utc).isoformat().encode()).hexdigest()[:12]
    base = Path(tempfile.mkdtemp(prefix="skill-reach-", dir=str(work_dir) if work_dir else None)).resolve()
    roots = {"project": base / "ws", "home": base / "home", "config": base / "cfg"}
    try:
        for root in roots.values():
            root.mkdir()
        check_skill_ancestors(roots["project"], profile.guidance_names)
        fixture = build_fixture(cases, profile, roots, base / "outside", salt)
        if not fixture.placed:
            raise SkillReachError("no case could be placed: " + "; ".join(s["reason"] for s in fixture.skipped))
        for scenario_cwd in {c.cwd for c in cases if c.cwd}:
            (roots["project"] / scenario_cwd).mkdir(parents=True, exist_ok=True)
        _git_init(roots["project"])
        version = profile.version(child_env(profile, base / "cfg-version", roots["home"]), bin_path)
        trace_dir = _trace_dir(out_dir)
        known = {p.token for p in fixture.placed} | {p.body_token for p in fixture.placed}
        rows, records = [], []
        for n, (sid, cwd) in enumerate(scenario_ids(cases)):
            transcript, row = _run_one(profile, sid, cwd, model, roots, bin_path, max_turns, budget_usd, timeout_s)
            slug = f"{n:02d}-" + re.sub(r"[^A-Za-z0-9._-]+", "_", sid)
            (trace_dir / f"{slug}.stdout").write_text(row.pop("_stdout"), encoding="utf-8")
            looked = False
            if transcript is not None:
                (trace_dir / f"{slug}.events.jsonl").write_text(transcript.events_jsonl(), encoding="utf-8")
                reported = sorted(set(TOKEN_RE.findall(transcript.answer)))
                looked = _looked_for_itself(transcript.events)
                row.update(reported=reported, unknown=[t for t in reported if t not in known],
                           prose=prose_words(TOKEN_RE.sub(" ", transcript.answer)), looked=looked)
                version = transcript.harness_version or version
            rows.append(row)
            for p in fixture.placed:
                records.append({"case": p.case.id, "scenario": sid, **classify(p, transcript, looked)})
    finally:
        if not keep:
            shutil.rmtree(base, onerror=retry_writable)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "harness": {"name": profile.name, "version": version, "profile": profile.path,
                    "declared": profile.skills},
        "model": model,
        "salt": salt,
        "ask": ASK,
        "catalogue": str(catalogue or CATALOGUE),
        "catalogue_sha256": catalogue_sha256,
        "workspace": str(base) if keep else None,
        "cases": [{"id": p.case.id, "scope": p.scope, "path": p.path, "name": p.case.name,
                   "description": p.case.description, "eol": p.case.eol, "token": p.token,
                   "body_token": p.body_token,
                   **({"link": p.link_kind} if p.link_kind else {}),
                   **({"collision": p.case.collision} if p.case.collision else {})}
                  for p in fixture.placed],
        "skipped": fixture.skipped,
        "scenarios": rows,
        "records": records,
        "collisions": collisions(fixture, records),
        "summary": {
            # None when any scenario's cost is unknown (Codex, Copilot, a timeout): a sum would read as free
            "cost_usd": (round(sum(r["cost_usd"] for r in rows), 6)
                         if all(r.get("cost_usd") is not None for r in rows) else None),
            "by_class": {c: sum(1 for r in records if r["class"] == c) for c in CLASSES},
        },
    }


def _git_init(workspace: Path) -> None:
    """A one-commit repository: clients bound skill discovery by the git root. Git gets an allow-listed
    environment and no user or system config, so GIT_DIR, GIT_WORK_TREE and hooks of the caller do not apply."""
    env = process.Env.only(("PATH", "SYSTEMROOT"), {
        "GIT_AUTHOR_NAME": "dokimi", "GIT_AUTHOR_EMAIL": "dokimi@localhost",
        "GIT_COMMITTER_NAME": "dokimi", "GIT_COMMITTER_EMAIL": "dokimi@localhost",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    git = shutil.which("git") or "git"  # looked up on the caller's PATH, not the allow-listed one
    for args in (["init", "-q"], ["add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture",
                                                  "--allow-empty"]):
        try:
            proc = process.run([git, *args], cwd=workspace, env=env, timeout=60)
        except OSError as exc:  # no git executable
            raise SkillReachError(f"git {' '.join(args)} could not start: {exc}") from exc
        if proc.returncode != 0:
            raise SkillReachError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")


def _run_one(profile: HarnessProfile, sid: str, cwd: str, model: str, roots: dict[str, Path],
             bin_path: str | None, max_turns: int, budget_usd: float,
             timeout_s: int) -> tuple[Transcript | None, dict[str, Any]]:
    config_dir = roots["config"]
    # The config dir holds the config-scope skills, so it is shared by the scenarios; the profile's files are rewritten.
    profile.write_config(config_dir, {"config_dir": config_dir.as_posix(), "support_dir": SUPPORT_HOST.as_posix()})
    argv = profile.argv(prompt=ASK, model=model, tools="skills", max_turns=max_turns, budget_usd=budget_usd,
                        bin_path=bin_path)
    row: dict[str, Any] = {"id": sid, "cwd": cwd, "returncode": None, "error": None, "cost_usd": None,
                           "num_turns": None, "reported": [], "unknown": [], "prose": None, "looked": False}
    try:
        proc = process.run(argv, cwd=roots["project"] / cwd, env=child_env(profile, config_dir, roots["home"]),
                           timeout=timeout_s, capture_limit=None)
    except OSError as exc:
        row.update(error=f"{type(exc).__name__}: {exc}", _stdout="")
        return None, row
    if proc.timed_out:
        row.update(error=f"TimeoutExpired: {argv[0]} timed out after {timeout_s} seconds", _stdout=proc.stdout)
        return None, row
    transcript = convert(profile.trace, proc.stdout, str(roots["project"]), cwd)
    row.update(returncode=proc.returncode, cost_usd=transcript.cost_usd, num_turns=transcript.num_turns,
               _stdout=proc.stdout)
    if proc.returncode != 0 or transcript.is_error or not transcript.answer:
        row["error"] = (f"exit {proc.returncode}" + (" (error result)" if transcript.is_error else "")
                        + (" (no answer)" if not transcript.answer else "")
                        + (f": {proc.stderr.strip()[:300]}" if proc.stderr.strip() else ""))
        return None, row
    return transcript, row


def format_table(payload: dict[str, Any]) -> str:
    """One row per case, one column per scenario, then the collision verdicts."""
    short = {"listed": "L", "fetched": "F", "not_listed": ".", "error": "E"}
    ids = [s["id"] for s in payload["scenarios"]]
    cell = {(r["case"], r["scenario"]): r for r in payload["records"]}
    label = {c["id"]: f"{c['id']}  ({c['scope']}:{c['path']})" for c in payload["cases"]}
    width = max([len(v) for v in label.values()] + [4])
    cost = payload["summary"]["cost_usd"]
    lines = [f"{payload['harness']['name']} {payload['harness']['version']}  model={payload['model']}"
             f"  cost={'unknown' if cost is None else f'${cost:.4f}'}"]
    lines += [f"  [{i}] {sid}" for i, sid in enumerate(ids)]
    lines.append(" " * (width + 2) + " ".join(f"{i:>2}" for i in range(len(ids))))
    for c in payload["cases"]:
        marks = []
        for s in ids:
            r = cell.get((c["id"], s), {"class": "error", "body": False})
            marks.append(short[r["class"]] + ("b" if r.get("body") else ""))
        lines.append(f"{label[c['id']]:<{width}}  " + " ".join(f"{m:>2}" for m in marks))
    lines.append("L listed  F listed after the agent looked itself  . not listed  E error  b body loaded too")
    for col in payload["collisions"]:
        winner = f": {col['listed'][0]}" if col["verdict"] == "one" else ""
        lines.append(f"collision {col['group']} [{col['scenario']}]: {col['verdict']}{winner}")
    for s in payload["skipped"]:
        lines.append(f"skipped {s['case']}: {s['reason']}")
    for s in payload["scenarios"]:
        if s["error"]:
            lines.append(f"error in {s['id']}: {s['error']}")
        if s.get("looked"):
            lines.append(f"warning: {s['id']}: the agent ran a command, read a file or used a tool; "
                         "its L cells are F")
        if s["unknown"]:
            lines.append(f"unknown tokens in {s['id']}: {', '.join(s['unknown'])}")
    return "\n".join(lines)


def save_skill_reach(payload: dict[str, Any], out_dir: Path) -> Path:
    from dokimi.artifacts import save_artifact

    path = out_dir / "skill-discovery.json"
    save_artifact("skill_discovery", payload, path)
    return path
