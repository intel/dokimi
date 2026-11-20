# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The packages installed from requirements.lock.txt run a ``skill_agent:`` arm.

The real ``litellm.completion`` runs with ``mock_response``: it sends nothing, but imports what
a call that offers tools imports, which on litellm 1.106.0 to 1.108.3 is ``mcp`` (BerriAI/litellm#46045).
"""

import pytest

from dokimi.eval.arm_runner import ArmRunner
from dokimi.skills import load_skill
from dokimi.treatments import SkillAgentTreatment

REPLY = "The demo widget listens on port 4242."


@pytest.fixture
def offline_litellm(monkeypatch):
    """``litellm.completion`` with its real import path and a canned reply; the calls land in the list."""
    import litellm

    from dokimi import caching

    real, calls = litellm.completion, []

    def completion(**kwargs):
        calls.append(kwargs)
        return real(**kwargs, mock_response=REPLY)

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return calls


def test_a_skill_agent_arm_answers_with_the_locked_dependencies(offline_litellm, demo_skill):
    """On litellm 1.106.0 through 1.108.3 every agent arm answer was ``agent_failed: No module named 'mcp'``."""
    runner = ArmRunner([SkillAgentTreatment(load_skill(demo_skill))], model="gpt-4o", provider="openai")
    arm = runner.run("demo", [{"id": "Q1", "question": "Which port does the demo widget use?"}],
                     concurrency=1)[0]["arms"]["skill_agent:demo-skill"]
    assert arm.get("error") is None
    assert arm["answer"] == REPLY
    assert [bool(call.get("tools")) for call in offline_litellm] == [True]
