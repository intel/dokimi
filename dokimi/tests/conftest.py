# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pytest configuration."""

import os
import sys
from pathlib import Path

# litellm downloads its model price map from GitHub on import unless told to use the copy
# it ships. With the download, cost assertions depend on upstream pricing edits and every
# test run needs the network. Set before anything imports litellm.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import pytest

pytest_plugins = ["tests.outcomes.plugin"]

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# A body token no other fixture or source file contains, so an assertion that the skill text
# reached a prompt cannot pass on some incidental substring.
DEMO_SKILL_MARKER = "demo_marker()"


@pytest.fixture
def demo_skill(tmp_path: Path) -> Path:
    """A minimal on-disk skill directory: ``<tmp>/demo-skill/SKILL.md``.

    The framework loads skills by path, so tests about arm naming, context assembly and
    telemetry need *a* skill, not a particular product's skill. The directory name is what
    :func:`load_skill` derives the arm name from, hence ``skill:demo-skill``.
    """
    d = tmp_path / "demo-skill"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: A minimal skill used as a test fixture.\n"
        "---\n"
        "# Demo skill\n\n"
        f"Call {DEMO_SKILL_MARKER} before answering.\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def fake_llm():
    """Every :class:`dokimi.llm.LLM` without its own adapter answers from this script.

    The seam for LLM calls in tests: push replies (text, a ``Reply``, or an exception
    to raise) and read back ``fake_llm.requests`` / ``fake_llm.prompts``. An unscripted
    call fails the test, so nothing reaches a provider.
    """
    from dokimi import llm

    fake = llm.FakeAdapter()
    with llm.use_adapter(fake):
        yield fake


@pytest.fixture
def provider_calls(monkeypatch):
    """Records every provider call a path makes and answers it (``tests/provider_calls.py``).

    The ``dokimi.llm`` adapter seam and the validator's embeddings client both write
    to one ledger, ``provider_calls.calls``. A request that skips the seam and calls
    litellm directly fails the test. The response cache is off (a test turns it on
    with ``DOKIMI_RESPONSE_CACHE``) and retry backoff does not sleep; the delays
    land in ``provider_calls.sleeps``.
    """
    import time
    from types import SimpleNamespace

    import litellm

    from dokimi import caching, llm
    from tests.provider_calls import FakeOpenAI, ProviderCalls

    ledger = ProviderCalls()

    def bypass(*_a, **_kw):
        message = "a provider call bypassed the dokimi.llm adapter"
        ledger.bypasses.append(message)
        raise AssertionError(message)

    monkeypatch.setattr(litellm, "completion", bypass)
    monkeypatch.setattr(litellm, "embedding", bypass)
    monkeypatch.setattr(
        "dokimi.questions.validator.OpenAI", lambda **kw: FakeOpenAI(ledger, **kw), raising=False
    )
    # Only llm.py's own sleep is swapped: patching ``time.sleep`` would swallow every sleeper.
    monkeypatch.setattr(llm, "time", SimpleNamespace(time=time.time, sleep=ledger.sleeps.append))
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    with llm.use_adapter(ledger):
        yield ledger
    assert not ledger.bypasses, ledger.bypasses
