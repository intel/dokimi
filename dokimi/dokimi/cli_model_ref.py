# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``--model P/M`` and ``--judge P/M``: one flag for a model and its provider (issue #94 §2.4, F1b model concept).

The old ``--provider`` and ``--judge-provider`` flags are gone; their value moved into the front of the model flag.
A value whose part before the first slash is a provider on the leaf sets the provider too. A bare model name
(``haiku``, or ``meta-llama/x`` where ``meta-llama`` is no provider) leaves the provider at the leaf's default, as
``--model`` alone always did.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable

from dokimi.llm import ANSWER_PROVIDERS


class ProviderModelAction(argparse.Action):
    """Store the model; when the value names a provider, store that provider in ``provider_dest`` too.

    A prefix that names a provider dokimi knows, but not one this command takes, is refused: the value
    was meant as a provider, and running it as a model name on the default provider would hide the mistake.
    Any other prefix (``meta-llama/x`` on openrouter) is a model name.
    """

    def __init__(self, option_strings, dest, *, provider_dest: str, providers: Iterable[str],
                 known: Iterable[str] = (), named_dest: str | None = None, **kwargs):
        self.provider_dest = provider_dest
        self.providers = tuple(providers)
        self.known = frozenset(known)
        self.named_dest = named_dest
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        provider, sep, model = str(values).partition("/")
        if sep and provider in self.providers:  # `P/` (empty model) is P with no model: the provider's check reports it
            setattr(namespace, self.provider_dest, provider)
            if self.named_dest:
                setattr(namespace, self.named_dest, True)
            setattr(namespace, self.dest, model)
        elif sep and provider in self.known and model.strip():
            parser.error(f"{option_string}: `{provider}` is not a provider for this command; "
                         f"use one of {', '.join(sorted(self.providers))}")
        else:
            setattr(namespace, self.provider_dest, parser.get_default(self.provider_dest))
            if self.named_dest:
                setattr(namespace, self.named_dest, False)
            setattr(namespace, self.dest, values)


def add_model_flag(parser: argparse.ArgumentParser, option: str, *, dest: str, provider_dest: str,
                   providers: Iterable[str], default_model: object, default_provider: str | None,
                   named_dest: str | None = None, help: str | None = None) -> argparse.Action:
    """Add ``option PROVIDER/MODEL`` storing the model in ``dest`` and the provider in ``provider_dest``.

    ``default_provider`` is the provider a bare model (or no model) runs on, the default ``--provider`` used to be.
    """
    action = parser.add_argument(
        option, dest=dest, action=ProviderModelAction, provider_dest=provider_dest, providers=tuple(providers),
        known=ANSWER_PROVIDERS, named_dest=named_dest, default=default_model, metavar="PROVIDER/MODEL", help=help,
    )
    parser.set_defaults(**{provider_dest: default_provider})
    return action


_EMPTY_MODEL_CHECKS = (("--model", "provider", "model"), ("--judge", "judge_provider", "judge_model"))


def refuse_empty_model(parser: argparse.ArgumentParser, args: argparse.Namespace, *,
                       checked_elsewhere: Iterable[str] = ()) -> None:
    """Refuse ``--model P/`` and ``--judge P/``: a provider with no model, which would reach its API as an empty name.

    The old ``--judge-provider P`` alone was refused up front; the Bedrock judge check reports an empty model before
    sampling too. This keeps the other providers from sampling first and failing at the judge. A provider in
    ``checked_elsewhere`` (the CLI backends) has its own check in ``cli.parse_args``, which names the models it takes.
    """
    for flag, provider_attr, model_attr in _EMPTY_MODEL_CHECKS:
        provider = getattr(args, provider_attr, None)
        if provider and provider not in checked_elsewhere and getattr(args, model_attr, None) == "":
            parser.error(f"{flag} {provider}/ has no model: pass {flag} {provider}/<MODEL>")
