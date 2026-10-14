# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Arm-spec grammar: what an ``--arms`` entry means and what the arm is called.

Two grammars meet here and are easy to confuse:

* an arm **spec** is what a user writes — ``--arms``, a subject descriptor's
  baseline: ``docs:local:./d``, ``skill-agent:<path>``, ``docs+skill:<path>``;
* an arm **name** is what a run records — the artifact's ``arms`` list, summary
  keys, ``--baseline-arm``: ``docs``, ``skill_agent:<skill name>``,
  ``docs+skill:<skill name>``.

Spec to name is not a string rewrite. ``docs:local:./a`` and ``docs:local:./b``
are both named ``docs``; ``profile:``, ``skill:`` and ``skill-agent:`` arms are
named after the content they load (the profile's frontmatter ``id``, the skill's
``name``), so their name is only known once the content is read. A
``skill-catalog:<dir>`` arm is named after its argument (``skill_catalog:<dir>``),
so two catalogs of one size — a description before and after a fix — can run
side by side.
:meth:`ArmPart.arm_name` is the rule; the treatment factory applies it.

A combined spec joins parts with ``+``. A ``+`` only starts a new part when the
text after it starts a known kind (``docs+skill:/x/c++`` is two parts, the
second a path containing ``++``), and a spec that *starts* with ``mcp:`` is never
split, because an MCP reference may carry ``+`` in a URL query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Iterable, List, Optional, Tuple

#: Separator between the parts of a combined spec, and between the sub-arm
#: names of a combined arm's name.
COMBINE = "+"

_NONE, _OPTIONAL, _REQUIRED = "none", "optional", "required"


class ArmSpecError(ValueError):
    """An arm spec that does not parse: unknown kind, or a missing argument."""


@dataclass(frozen=True)
class ArmKind:
    """One arm kind: how its spec is written and how its arm is named."""

    prefix: str
    arg: str  # _NONE | _OPTIONAL | _REQUIRED
    #: Fixed arm name, or ``None`` when the name is ``"<name_prefix>:<label>"``
    #: with ``label`` read from the content the arm loads.
    name: Optional[str] = None
    name_prefix: Optional[str] = None
    #: The label is the spec argument itself rather than content-derived.
    name_from_arg: bool = False
    #: ``"path"``: the argument is a file/dir the arm reads. ``"doc_source"``: the
    #: argument is a doc-source ref, which is local content only as ``local:<path>``.
    content: Optional[str] = None
    default_arg: str = ""
    example: str = ""
    #: What a required argument is, for usage and error text.
    arg_noun: str = "path"


KINDS: Tuple[ArmKind, ...] = (
    ArmKind("baseline", _NONE, name="baseline"),
    ArmKind("docs", _OPTIONAL, name="docs", content="doc_source", default_arg="context7"),
    ArmKind("mcp", _REQUIRED, name="mcp_doc", arg_noun="ref",
            example="mcp:http=https://mcp.context7.com/mcp"),
    ArmKind("profile", _REQUIRED, name_prefix="profile", content="path",
            example="profile:data/agent_profiles/concise_expert.md"),
    ArmKind("skill", _REQUIRED, name_prefix="skill", content="path",
            example="skill:data/skills/fastcontext"),
    ArmKind("agent", _OPTIONAL, name="agent", content="doc_source", default_arg="context7"),
    ArmKind("skill-agent", _REQUIRED, name_prefix="skill_agent", content="path",
            example="skill-agent:data/skills/fastcontext"),
    ArmKind("skill-catalog", _REQUIRED, name_prefix="skill_catalog", content="path",
            name_from_arg=True, arg_noun="dir",
            example="skill-catalog:/path/to/skills-repo/skills"),
)
_BY_PREFIX = {k.prefix: k for k in KINDS}


def _usage(kind: ArmKind) -> str:
    if kind.arg == _NONE:
        return kind.prefix
    if kind.arg == _OPTIONAL:
        return f"{kind.prefix}[:<source>]"
    return f"{kind.prefix}:<{kind.arg_noun}>"


_ARG_NOUNS = {"path": "a path", "ref": "a reference", "dir": "a directory of skills"}


VALID_SPECS = ", ".join(f"'{_usage(k)}'" for k in KINDS) + ", '<spec>+<spec>[+...]'"


@dataclass(frozen=True)
class ArmPart:
    """One non-combined part of an arm spec."""

    kind: ArmKind
    #: Text after ``<prefix>:``, stripped; ``""`` when absent.
    arg: str
    #: The part as written.
    raw: str

    @property
    def doc_source(self) -> str:
        """Doc-source ref for ``docs``/``agent``; ``mcp`` passes its whole spec."""
        if self.kind.prefix == "mcp":
            return self.raw
        return self.arg or self.kind.default_arg

    @property
    def content_ref(self) -> Optional[str]:
        """Local content this part reads, for digesting; ``None`` when remote or none.

        A path for ``profile:``/``skill:``/``skill-agent:``, a directory for
        ``skill-catalog:``; a ``local:<path>`` ref for
        ``docs:``/``agent:``. Context7, URLs and MCP servers are not local content.
        """
        if self.kind.content == "path":
            return self.arg
        if self.kind.content == "doc_source" and self.arg.startswith("local:"):
            return self.arg
        return None

    def arm_name(self, label: Optional[str] = None) -> str:
        """The arm name; ``label`` is the content-derived id kinds without a fixed name need."""
        if self.kind.name is not None:
            return self.kind.name
        if self.kind.name_from_arg:
            label = self.arg
        if not label:
            raise ValueError(f"arm '{self.raw}' is named after its content; pass its label")
        return f"{self.kind.name_prefix}:{label}"


def _kind_of(part: str) -> Optional[ArmKind]:
    head = part.split(":", 1)[0]
    return _BY_PREFIX.get(head)


def split_combined(spec: str) -> List[str]:
    """Split a spec on the ``+`` that start a new part; see the module docstring."""
    spec = spec.strip()
    if spec.startswith("mcp:"):
        return [spec]
    out: List[str] = []
    for piece in spec.split(COMBINE):
        piece = piece.strip()
        if out and _kind_of(piece) is None:
            out[-1] += COMBINE + piece
        else:
            out.append(piece)
    return out


def parse_part(part: str) -> ArmPart:
    """Parse one non-combined part, raising :class:`ArmSpecError` with the valid forms."""
    part = part.strip()
    kind = _kind_of(part)
    if kind is None:
        raise ArmSpecError(f"Unknown arm spec: '{part}'. Valid specs: {VALID_SPECS}.")
    arg = part[len(kind.prefix) + 1:].strip() if part != kind.prefix else ""
    if kind.arg == _NONE and part != kind.prefix:
        raise ArmSpecError(f"'{kind.prefix}' arm takes no argument, got '{part}'")
    if kind.arg == _REQUIRED and not arg:
        raise ArmSpecError(
            f"'{kind.prefix}:' arm requires {_ARG_NOUNS[kind.arg_noun]}, e.g. '{kind.example}'")
    return ArmPart(kind=kind, arg=arg, raw=part)


def parse_arm_spec(spec: str) -> List[ArmPart]:
    """Parse a (possibly combined) spec into its parts, in spec order."""
    return [parse_part(p) for p in split_combined(spec)]


def content_refs(spec: str) -> List[Tuple[str, str]]:
    """``(part, content_ref)`` for each part of *spec* that reads local content.

    Tolerant of parts that do not parse, so a digest can be taken of any recorded spec.
    """
    refs = []
    for raw in split_combined(spec):
        try:
            part = parse_part(raw)
        except ArmSpecError:
            continue
        if part.content_ref is not None:
            refs.append((part.raw, part.content_ref))
    return refs


def split_arm_list(raw: str) -> List[str]:
    """Split a comma-separated ``--arms`` value; at least one spec is required."""
    specs = [s.strip() for s in raw.split(",") if s.strip()]
    if not specs:
        raise ArmSpecError("--arms must list at least one arm spec.")
    return specs


def combine_specs(parts: Iterable[str]) -> str:
    """Join specs into one combined spec that :func:`split_combined` splits back.

    A combined spec that starts with ``mcp:`` would never be split, so an ``mcp:``
    part is moved behind the first part that is not one. All-``mcp:`` combinations
    cannot be expressed and are joined as written.
    """
    parts = list(parts)
    if len(parts) > 1 and parts[0].startswith("mcp:"):
        first = next((i for i, p in enumerate(parts) if not p.startswith("mcp:")), None)
        if first is not None:
            parts.insert(0, parts.pop(first))
    return COMBINE.join(parts)


def combined_name(names: Iterable[str]) -> str:
    """Name of a combined arm from its sub-arm names."""
    return COMBINE.join(names)


def split_combined_name(name: str, present: Collection[str]) -> Optional[Tuple[str, str]]:
    """Split a two-factor combined arm *name* into ``(a, b)`` both in *present*.

    Names may contain ``+`` themselves (``skill:c++-guide``), so the split point is
    chosen by membership, not by counting separators; the leftmost match wins.
    """
    for i, char in enumerate(name):
        if char == COMBINE and name[:i] in present and name[i + 1:] in present:
            return name[:i], name[i + 1:]
    return None
