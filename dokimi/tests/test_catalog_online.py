# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --online``: packages missing from PyPI or npm, and dead links. Mocked HTTP only."""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from dokimi.cli import build_parser
from dokimi.external import online_checks, read_skill_manifest, review_skills
from dokimi.utils import InputError
from tests.test_catalog_validate import _skill

PYPI = "https://pypi.org/simple/{name}/"
NPM = "https://registry.npmjs.org/{name}"


class Web:
    """A fake network. ``answers`` maps ``(method, url)`` or ``url`` to a status code or an exception."""

    def __init__(self, answers=None, default=200):
        self.answers = answers or {}
        self.default = default
        self.requests: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        answer = self.answers.get((request.method, url), self.answers.get(url, self.default))
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle), follow_redirects=True)

    def hits(self, url: str) -> int:
        return sum(1 for _, seen in self.requests if seen == url)


def _dns_failure() -> httpx.ConnectError:
    error = httpx.ConnectError("[Errno -2] Name or service not known")
    error.__cause__ = socket.gaierror(-2, "Name or service not known")
    return error


def _reviews(root, web: Web):
    """Review every skill under ``root`` the way ``catalog check --online`` does, with ``web`` as the network."""
    entries = read_skill_manifest(root)
    reviews = review_skills(entries)
    online_checks.add_online_findings(
        [(e.skill, r) for e, r in zip(entries, reviews, strict=True) if e.loaded and r is not None],
        client=web.client(),
    )
    return reviews


def _codes(review) -> list[str]:
    return [f.code for f in review.findings]


def _one_skill(tmp_path, body: str):
    _skill(tmp_path / "skills", "tool", name="tool", description="Use for tool work.", body=body)
    return tmp_path


# ---- which packages and links are checked -------------------------------------------------


def test_pip_and_npm_install_lines_name_the_packages():
    body = (
        "```bash\npip install requests==2.31 numpy\n"
        "npm install -g @anthropic-ai/server-postgres@1.0\nnpx -y create-thing --flag\n```\n"
    )
    assert online_checks.extract_packages(body) == [
        ("pypi", "requests"), ("pypi", "numpy"),
        ("npm", "@anthropic-ai/server-postgres"), ("npm", "create-thing"),
    ]


def test_options_with_values_and_non_registry_targets_are_not_packages():
    body = (
        "pip install -r requirements.txt\npip install -e .\npip install git+https://github.com/x/y\n"
        "pip install ./local\nnpm install ./pkg.tgz\nnpm install github:user/repo\n"
        "npm install --prefix build foo\n"
    )
    assert online_checks.extract_packages(body) == [("npm", "foo")]


def test_prose_that_mentions_pip_is_not_an_install_command():
    assert online_checks.extract_packages("Tell the user pip install is slow.\n") == []


def test_placeholder_package_names_are_not_checked():
    body = "pip install <package>\npip install your-package\nnpm install ${PKG}\n"
    assert online_checks.extract_packages(body) == []


def test_placeholder_and_template_urls_are_not_checked():
    body = (
        "See https://example.com/docs, http://localhost:8000/x, http://127.0.0.1/y,\n"
        "https://your-org.example.org/z and https://api.x.com/<id> or https://h.io/{id}.\n"
    )
    assert online_checks.extract_urls(body) == []


def test_real_link_in_markdown_keeps_its_path_and_drops_the_closing_paren():
    body = "Read [the docs](https://docs.python.org/3/library/) first.\n"
    assert online_checks.extract_urls(body) == ["https://docs.python.org/3/library/"]


def test_your_word_in_a_path_is_not_a_placeholder_host():
    body = "See https://github.com/acme/your-tool/docs and https://your-org.example-tool.dev/x\n"
    assert online_checks.extract_urls(body) == ["https://github.com/acme/your-tool/docs"]


def test_malformed_bracketed_host_is_skipped_not_fatal():
    body = "See http://[broken/x and [the guide](https://docs.example-tool.dev/ok).\n"
    assert online_checks.extract_urls(body) == ["https://docs.example-tool.dev/ok"]


def test_npx_package_equals_form_names_the_package():
    assert online_checks.extract_packages("npx --package=@scope/cli mytool\n") == [("npm", "@scope/cli")]


def test_pip_options_with_values_are_not_packages():
    body = "pip install --python-version 3.11 --platform manylinux2014_x86_64 --upgrade-strategy eager numpy\n"
    assert online_checks.extract_packages(body) == [("pypi", "numpy")]


def test_urls_are_capped_per_skill():
    body = "\n".join(f"https://site{i}.dev/p" for i in range(25))
    assert len(online_checks.extract_urls(body)) == online_checks.MAX_URLS_PER_SKILL == 20


# ---- what the registry and the web answer --------------------------------------------------


def test_package_missing_on_pypi_is_a_warning(tmp_path):
    root = _one_skill(tmp_path, "Run `pip install nosuch-pkg-xyz`.\n")
    web = Web({PYPI.format(name="nosuch-pkg-xyz"): 404})

    [review] = _reviews(root, web)
    assert _codes(review) == ["package-not-found"]
    assert review.state == "warn"
    assert "nosuch-pkg-xyz" in review.findings[0].message


def test_npm_scoped_package_missing_from_the_registry_is_a_warning(tmp_path):
    root = _one_skill(tmp_path, "```\nnpm install -g @anthropic-ai/server-postgres\n```\n")
    web = Web({NPM.format(name="@anthropic-ai%2Fserver-postgres"): 404})

    [review] = _reviews(root, web)
    assert _codes(review) == ["package-not-found"]


def test_package_that_exists_is_not_a_finding(tmp_path):
    root = _one_skill(tmp_path, "pip install requests\n")

    [review] = _reviews(root, Web(default=200))
    assert review.findings == []


@pytest.mark.parametrize("status", [403, 429, 503])
def test_registry_refusals_are_not_findings(tmp_path, status):
    root = _one_skill(tmp_path, "pip install requests\n")

    [review] = _reviews(root, Web(default=status))
    assert review.findings == []


def test_network_error_on_a_package_is_not_a_finding(tmp_path):
    root = _one_skill(tmp_path, "pip install requests\n")
    web = Web({PYPI.format(name="requests"): httpx.ConnectTimeout("timed out")})

    [review] = _reviews(root, web)
    assert review.findings == []


def test_dead_link_404_on_head_and_get_is_a_warning(tmp_path):
    url = "https://docs.example-tool.dev/guide"
    root = _one_skill(tmp_path, f"See [the guide]({url}).\n")
    web = Web({url: 404})

    [review] = _reviews(root, web)
    assert _codes(review) == ["link-dead"]
    assert url in review.findings[0].message
    assert [m for m, _ in web.requests] == ["HEAD", "GET"]


def test_410_gone_is_a_dead_link(tmp_path):
    root = _one_skill(tmp_path, "See https://docs.example-tool.dev/old.\n")

    [review] = _reviews(root, Web({"https://docs.example-tool.dev/old": 410}))
    assert _codes(review) == ["link-dead"]


def test_head_refused_but_get_working_is_not_dead(tmp_path):
    url = "https://docs.example-tool.dev/head-refused"
    root = _one_skill(tmp_path, f"See {url}\n")
    web = Web({("HEAD", url): 405, ("GET", url): 200})

    [review] = _reviews(root, web)
    assert review.findings == []


@pytest.mark.parametrize("status", [403, 500, 503])
def test_403_and_5xx_are_not_dead(tmp_path, status):
    url = "https://docs.example-tool.dev/guarded"
    root = _one_skill(tmp_path, f"See {url}\n")

    [review] = _reviews(root, Web(default=status))
    assert review.findings == []


def test_dns_failure_is_a_dead_link(tmp_path):
    url = "https://gone-host.example-tool.dev/"
    root = _one_skill(tmp_path, f"See {url}\n")
    web = Web({url: _dns_failure()})

    [review] = _reviews(root, web)
    assert _codes(review) == ["link-dead"]
    assert "does not resolve" in review.findings[0].message


def test_timeout_on_a_link_is_not_dead(tmp_path):
    url = "https://slow.example-tool.dev/"
    root = _one_skill(tmp_path, f"See {url}\n")

    [review] = _reviews(root, Web({url: httpx.ReadTimeout("slow")}))
    assert review.findings == []


def test_each_package_and_url_is_requested_once_per_run(tmp_path):
    url = "https://docs.example-tool.dev/shared"
    for name in ("first", "second"):
        _skill(tmp_path / "skills", name, name=name, description=f"Use for {name} work.",
               body=f"pip install requests\nSee {url}\n")
    web = Web()

    reviews = _reviews(tmp_path, web)
    assert len(reviews) == 2
    assert web.hits(PYPI.format(name="requests")) == 1
    assert web.hits(url) == 1  # one HEAD for the single probe (200), never one per skill


def test_probes_use_a_five_second_timeout():
    assert online_checks.make_client().timeout == httpx.Timeout(5.0)


def test_placeholder_hosts_make_no_request(tmp_path):
    root = _one_skill(tmp_path, "See https://example.com/a and http://localhost/b and https://api.x.com/<id>\n")
    web = Web()

    _reviews(root, web)
    assert web.requests == []


# ---- the flag ------------------------------------------------------------------------------


def test_without_online_no_request_is_made(tmp_path, monkeypatch, capsys):
    root = _one_skill(tmp_path, "pip install nosuch-pkg-xyz\nSee https://docs.example-tool.dev/dead\n")

    def no_network():
        raise AssertionError("catalog check without --online must not open a connection")

    monkeypatch.setattr(online_checks, "make_client", no_network)
    code, report = _scan_capture(root, capsys)
    assert code == 0
    assert [f["code"] for f in report["skills"][0]["findings"]] == []


def test_online_reports_dead_links_and_packages_as_warnings(tmp_path, monkeypatch, capsys):
    root = _one_skill(tmp_path, "pip install nosuch-pkg-xyz\nSee https://docs.example-tool.dev/dead\n")
    web = Web({PYPI.format(name="nosuch-pkg-xyz"): 404, "https://docs.example-tool.dev/dead": 404})
    monkeypatch.setattr(online_checks, "make_client", web.client)

    code, report = _scan_capture(root, capsys, "--online")
    assert code == 0  # warnings only: the exit code does not change
    assert report["skills"][0]["state"] == "warn"
    assert sorted(f["code"] for f in report["skills"][0]["findings"]) == ["link-dead", "package-not-found"]


def test_online_with_per_skill_is_a_usage_error(tmp_path):
    parser = build_parser()
    args = parser.parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills",
                              "--per-skill", "--online"])
    with pytest.raises(InputError, match="--online"):
        args.func(args)


def _scan_capture(root, capsys, *extra: str) -> tuple[int, dict]:
    parser = build_parser()
    args = parser.parse_args(["catalog", "check", "--root", str(root), "--kind", "skills",
                              "--format", "json", *extra])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    return code, json.loads(capsys.readouterr().out)
