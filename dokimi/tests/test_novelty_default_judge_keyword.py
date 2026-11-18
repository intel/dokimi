# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi.novelty.client.default_judge keeps its ``model`` keyword, as before the shared rule."""

from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER
from dokimi.novelty.client import default_judge


def test_the_novelty_default_judge_takes_the_model_keyword():
    assert default_judge(model="gpt-6.1-sol") == (DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER)
