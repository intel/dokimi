# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Edge cases of `catalog check --online`: install forms, code spans and proxy errors.

Split from test_catalog_online.py, which is held to the 1000-line growth cap.
"""

import socket

import pytest

from dokimi.external import online_checks
from dokimi.utils import InputError
from tests.test_catalog_online import _no_environment_proxy  # noqa: F401  (autouse)


def test_pip3_and_npm_add_name_their_packages():
    assert online_checks.extract_packages("pip3 install nosuch-pkg-xyz\n") == [("pypi", "nosuch-pkg-xyz")]
    assert online_checks.extract_packages("npm add nosuch-pkg-xyz\n") == [("npm", "nosuch-pkg-xyz")]


def test_resolver_failure_of_a_no_proxy_host_is_a_dns_failure(monkeypatch):
    # NO_PROXY sends docs.acme.dev direct, so its resolver failure is the link's own, not the proxy's.
    monkeypatch.setenv("HTTPS_PROXY", "http://missing-proxy.invalid:8080")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "docs.acme.dev")

    def no_such_host(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", no_such_host)
    assert online_checks._link_outcome(online_checks.make_client(), "https://docs.acme.dev/x").status == "dns"


def test_a_url_in_a_double_backtick_or_multi_line_code_span_is_not_probed():
    assert online_checks.extract_urls("Example: ``https://docs.acme.dev/old``\n") == []
    assert online_checks.extract_urls("Example: `see\nhttps://docs.acme.dev/old`\n") == []
    # a code span does not run over a blank line, so the URL after the stray backtick is a link
    assert online_checks.extract_urls("A `stray\n\nhttps://docs.acme.dev/live`\n") == ["https://docs.acme.dev/live"]


def test_a_socks_proxy_without_socksio_is_an_input_error_not_a_traceback(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")

    def no_socksio(*args, **kwargs):
        raise ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")

    monkeypatch.setattr(online_checks.httpx, "Client", no_socksio)
    with pytest.raises(InputError, match="socksio"):
        online_checks.make_client()


@pytest.mark.parametrize("start", ["- ", "1. ", "# ", "| ", "> "])
def test_a_code_span_stops_at_the_next_block_so_a_stray_backtick_does_not_hide_its_url(start):
    # a lone backtick does not open a span that runs into a list item, heading, table row or quote below it
    assert online_checks.extract_urls(f"A `stray\n{start}https://docs.acme.dev/live`\n") == [
        "https://docs.acme.dev/live"]
