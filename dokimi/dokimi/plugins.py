# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Runtime plugins for treatment-arm runs.

Plugins are behavior modifiers applied inside a fixed model/harness cell.  Two
plugin kinds are implemented:

* ``prompt_middleware`` — modifies the system prompt *before* the model runs
  (e.g. a Caveman brevity instruction). Raw and final answer are identical.
* ``output_shaper`` — post-processes the model's answer *after* it is produced
  (e.g. a hard truncation). This is the kind whose whole point is that the
  final answer differs from the raw model output, which is why the metrics
  block carries distinct ``raw_answer_chars`` / ``final_answer_chars``:
  a shaper can cut the delivered text without reducing the tokens the
  model was already billed for.

Every run records a canonical plugin-set identity for reports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional

from dokimi.treatments.base import AgentConfig, Treatment
from dokimi.digest import digest_text


EMPTY_PLUGIN_SET_ID = "sha256:e3b0c44298fc1c149afbf4c8996fb924"


_CAVEMAN_LEVELS = {
    "lite": (
        "Be concise. Prefer short direct sentences. Keep all technical facts, "
        "numbers, warnings, and code intact."
    ),
    "full": (
        "Respond in caveman style: terse, blunt, low-fluff fragments. Keep all "
        "technical substance, exact API names, numbers, warnings, and code intact."
    ),
    "ultra": (
        "Respond in ultra-compressed caveman style. Use minimal words. Preserve "
        "technical correctness, required caveats, exact identifiers, and code."
    ),
}

_PROMPT_MIDDLEWARE_HARNESSES = {
    "arms-runner",
    "single-shot",
    "agent",
    "openclaw-agent",
}


@dataclass(frozen=True)
class Plugin:
    """A runtime behavior modifier applied to an ``AgentConfig``."""

    id: str
    ref: str
    kind: str
    version: str
    config: Dict[str, Any] = field(default_factory=dict)
    system_prompt: Optional[str] = None

    @property
    def is_output_shaper(self) -> bool:
        return self.kind == "output_shaper"

    def apply(self, cfg: AgentConfig) -> AgentConfig:
        """Return a copy of *cfg* with this plugin applied.

        Prompt-middleware plugins fold their instruction into the system prompt
        here (pre-model). Output shapers leave the prompt untouched — they act
        after the model runs, via :meth:`shape`, and register themselves in
        ``metadata["output_shapers"]`` so the runner knows to call them.
        """
        system_prompt = cfg.system_prompt
        if self.system_prompt and not self.is_output_shaper:
            system_prompt = (
                f"{system_prompt.rstrip()}\n\n{self.system_prompt}"
                if system_prompt
                else self.system_prompt
            )

        metadata = dict(cfg.metadata)
        plugins = list(metadata.get("plugins", []))
        plugins.append(self.metadata())
        metadata["plugins"] = plugins
        if self.is_output_shaper:
            shapers = list(metadata.get("output_shapers", []))
            shapers.append(self)
            metadata["output_shapers"] = shapers
        # replace(), not a field-by-field AgentConfig(...): listing the fields
        # here silently dropped context_is_static, so wrapping a static-context
        # arm (a skill body) in any plugin set turned its provider prompt
        # caching off with nothing in the artifact to say so. Any field added to
        # AgentConfig later would have been dropped the same way.
        return replace(
            cfg,
            system_prompt=system_prompt,
            injected_context=list(cfg.injected_context),
            tools=list(cfg.tools),
            metadata=metadata,
        )

    def shape(self, answer: str) -> str:
        """Post-process a model answer (output shapers only).

        Deterministic and offline — no LLM call — so the token/length trade-off
        can be exercised in CI without a live runtime. A non-shaper
        plugin returns the answer unchanged.
        """
        if not self.is_output_shaper or answer is None:
            return answer
        if self.id == "truncate":
            max_chars = int(self.config.get("max_chars", 0) or 0)
            if max_chars > 0 and len(answer) > max_chars:
                ellipsis = "…" if self.config.get("ellipsis", True) else ""
                keep = max(0, max_chars - len(ellipsis))
                return answer[:keep] + ellipsis
            return answer
        return answer

    def metadata(self) -> Dict[str, Any]:
        """Stable, serialisable plugin metadata."""
        return {
            "id": self.id,
            "ref": self.ref,
            "kind": self.kind,
            "version": self.version,
            "config": dict(self.config),
            "config_hash": _hash_obj(self.config),
        }


class PluginWrappedTreatment(Treatment):
    """Treatment wrapper that applies a plugin set after arm preparation."""

    def __init__(self, inner: Treatment, plugins: Iterable[Plugin]):
        self.inner = inner
        self.plugins = list(plugins)
        self.name = inner.name
        self.plugin_set = plugin_set_metadata(self.plugins)

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        cfg = self.inner.prepare(question_text, library_name, library_id)
        arm_prompt = cfg.system_prompt
        for plugin in self.plugins:
            cfg = plugin.apply(cfg)

        metadata = dict(cfg.metadata)
        metadata["plugin_set"] = self.plugin_set["plugin_set"]
        metadata["plugin_set_id"] = self.plugin_set["plugin_set_id"]
        # A prompt-middleware plugin appends to the system prompt *after* the arm has
        # digested its own contribution, so ``profile_prompt_sha256`` describes the
        # profile, not the prompt that reaches the model. Digest the prompt as it leaves
        # this wrapper as well, and say whether a plugin extended it — otherwise a caveman
        # cell and a bare profile cell carry the same profile digest and nothing in the
        # artifact distinguishes the two system prompts.
        if cfg.system_prompt:
            metadata["system_prompt_sha256"] = digest_text(cfg.system_prompt)
            metadata["system_prompt_extended_by_plugins"] = cfg.system_prompt != arm_prompt
        # replace() for the same reason as in Plugin.apply above.
        return replace(cfg, metadata=metadata)


def take_output_shapers(cfg: AgentConfig) -> tuple[list["Plugin"], Dict[str, Any]]:
    """Split runtime shaper objects out of an AgentConfig's metadata.

    Returns ``(shapers, clean_metadata)`` where ``clean_metadata`` is a shallow
    copy safe to serialise (the ``output_shapers`` key holds live ``Plugin``
    objects and must not reach ``json.dumps``). Shaper *provenance* still lives
    in the serialisable ``metadata["plugins"]`` list.
    """
    shapers = list(cfg.metadata.get("output_shapers", []))
    if not shapers:
        return [], cfg.metadata
    clean = {k: v for k, v in cfg.metadata.items() if k != "output_shapers"}
    return shapers, clean


def apply_output_shapers(shapers: Iterable["Plugin"], answer: str) -> str:
    """Run an ordered list of output shapers over a model answer."""
    for shaper in shapers:
        answer = shaper.shape(answer)
    return answer


def create_plugin(spec: str) -> Plugin:
    """Create one plugin from a CLI spec.

    Supported refs:
      - ``plugin:caveman`` (prompt_middleware)
      - ``plugin:caveman:<lite|full|ultra>``
      - ``plugin:truncate`` (output_shaper; defaults to 800 chars)
      - ``plugin:truncate:<max_chars>``
      - the ``plugin:`` prefix is optional (``caveman:ultra``, ``truncate:500``)
    """
    raw = spec.strip()
    if not raw:
        raise ValueError("Empty plugin spec")

    ref = raw
    if raw.startswith("plugin:"):
        raw = raw[len("plugin:"):]

    parts = raw.split(":")
    plugin_id = parts[0].strip()
    canonical_ref = ref if ref.startswith("plugin:") else f"plugin:{ref}"

    if plugin_id == "caveman":
        if len(parts) > 2:
            raise ValueError(
                f"Invalid caveman plugin spec: '{spec}'. Use 'plugin:caveman[:level]'."
            )
        level = parts[1].strip() if len(parts) == 2 else "full"
        if level not in _CAVEMAN_LEVELS:
            raise ValueError(
                f"Invalid caveman level '{level}'. Valid levels: {', '.join(_CAVEMAN_LEVELS)}."
            )
        return Plugin(
            id="caveman",
            ref=canonical_ref,
            kind="prompt_middleware",
            version="0.1.0",
            config={"level": level, "target_style": "terse"},
            system_prompt=_CAVEMAN_LEVELS[level],
        )

    if plugin_id == "truncate":
        if len(parts) > 2:
            raise ValueError(
                f"Invalid truncate plugin spec: '{spec}'. Use 'plugin:truncate[:max_chars]'."
            )
        if len(parts) == 2:
            raw_max = parts[1].strip()
            try:
                max_chars = int(raw_max)
            except ValueError:
                raise ValueError(
                    f"Invalid truncate max_chars '{raw_max}': must be a positive integer."
                ) from None
            if max_chars <= 0:
                raise ValueError(
                    f"Invalid truncate max_chars '{raw_max}': must be a positive integer."
                )
        else:
            max_chars = 800
        return Plugin(
            id="truncate",
            ref=canonical_ref,
            kind="output_shaper",
            version="0.1.0",
            config={"max_chars": max_chars, "ellipsis": True},
        )

    raise ValueError(
        f"Unknown plugin spec: '{spec}'. Valid specs: "
        f"'plugin:caveman[:lite|full|ultra]', 'plugin:truncate[:max_chars]'."
    )


def create_plugins(specs: Iterable[str]) -> List[Plugin]:
    """Create plugins from a sequence of CLI specs."""
    return [create_plugin(s) for s in specs if s.strip()]


_OUTPUT_SHAPER_HARNESSES = {
    "arms-runner",
    "single-shot",
    "agent",
    "openclaw-agent",
}


def validate_plugins_for_harness(plugins: Iterable[Plugin], harness: str) -> None:
    """Reject plugin/harness combinations that cannot execute faithfully."""
    for plugin in plugins:
        if plugin.kind == "prompt_middleware" and harness not in _PROMPT_MIDDLEWARE_HARNESSES:
            raise ValueError(
                f"Plugin '{plugin.ref}' is prompt_middleware and is not supported by "
                f"harness '{harness}'. Supported harnesses: "
                f"{', '.join(sorted(_PROMPT_MIDDLEWARE_HARNESSES))}."
            )
        if plugin.kind == "output_shaper" and harness not in _OUTPUT_SHAPER_HARNESSES:
            raise ValueError(
                f"Plugin '{plugin.ref}' is output_shaper and is not supported by "
                f"harness '{harness}'. Supported harnesses: "
                f"{', '.join(sorted(_OUTPUT_SHAPER_HARNESSES))}."
            )


def wrap_treatments(treatments: Iterable[Treatment], plugins: Iterable[Plugin]) -> List[Treatment]:
    """Apply the same plugin set to every treatment arm."""
    plugins = list(plugins)
    if not plugins:
        return list(treatments)
    return [PluginWrappedTreatment(t, plugins) for t in treatments]


def plugin_set_metadata(plugins: Iterable[Plugin]) -> Dict[str, Any]:
    """Return canonical metadata for an ordered plugin set."""
    items = [p.metadata() for p in plugins]
    if not items:
        return {
            "plugin_set": "none",
            "plugin_set_id": EMPTY_PLUGIN_SET_ID,
            "plugins": [],
        }
    label = "+".join(_plugin_label(p) for p in items)
    return {
        "plugin_set": label,
        "plugin_set_id": _hash_obj(items),
        "plugins": items,
    }


def _plugin_label(p: Dict[str, Any]) -> str:
    """Human-readable per-plugin label for a plugin-set name."""
    cfg = p.get("config", {}) or {}
    if p.get("id") == "caveman" and cfg.get("level"):
        return f"caveman:{cfg['level']}"
    if p.get("id") == "truncate" and cfg.get("max_chars"):
        return f"truncate:{cfg['max_chars']}"
    return p["id"]


def _hash_obj(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()[:32]
