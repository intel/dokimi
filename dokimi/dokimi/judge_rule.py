# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The one default-judge rule, shared by every surface that judges.

It lives beside :mod:`dokimi.utils` (for :func:`same_model`) rather than in
:mod:`dokimi.defaults`, which utils imports: a defaults module importing utils would close
an import cycle.
"""

from dokimi import llm_cli
from dokimi.defaults import (
    BEDROCK_MODEL_IDS,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_GENERATOR_PROVIDER,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
)
from dokimi.utils import same_model

#: The Claude CLI aliases, in the order a judge is picked from them.
_CLI_ALIASES = ("opus", "sonnet", "haiku")


class JudgeIsAnswerer(ValueError):
    """The only default judge on the named ``--judge-provider`` is the model under measurement."""


def _claude_cli_judge(answerer: str | None, provider: str) -> tuple[str, str]:
    # The first alias not of the answerer's family. The model is compared, not a bare name:
    # ``myorg/sonnet`` is not the CLI's sonnet.
    for alias in _CLI_ALIASES:
        if answerer is None or not same_model(answerer, alias):
            return alias, provider
    raise ValueError(f"judge provider {provider}: no judge of another family than {answerer}; "
                     "pass --judge-model as well")


def _default_pair(answerer: str | None, provider: str | None) -> tuple[str, str]:
    pairs = ((DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER), (DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER))
    candidates = [(m, p) for m, p in pairs if answerer is None or not same_model(answerer, m)]

    def runs_there(model: str, model_provider: str) -> bool:
        return model_provider == provider or (provider == "amazon-bedrock" and model in BEDROCK_MODEL_IDS)

    if provider is None:
        return candidates[0]
    for m, p in candidates:
        if runs_there(m, p):
            return m, provider
    if any(runs_there(m, p) for m, p in pairs):
        raise JudgeIsAnswerer(f"--judge-provider {provider}: the default judge there is the model under "
                              f"measurement ({answerer}); pass --judge-model as well")
    raise ValueError(f"--judge-provider {provider} has no default judge model; pass --judge-model")


def default_judge(answerer: str | None, provider: str | None = None,
                  answerer_provider: str | None = None) -> tuple[str, str]:
    """A (model, provider) judge that is not the answerer, when the caller did not name one.

    Every surface (``novelty``, ``benchmark``, ``eval``, ``evaluate``, ``arms``, ``subjects``,
    ``answer_formats`` and the library classes) takes its judge from here. The default judge,
    unless it is the model under measurement (by ``same_model``, so a Bedrock
    ``us.anthropic.claude-...`` id counts too); then the default generator, which is of the
    other family. With ``provider`` (an explicit ``--judge-provider``) only a default that runs
    there is taken: its own provider, or Bedrock for a name in ``BEDROCK_MODEL_IDS``. Pairing
    the provider with a model of another provider would send e.g. ``bedrock/gpt-6.1-sol``, so
    with no such default this raises instead. On ``claude-cli`` the judge is the first Claude
    alias (``opus``, ``sonnet``, ``haiku``) of another family than the answerer. A CLI
    ``answerer_provider`` with no ``provider`` judges on the same CLI. ``answerer`` ``None``
    (a command with no answerer) excludes nothing.

    Raises:
        JudgeIsAnswerer: ``provider`` runs a default judge, but it is ``answerer``.
        ValueError: ``provider`` runs no default judge at all.
    """
    if provider is None and answerer_provider is not None and llm_cli.backend(answerer_provider) is not None:
        provider = answerer_provider
    # Hard-coded for claude-cli; a second CLI backend (codex-cli) needs its own alias list here.
    if provider == "claude-cli":
        return _claude_cli_judge(answerer, provider)
    return _default_pair(answerer, provider)


def apply_default_judge(args, *, matrix: bool, named: bool) -> None:
    """Fill ``args.judge_model`` (and ``judge_provider`` on a CLI answerer) with :func:`default_judge`.

    The answerer is ``args.model`` and ``args.provider``, unless *matrix*: the cells of a matrix set their own
    models, so the top-level one is no answerer. *named* says the caller gave ``--judge-provider``, so a CLI answerer
    does not move the judge to its own CLI. A run that does not judge (no ``--judge``) keeps the base default instead
    of the refused one. Raises :class:`JudgeIsAnswerer` or ``ValueError`` when no judge can be picked.
    """
    answerer, answerer_provider = (None, None) if matrix else (getattr(args, "model", None),
                                                               getattr(args, "provider", None))
    on_cli = answerer_provider is not None and llm_cli.backend(answerer_provider) is not None
    follow_answerer = on_cli and not named
    try:
        judge_model, picked = default_judge(answerer, None if follow_answerer else args.judge_provider,
                                            answerer_provider)
    except JudgeIsAnswerer:
        if getattr(args, "judge", True):
            raise
        args.judge_model, _ = default_judge(None, args.judge_provider)
        return
    args.judge_model = judge_model
    if follow_answerer:
        args.judge_provider = picked


def follow_answerer_cli(args, *, matrix: bool, named: bool) -> None:
    """An explicit ``--judge-model`` with ``--judge-provider`` left out judges on a CLI answerer's CLI.

    The left-out judge model already does (:func:`apply_default_judge`); an alias given by name, such as ``haiku``,
    must not reach the Anthropic API, where it is not a model id. A run that does not judge is left alone.
    """
    answerer_provider = None if matrix else getattr(args, "provider", None)
    if (named or not getattr(args, "judge", True) or getattr(args, "judge_model", None) is None
            or answerer_provider is None or llm_cli.backend(answerer_provider) is None):
        return
    args.judge_provider = answerer_provider
