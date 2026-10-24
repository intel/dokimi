# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The network a ``docker-*`` task run gets, per phase: build, solve, verify.

Three modes, the same three Harbor's ``task.toml`` names (``network_mode`` =
``no-network`` / ``allowlist`` / ``public``, with ``allowed_hosts``):

``none``
    No route out. Off unless something says otherwise: the solve (an
    in-container agent still reaches its LLM through the egress sidecar, and
    nothing else) and the verifier.
``allowlist``
    Only the listed ``host[:port]`` entries (port 443 when omitted), through the
    :mod:`.egress_proxy` sidecar. Every request is logged; the verifier's
    decisions are kept as ``metrics.network.verify_egress``.
``public``
    The default bridge (NAT'd internet, not the host network) plus the host's
    proxy variables. The image build gets it by default: ``apt``/``pip`` in a
    Dockerfile needs it, and nothing under test runs then.

Where a phase's policy comes from, later wins:

1. :data:`DEFAULTS`.
2. The task: ``[environment]`` ``network_mode`` / ``allowed_hosts`` (or Harbor's
   deprecated ``allow_internet``) for the oracle and the verifier;
   ``[agent]`` and ``[verifier]`` overrides for their phase; dokimi's own
   ``environment.egress`` hosts for an agent's solve. An ``[environment]``-level
   ``public`` is not extended to an agent: Harbor defaults to public and catalogs
   copy it, so honouring it would hand every agent the internet. An agent gets
   the internet only from an explicit ``[agent]`` policy or the operator.
3. The operator: ``dokimi tasks run --network PHASE=MODE[:host,...]``.

The effective plan, with each phase's source, is recorded in every run's
``metrics.network``, so two runs can be checked for the same network before
their verdicts are compared.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from dokimi.utils import parse_frontmatter

from .egress_proxy import normalize_entry

PHASES = ("build", "solve", "verify")
MODES = ("none", "allowlist", "public")
_MODE_ALIASES = {"no-network": "none", "off": "none", "open": "public", "on": "public"}
_PHASE_ALIASES = {"setup": "build", "agent": "solve", "oracle": "solve", "verifier": "verify",
                  "all": "build,solve,verify"}
PROXY_VARS = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
SIDECAR_URL = "http://egress:3128"  # container_agent.PROXY_PORT on the internal network's alias
_KEEP_SCHEME = r"\1"


@dataclass(frozen=True)
class PhasePolicy:
    mode: str
    hosts: tuple[str, ...] = ()
    source: str = "default"

    def label(self) -> str:
        return f"allowlist:{','.join(self.hosts)}" if self.mode == "allowlist" else self.mode

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "hosts": list(self.hosts), "source": self.source}


DEFAULTS = {"build": PhasePolicy("public"), "solve": PhasePolicy("none"), "verify": PhasePolicy("none")}


@dataclass(frozen=True)
class NetworkPlan:
    build: PhasePolicy
    solve: PhasePolicy
    verify: PhasePolicy

    def as_metrics(self) -> dict[str, Any]:
        return {p: getattr(self, p).as_dict() for p in PHASES}

    def summary(self) -> str:
        return " ".join(f"{p}={getattr(self, p).label()}" for p in PHASES)


def policy(mode: Any, hosts: Iterable[Any] = (), source: str = "default") -> PhasePolicy:
    """A validated policy; an unknown mode, or hosts on a mode without a list, is a ``ValueError``."""
    name = _MODE_ALIASES.get(str(mode).strip().lower(), str(mode).strip().lower())
    if name not in MODES:
        raise ValueError(f"network mode {mode!r} ({source}): expected one of {', '.join(MODES)}")
    entries = tuple(sorted({normalize_entry(str(h)) for h in hosts}))
    if entries and name != "allowlist":
        raise ValueError(f"network hosts ({source}) are only valid with mode allowlist, not {name}")
    return PhasePolicy(name, entries, source)


def parse_override(spec: str) -> dict[str, PhasePolicy]:
    """``PHASE[,PHASE]=MODE[:host,...]`` (``all`` for every phase) -> one policy per phase."""
    phases_raw, sep, value = spec.partition("=")
    if not sep or not value:
        raise ValueError(f"--network {spec!r}: expected PHASE=MODE, e.g. verify=none or "
                         "solve=allowlist:pypi.org,files.pythonhosted.org")
    mode, _, hosts = value.partition(":")
    phases = [p.strip() for raw in phases_raw.split(",")
              for p in _PHASE_ALIASES.get(raw.strip().lower(), raw.strip().lower()).split(",")]
    unknown = [p for p in phases if p not in PHASES]
    if unknown:
        raise ValueError(f"--network {spec!r}: unknown phase {', '.join(unknown)}; "
                         f"expected {', '.join(PHASES)} or all")
    pol = policy(mode, [h for h in hosts.split(",") if h.strip()], "cli")
    if pol.mode == "allowlist" and not pol.hosts:
        raise ValueError(f"--network {spec!r}: allowlist needs hosts, e.g. allowlist:pypi.org")
    if pol.mode == "allowlist" and "build" in phases:
        raise ValueError(f"--network {spec!r}: the image build takes none or public (docker build "
                         "cannot route through the egress sidecar)")
    return {p: pol for p in phases}


def _declared(table: Any, where: str) -> Optional[PhasePolicy]:
    """A table's ``network_mode`` (+ ``allowed_hosts``), or Harbor's ``allow_internet`` bool."""
    if not isinstance(table, dict):
        return None
    hosts = table.get("allowed_hosts") or []
    hosts = [hosts] if isinstance(hosts, str) else list(hosts)
    if table.get("network_mode") is not None:
        return policy(table["network_mode"], hosts, f"task [{where}] network_mode")
    if isinstance(table.get("allow_internet"), bool):
        return policy("public" if table["allow_internet"] else "none", (), f"task [{where}] allow_internet")
    return None


def _with_hosts(base: PhasePolicy, hosts: Sequence[str], source: str) -> PhasePolicy:
    """*base* widened by *hosts*: ``none`` becomes an allowlist, ``public`` stays public."""
    if not hosts or base.mode == "public":
        return base
    merged = policy("allowlist", [*base.hosts, *hosts], source)
    return replace(merged, source=source if base.source == "default" else f"{base.source} + {source}")


def resolve(task: Any, overrides: Sequence[str] = (), *, agent: bool = False) -> NetworkPlan:
    """The plan for one run of *task*; *agent* when an LLM agent (not the oracle) solves it."""
    plan = dict(DEFAULTS)
    baseline = _declared(task.environment, "environment")
    if baseline is not None:
        plan["verify"] = baseline
        if not agent:
            plan["solve"] = baseline
        elif baseline.mode == "allowlist":
            plan["solve"] = baseline  # explicit hosts are safe to extend; "public" is not
    egress = task.environment.get("egress")
    if agent and egress:
        extra = [egress] if isinstance(egress, str) else [str(h) for h in egress]
        plan["solve"] = _with_hosts(plan["solve"], extra, "task environment.egress")
    for phase, table in (("solve", task.agent), ("verify", task.verifier)):
        explicit = _declared(table, "agent" if phase == "solve" else "verifier")
        if explicit is not None:
            plan[phase] = explicit
    for spec in overrides:
        plan.update(parse_override(spec))
    return NetworkPlan(**plan)


def skill_needs(skill_dir: Optional[Path], plan: NetworkPlan) -> Optional[dict[str, Any]]:
    """What a skill declares it needs at solve time, and what this run's plan leaves out.

    A skill declares hosts in its ``SKILL.md`` frontmatter as ``metadata.network``
    (``allowlist:host,...`` or ``public``; the Agent Skills spec's ``metadata`` is
    a free string map, and its ``compatibility`` text is where the reason goes for
    people). The plan is not widened for the skill arm alone: its baseline would
    then run on a different network, and the delta would not be the skill's. The
    operator widens every arm with ``--network solve=...``; ``missing`` says
    which declared hosts the run did not grant.
    """
    path = skill_dir / "SKILL.md" if skill_dir is not None else None
    if path is None or not path.is_file():
        return None
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    declared = (meta.get("metadata") or {}).get("network") if isinstance(meta.get("metadata"), dict) else None
    if not declared:
        return None
    mode, _, hosts = str(declared).partition(":")
    want = policy(mode, [h for h in hosts.split(",") if h.strip()], "skill metadata.network")
    if plan.solve.mode == "public":
        missing: list[str] = []
    elif want.mode == "public":
        missing = ["*"]
    else:
        missing = [h for h in want.hosts if h not in plan.solve.hosts]
    return {"declared": want.label(), "missing": missing}


# ── applying a plan ─────────────────────────────────────────────────────────


def proxy_env_flags(pol: PhasePolicy, *, credentials: bool = True) -> list[str]:
    """``docker exec -e`` flags for a step under *pol*.

    ``public``: the host's proxy variables (behind a corporate proxy a verifier
    that installs pytest at verify time fails without them), with their
    ``user:pw@`` dropped when ``credentials`` is off (the verifier after an
    in-container agent runs the agent's code). ``allowlist``: the sidecar, which
    holds the upstream credentials itself. ``none``: nothing.
    """
    if pol.mode == "allowlist":
        return [a for v in PROXY_VARS for a in
                ("-e", f"{v}={'localhost,127.0.0.1,::1' if v.lower() == 'no_proxy' else SIDECAR_URL}")]
    if pol.mode != "public":
        return []
    flags: list[str] = []
    for var in PROXY_VARS:
        val = os.environ.get(var)
        if val:
            flags += ["-e", f"{var}={val if credentials else _USERINFO_RE.sub(_KEEP_SCHEME, val)}"]
    return flags


def build_args(pol: PhasePolicy) -> list[str]:
    """``docker build`` network flags: host network and proxy build-args, or none."""
    if pol.mode == "none":
        return ["--network", "none"]
    flags = ["--network", "host"]
    for var in PROXY_VARS:
        if os.environ.get(var):
            flags += ["--build-arg", f"{var}={os.environ[var]}"]
    return flags


def container_phases(plan: NetworkPlan, *, solve_in_container: bool) -> list[PhasePolicy]:
    return ([plan.solve] if solve_in_container else []) + [plan.verify]


def needs_sandbox(plan: NetworkPlan, *, solve_in_container: bool) -> bool:
    """An allowlist phase needs the per-run internal network, for its sidecar."""
    return any(p.mode == "allowlist" for p in container_phases(plan, solve_in_container=solve_in_container))


def start_args(plan: NetworkPlan, *, solve_in_container: bool) -> list[str]:
    """``docker run`` network without a sidecar: bridge when a phase is public, else none.

    A phase that is ``none`` then leaves the bridge (:func:`enter_phase`) before
    anything but the container's ``sleep`` runs: no network at all, stricter than
    an internal network, which still reaches services on the Docker host's gateway.
    """
    public = any(p.mode == "public" for p in container_phases(plan, solve_in_container=solve_in_container))
    return ["--network", "bridge" if public else "none"]


Docker = Callable[..., Any]


def enter_phase(pol: PhasePolicy, container: str, docker: Docker, log, *, bridged: bool) -> bool:
    """Join or leave the default bridge for *pol*; returns whether the container is on it now.

    The sidecar (``allowlist``) is the caller's: it must be started after this,
    on the container's internal network.
    """
    want = pol.mode == "public"
    if want and not bridged:
        docker(["network", "connect", "bridge", container], log)
    elif bridged and not want:
        docker(["network", "disconnect", "-f", "bridge", container], log)
    return want


# userinfo after a scheme or right after "VAR=" (a scheme-less proxy); greedy,
# so a raw "@" or "=" in the password (base64 "==") is covered up to the last
# "@" before the host.
_CREDENTIALS_RE = re.compile(r"(://|=)[^/\s]+@")
# The same userinfo in a bare proxy value (no "VAR="), to drop it.
_USERINFO_RE = re.compile(r"(^|://)[^/\s]+@")


def redact(arg: str) -> str:
    """Hide ``user:password@`` in proxy URLs written to run.log.

    The pattern needs ``://`` or ``VAR=`` before the userinfo; a step that
    echoes a scheme-less proxy on its own (``u:pw@proxy:912``) is caught by
    the host's own proxy userinfo, matched literally.
    """
    out = _CREDENTIALS_RE.sub(r"\1***@", arg)
    for var in PROXY_VARS:
        found = _USERINFO_RE.search(os.environ.get(var, ""))
        userinfo = found.group(0)[len(found.group(1)):-1] if found else ""
        if ":" in userinfo:  # a password: a bare user name is too short to match safely
            out = out.replace(userinfo + "@", "***@")
    return out
