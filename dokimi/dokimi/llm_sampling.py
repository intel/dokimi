# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Whether litellm refuses one sampling value on one route, before any request is sent.

Kept apart from ``dokimi.llm`` (which it sizes) so the name check there can ask it
without an import cycle.
"""

from __future__ import annotations

from typing import Any


def value_refused(litellm: Any, resolved: str, provider: str, name: str, value: Any) -> bool:
    """True only when litellm (passed in; dokimi.llm owns the import) refuses this value on this route.

    litellm judges the bare id plus its provider; the ``bedrock/``-prefixed form accepts
    temperature=0 for a model that refuses it under the bare id, so the prefix is split off.
    Any other failure reads as accepted.
    """
    route, _, bare = resolved.rpartition("/")
    try:
        litellm.get_optional_params(model=bare, custom_llm_provider=route or provider,
                                    **{name: value})
    except litellm.UnsupportedParamsError:
        return True
    except Exception:  # noqa: BLE001 - a probe failure must not block a run
        return False
    return False
