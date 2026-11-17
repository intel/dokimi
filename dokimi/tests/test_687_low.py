# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#687 Low: state that lies after a rerun or a failed step (https://github.com/intel/dokimi/issues/687)."""

from __future__ import annotations

from unittest.mock import Mock

from dokimi.personas.analyzer import PersonaAnalyzer

# ── personas/analyzer.py: the use-case section parser ─────────────────────────


def test_a_use_case_bullet_that_names_the_keyword_is_kept():
    readme = "## Use cases\n- Web applications\n- Batch jobs\n\n## Install\n- pip install x\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == ["web applications", "batch jobs"]


def test_a_bullet_that_names_the_keyword_opens_no_section():
    readme = "## Features\n- Runs any application\n- Fast\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == []


def test_a_bold_use_case_line_still_opens_a_section():
    readme = "**Use cases**\n* Robotics\n\n## Install\n- pip install x\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == ["robotics"]


def test_the_pygithub_path_keeps_a_keyword_bullet_too():
    repo = Mock()
    repo.get_readme.return_value.decoded_content = b"## When to use\n- Desktop applications\n- CLIs\n"
    assert PersonaAnalyzer()._extract_use_cases(repo) == ["desktop applications", "clis"]
