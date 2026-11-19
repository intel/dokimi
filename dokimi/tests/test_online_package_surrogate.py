# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A package name with a lone surrogate is looked up with a ``?`` instead of raising (#862)."""

from __future__ import annotations

import httpx
import pytest

from dokimi.external import online_checks

PYPI = "https://pypi.org/simple/{name}/"
NPM = "https://registry.npmjs.org/{name}"


@pytest.mark.parametrize("eco", ["pypi", "npm"])
@pytest.mark.parametrize(("name", "quoted"), [
    pytest.param("n\ud800", "n%3F", id="d800"),
    pytest.param("n\udcff", "n%3F", id="dcff"),
    pytest.param("n" + chr(0xD83D) + chr(0xDE00), "n%F0%9F%98%80", id="pair"),  # a JSON escape pair of U+1F600
])
def test_a_package_name_with_a_lone_surrogate_is_looked_up_with_a_question_mark(eco, name, quoted):
    asked = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    outcome = online_checks._package_finding(client, eco, name)
    assert set(asked) == {(PYPI if eco == "pypi" else NPM).format(name=quoted)}  # a 404 to HEAD is asked again with GET
    assert outcome.finding is not None and outcome.finding.code == "package-not-found"
    outcome.finding.message.encode("utf-8")


def test_a_scoped_npm_name_with_a_lone_surrogate_keeps_its_at_sign():
    asked = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    outcome = online_checks._package_finding(client, "npm", "@n\ud800/x")
    assert set(asked) == {NPM.format(name="@n%3F%2Fx")}
    assert outcome.finding is not None and outcome.finding.code == "package-not-found"
