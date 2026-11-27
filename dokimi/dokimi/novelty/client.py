# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The novelty layer's single LLM entry point.

Every novelty stage that needs a model goes through :class:`LLMClient`, for three
reasons: the usage records accumulate in one place (so a `novelty.v1` artifact can
state what the measurement cost), the roles stay separable (extractor, answerer,
judge are distinct clients, never silently the same model), and tests can swap the
transport for a stub without patching :mod:`dokimi.llm`.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from dokimi.llm_cli import backend as cli_backend
from dokimi.metrics.usage import UsageRecord
from dokimi.spend import Spend, SpendCap, SpendCapReached
from dokimi import judge_rule


def default_judge(model: str | None, provider: str | None = None,
                  answerer_provider: str | None = None) -> tuple[str, str]:
    """The one default-judge rule (:func:`dokimi.judge_rule.default_judge`), under the ``model`` keyword it had here.

    ``commands/novelty`` imports it from this module, and callers used ``default_judge(model=...)``.
    """
    return judge_rule.default_judge(model, provider, answerer_provider)

# A transport takes (prompt, system) and returns (text, usage).
Transport = Callable[[str, "str | None"], "tuple[str, UsageRecord | None]"]
# An acceptance test for a reply: False means "unusable", so the response cache neither
# stores it nor replays it (see :func:`dokimi.caching.keeps`).
Accept = Callable[[str], bool]


# The output ceiling a novelty call asks for when none is named. A provider default is
# lower (litellm sends 4096 to Bedrock-hosted Claude), and a cut reply is a lost datum.
DEFAULT_MAX_TOKENS = 16384


class OutputTruncated(ValueError):
    """The reply hit the output ceiling, so its text is a prefix of the answer.

    A ``ValueError``, so a caller that already treats an unparseable reply as a failed one
    treats a cut one the same way; the stages that can re-ask smaller (extraction, batches)
    catch it by type and split the batch. ``reply`` is the start of the cut text (2000
    characters), for a report that keeps a reply's raw text.
    """

    def __init__(self, message: str, reply: str = "") -> None:
        super().__init__(message)
        self.reply = reply[:2000]


@dataclass(kw_only=True, init=False)
class NestedSpendCap(SpendCap):
    """A cap for one unit of a larger run (one skill of a sweep), that also charges ``parent``.

    Every call is checked against both limits and recorded on both, so a sweep's whole-run
    budget holds across the skills running at once. The parent is charged first: a call the
    parent refuses or cannot price never reaches this cap's own count.
    """

    parent: SpendCap | None = None
    # Set to stop this unit at once: the call in flight ends on its own, and no later call starts.
    hard_stop: threading.Event | None = field(default=None, repr=False, compare=False)

    def __init__(self, limit_usd: float, spent_usd: float = 0.0, calls: int = 0, *,
                 parent: SpendCap | None = None, hard_stop: threading.Event | None = None,
                 **counts: int) -> None:
        super().__init__(limit_usd, spent_usd, calls, **counts)
        self.parent, self.hard_stop = parent, hard_stop

    def before_call(self, model: str) -> None:
        if self.hard_stop is not None and self.hard_stop.is_set():
            raise SpendCapReached(f"stopped by a second signal; not calling {model}", reason="stopped")
        super().before_call(model)
        if self.parent is not None:
            self.parent.before_call(model)

    def record(self, usage: UsageRecord | None, model: str) -> None:
        if self.parent is not None:
            self.parent.record(usage, model)
        super().record(usage, model)


@dataclass
class LLMClient:
    """One model in one role, with its usage recorded.

    Args:
        model: Model id passed to the provider.
        provider: Provider name understood by :mod:`dokimi.llm`.
        role: What this client is for (``extractor``, ``answerer``, ``judge``, …).
            Recorded on every usage row so cost can be attributed per role.
        max_tokens: Output ceiling; ``DEFAULT_MAX_TOKENS`` (16384) when unset.
            Reasoning models can spend it entirely on internal reasoning and return
            empty content, so novelty runs against them need this raised.
        transport: Injected call function; defaults to the real LLM on first use.
        cap: Spend limit shared with the run's other clients; ``None`` is unmetered.
    """

    model: str
    provider: str = "openai"
    role: str = "novelty"
    max_tokens: int | None = None
    transport: Transport | None = None
    usage: list[UsageRecord] = field(default_factory=list)
    calls: int = 0
    cap: SpendCap | None = None
    output_limit_configurable: bool = True

    def text(
        self, prompt: str, *, system: str | None = None, accept: Accept | None = None,
        draw: int = 0,
    ) -> str:
        """Return the model's reply as plain text.

        ``accept`` is the caller's test for a usable reply. The default transport passes
        it to the response cache as ``cache_if``: without it, with
        ``DOKIMI_RESPONSE_CACHE`` set, a cut-off or unparseable reply is stored and every
        later run replays the failure without calling the model. An injected transport
        is the caller's own and is not given it.

        ``draw`` is the sample index of this prompt. With the response cache on, a repeat
        of a byte-identical prompt replays draw 0 unless it has its own index, so a
        repeated ask is only a new sample when it passes a new one. Draw 0 is the default
        and keeps every existing cache entry. An injected transport is not given it.
        """
        if self.cap is not None:
            self.cap.before_call(self.model)
        if self.transport is not None:
            reply, usage = self.transport(prompt, system)
        else:
            reply, usage = self._default_transport(prompt, system, accept=accept, draw=draw)
        self.calls += 1
        if usage is not None:
            # UsageRecord is frozen; re-stamp the role so cost rolls up per role.
            self.usage.append(replace(usage, role=self.role))
        if self.cap is not None:
            try:
                self.cap.record(usage, self.model)
            except SpendCapReached as exc:
                exc.reply = reply or ""  # the call is paid for: the caller keeps its reply
                exc.truncated = usage is not None and usage.truncated
                raise
        # After the record: the cut call was paid for, whatever its text is worth. An empty
        # reply is no reply: its caller's empty-reply path handles it, and a split cannot help.
        if usage is not None and usage.truncated and (reply or "").strip():
            cli = cli_backend(self.provider) is not None
            ceiling = ("the CLI's own limit" if cli
                       else f"max_tokens={self.max_tokens or DEFAULT_MAX_TOKENS}")
            raise OutputTruncated(
                f"reply cut at the output ceiling ({self.model}, {ceiling}, "
                f"{usage.completion_tokens} tokens)" + ("; raise --max-output-tokens" if self.output_limit_configurable and not cli else ""),
                reply=reply or "")
        return reply or ""

    def json_object(
        self, prompt: str, *, system: str | None = None,
        usable: Callable[[dict[str, Any]], bool] | None = None,
        strict: bool = True,
    ) -> dict[str, Any]:
        """Return the model's reply parsed as a JSON object; raise ``ValueError`` otherwise.

        ``usable`` is the caller's test for the object's shape (``{"verdict": null}``
        parses, and is no verdict). It decides only what the response cache keeps: an
        object it rejects is still returned, for the caller to treat as it treats a bad
        reply, but is never stored or replayed, so a later run asks again.
        """

        def shaped(reply: str) -> dict[str, Any]:
            value = parse_json_object(reply, strict=strict)
            if usable is not None and not usable(value):
                raise ValueError("unusable reply")
            return value

        return parse_json_object(self.text(prompt, system=system, accept=accepting(shaped)), strict=strict)

    def json_array(self, prompt: str, *, system: str | None = None) -> list[Any]:
        """Return the model's reply parsed as a JSON array; raise ``ValueError`` otherwise."""
        from dokimi.llm import extract_json_array

        return extract_json_array(
            self.text(prompt, system=system, accept=accepting(extract_json_array))
        )

    def cost_summary(self) -> dict[str, Any]:
        """Roll up this client's usage.

        ``usd`` stays ``None`` when any call this run paid for had unknown pricing or
        returned no usage (a replayed call is not paid for, so it cannot make it unknown),
        rather than silently reporting the sum of the priced subset as the total.
        Calls served from the response cache are counted in ``replayed_calls`` and
        left out of the token and dollar totals.
        """
        return {"model": self.model, "provider": self.provider, "role": self.role,
                **_cost_block(self.spend())}

    def spend(self) -> Spend:
        """This client's calls as a :class:`Spend`; a call that returned no usage row has no price."""
        spend = Spend()
        for usage in [*self.usage, *[None] * (self.calls - len(self.usage))]:
            spend.add_call(usage)
        return spend

    def _default_transport(
        self, prompt: str, system: str | None, *, accept: Accept | None = None,
        draw: int = 0,
    ) -> tuple[str, UsageRecord | None]:
        from dokimi.llm import llm_call_with_usage

        return llm_call_with_usage(
            prompt,
            model=self.model,
            provider=self.provider,
            system=system,
            max_tokens=self.max_tokens or DEFAULT_MAX_TOKENS,
            cache_if=accept,
            draw=draw,
        )


def parse_json_object(reply: str, *, strict: bool = True) -> dict[str, Any]:
    """The reply as a JSON object. A list, ``null`` or scalar raises ``ValueError``.

    :func:`dokimi.llm.extract_json_object` returns whatever ``json.loads`` gives, so a
    ``[]`` reply used to come back as a list and fail later, at a ``.get``, outside any
    handler that knew it was a bad reply.
    """
    from dokimi.llm import extract_json_object

    value = extract_json_object(reply, strict=strict)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}: {reply[:120]!r}")
    return value


def accepting(parse: Callable[[str], Any]) -> Accept:
    """``parse`` as a response-cache acceptance test: usable means it does not raise."""

    def accept(reply: str) -> bool:
        try:
            parse(reply)
        except Exception:  # noqa: BLE001 - an accept predicate: any parse failure rejects the reply
            return False
        return True

    return accept


def roll_up_cost(clients: list[LLMClient]) -> dict[str, Any]:
    """Total cost across roles, with the per-role breakdown kept.

    The roles' calls are summed as one :class:`Spend`: a role that made no call adds nothing,
    so a run stopped before the judge's first call still has a dollar total.
    """
    return {**_cost_block(sum((c.spend() for c in clients), Spend())),
            "by_role": [c.cost_summary() for c in clients]}


def _cost_block(spend: Spend) -> dict[str, Any]:
    """A ``Spend`` under the field names of a ``novelty.v1`` cost block."""
    return {
        "llm_calls": spend.calls,
        "input_tokens": spend.prompt_tokens,
        "output_tokens": spend.completion_tokens,
        "cache_read_tokens": spend.cache_read_tokens,
        "cache_write_tokens": spend.cache_write_tokens,
        "replayed_calls": spend.replayed_calls,
        "usd": spend.usd,
    }


def same_client(a: LLMClient, b: LLMClient) -> bool:
    """Whether two clients run one model: the same name, or a CLI alias and a full id of its family."""
    from dokimi.utils import same_model

    return a.model == b.model or same_model(a.model, b.model)


# Substring of a lowercased model id -> vendor, first match wins. Minimal on purpose:
# PR #429 (novelty/provenance.py vendor_of) reads the same thing; reconcile on merge.
_VENDOR_MARKERS = (
    ("claude", "anthropic"), ("anthropic", "anthropic"), ("sonnet", "anthropic"),
    ("opus", "anthropic"), ("haiku", "anthropic"),
    ("gpt", "openai"), ("openai", "openai"), ("gemini", "google"), ("mistral", "mistral"),
    ("grok", "xai"), ("xai", "xai"),
)


def vendor_of(model: str | None) -> str | None:
    """The vendor family a model id names (``us.anthropic.claude-...`` is ``anthropic``), else ``None``."""
    lowered = (model or "").lower()
    return next((vendor for marker, vendor in _VENDOR_MARKERS if marker in lowered), None)


def extractor_judge_relation(extractor_model: str, judge: LLMClient,
                             extractor_provider: str = "") -> dict:
    """How the model that wrote the answer key relates to the judge, who grades against it.

    Takes a name, not a client: a saved atoms file records only the extractor's model id.
    """
    from dokimi.utils import same_model

    # the same id behind a routing prefix or a Bedrock spelling is one model (utils.same_model, the one rule)
    identical = extractor_model == judge.model or same_model(extractor_model, judge.model)
    vendor = vendor_of(extractor_model)
    return {
        "extractor": extractor_model,
        "judge": judge.model,
        "same_model": identical,
        "same_vendor": identical or (vendor is not None and vendor == vendor_of(judge.model)),
    }


# analyze writes its own answer key in the run, so re-running it with another extractor helps.
_REMEDY_ANALYZE = ("Use a judge from another family, or write the answer key again with another extractor "
                   "(analyze --extract-model, with --extract-provider when it is not the judge's)")
# probe reads a saved atoms file: only atoms can write that key again; probe's flags do not change it.
_REMEDY_PROBE = ("Use a judge from another family, or write the atoms file again with another extractor "
                 "(atoms --extract-model with --provider; probe's --extract-model does not change a saved key)")


def warn_extractor_judge(extractor_model: str, judge: LLMClient,
                         extractor_provider: str = "", remedy: str = _REMEDY_ANALYZE) -> dict:
    """Print a stderr warning when the judge is the answer key's model or its vendor family."""
    relation = extractor_judge_relation(extractor_model, judge, extractor_provider)
    if relation["same_model"]:
        print(f"WARNING: the extractor and the judge are both {judge.model}: one model wrote the "
              f"answer key and grades against it. {remedy} (docs/skill-novelty.md, Choosing the "
              "extractor and the judge)", file=sys.stderr)
    elif relation["same_vendor"]:
        print(f"WARNING: the extractor ({extractor_model}) and the judge ({judge.model}) are one "
              "vendor family: the judge may favour an answer key its own family wrote. "
              f"{remedy} (docs/skill-novelty.md, Choosing the extractor and the judge)",
              file=sys.stderr)
    return relation
