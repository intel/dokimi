# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from dokimi import llms_txt

GOOD = """# Acme API

> Payments for SaaS products.

## When to use

- Subscription billing

## When not to use

- Point of sale: use a terminal vendor

## Docs

- [Quickstart](https://acme.test/quickstart.md): first charge in five minutes
"""


def _checks(text: str) -> set[str]:
    return {f.check for f in llms_txt.check(text)}


def test_well_formed_file_passes():
    assert llms_txt.check(GOOD) == []


def test_missing_title_and_links_fail():
    found = llms_txt.check("just prose\n")
    assert {(f.check, f.severity) for f in found} >= {("title", "fail"), ("links", "fail")}


def test_missing_summary_is_reported_on_the_line_after_the_title():
    text = GOOD.replace("> Payments for SaaS products.\n", "")
    summary = [f for f in llms_txt.check(text) if f.check == "summary"]
    assert [(f.severity, f.line) for f in summary] == [("fail", 2)]


def test_when_not_heading_does_not_satisfy_when_to_use():
    text = GOOD.replace("## When to use\n", "## Overview\n")
    assert _checks(text) == {"when_to_use"}


def test_missing_negative_positioning_is_a_warning():
    text = GOOD.replace("## When not to use", "## Limits")
    assert _checks(text) == {"when_not_to_use"}


def test_undescribed_list_link_is_flagged_with_its_line():
    text = GOOD.replace(": first charge in five minutes", "")
    links = [f for f in llms_txt.check(text) if f.check == "links"]
    assert [f.line for f in links] == [text.splitlines().index(
        "- [Quickstart](https://acme.test/quickstart.md)") + 1]


def test_size_over_budget_is_flagged():
    assert "size" in _checks(GOOD + "x" * 9000) - _checks(GOOD)
    assert "size" not in {f.check for f in llms_txt.check(GOOD + "x" * 9000, token_budget=10_000)}
