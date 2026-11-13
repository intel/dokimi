# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The pytest ignore for pydantic's ReadOnly notice, as pyproject.toml declares it.

pydantic warns that it does not enforce ``ReadOnly`` on a TypedDict; litellm ignores the
notice process-wide, but pytest resets the filters for each test. These tests run under
the suite's own filters, where any other warning is an error.
"""
from __future__ import annotations

import warnings

import pytest
from pydantic import TypeAdapter
from typing_extensions import ReadOnly, TypedDict

MODULE = "pydantic._internal._generate_schema"
NOTICE = ("Item 'index' on TypedDict class 'Block' is using the `ReadOnly` qualifier. "
          "Pydantic will not protect items from any mutation on dictionary instances.")


def _warn(message: str, category: type[Warning] = UserWarning, module: str = MODULE) -> None:
    warnings.warn_explicit(message, category, "x.py", 1, module=module)


def test_a_typeddict_with_one_readonly_item_builds():
    class Block(TypedDict):
        index: ReadOnly[int]
        text: str

    assert TypeAdapter(Block).validate_python({"index": "1", "text": "a"}) == {"index": 1, "text": "a"}


def test_a_typeddict_with_several_readonly_items_builds():
    class DeviceCode(TypedDict):
        device_code: ReadOnly[str]
        user_code: ReadOnly[str]

    assert TypeAdapter(DeviceCode).validate_python({"device_code": "d", "user_code": "u"})["user_code"] == "u"


def test_a_typeddict_with_readonly_extra_items_builds():
    class Labels(TypedDict, extra_items=ReadOnly[int]):
        name: str

    assert TypeAdapter(Labels).validate_python({"name": "a", "count": "2"}) == {"name": "a", "count": 2}


@pytest.mark.parametrize(("message", "category", "module"), [
    ("A field name shadows an attribute in parent 'Base'", UserWarning, MODULE),
    (NOTICE, UserWarning, "dokimi.records"),
    (NOTICE, DeprecationWarning, MODULE),
], ids=["other-pydantic-notice", "other-module", "other-category"])
def test_only_that_notice_is_ignored(message, category, module):
    with pytest.raises(category):
        _warn(message, category, module)
