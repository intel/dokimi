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
    error.__cause__ = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
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


def test_your_placeholder_segments_are_skipped_but_a_word_containing_your_is_not():
    body = (
        "See https://github.com/your-username/your-repo, https://your-org.example-tool.dev/x\n"
        "and https://github.com/acme/tool-your-docs.\n"
    )
    assert online_checks.extract_urls(body) == ["https://github.com/acme/tool-your-docs"]


def test_malformed_bracketed_host_is_skipped_not_fatal():
    body = "See http://[broken/x and [the guide](https://docs.example-tool.dev/ok).\n"
    assert online_checks.extract_urls(body) == ["https://docs.example-tool.dev/ok"]


def test_npx_package_equals_form_names_the_package():
    assert online_checks.extract_packages("npx --package=@scope/cli mytool\n") == [("npm", "@scope/cli")]


def test_pip_options_with_values_are_not_packages():
    body = (
        "pip install --python-version 3.11 --platform manylinux2014_x86_64 --upgrade-strategy eager "
        "--retries 5 --use-feature 2020-resolver --keyring-provider import numpy\n"
    )
    assert online_checks.extract_packages(body) == [("pypi", "numpy")]


def test_urls_are_capped_per_skill():
    body = "\n".join(f"https://site{i}.dev/p" for i in range(25))
    assert len(online_checks.extract_urls(body)) == online_checks.MAX_URLS_PER_SKILL == 20


def test_backslash_continuation_is_a_documented_miss_and_names_nothing_wrong():
    # The line-based rule does not join continuation lines: the package on the next line is missed.
    body = "```bash\npip install \\\n  nosuch-pkg-xyz\n```\n"
    assert online_checks.extract_packages(body) == []


def test_quoted_requirement_with_a_semicolon_is_a_documented_miss():
    # Quotes are not read: the quoted requirement is not a valid name, so nothing is checked.
    body = "pip install \"requests; python_version < '3.11'\"\n"
    assert online_checks.extract_packages(body) == []


def test_shell_comments_are_removed_before_the_command_is_split():
    body = (
        "pip install requests # development-only\nnpm install lodash # development-only\n"
        "pip install requests # note; pip install nosuch-pkg-xyz\n"
    )
    assert online_checks.extract_packages(body) == [("pypi", "requests"), ("npm", "lodash")]


def test_redirection_operators_and_their_targets_are_not_packages():
    body = (
        "pip install requests > install.log\nnpm install lodash >> log.txt\n"
        "pip install requests 2>&1 | tee out\npip install numpy 2>/dev/null\n"
    )
    assert online_checks.extract_packages(body) == [("pypi", "requests"), ("npm", "lodash"), ("pypi", "numpy")]


def test_every_npx_package_option_is_checked_and_the_command_is_not_a_package():
    assert online_checks.extract_packages("npx --package=@scope/cli --package=nosuch-pkg-xyz tool\n") == [
        ("npm", "@scope/cli"), ("npm", "nosuch-pkg-xyz")]
    assert online_checks.extract_packages("npx -p @scope/cli -p nosuch-pkg-xyz tool\n") == [
        ("npm", "@scope/cli"), ("npm", "nosuch-pkg-xyz")]


def test_trailing_sentence_punctuation_is_not_part_of_a_name_and_prose_npx_names_nothing():
    assert online_checks.extract_packages("Run pip install requests.\nRun npm install lodash.\n") == [
        ("pypi", "requests"), ("npm", "lodash")]
    assert online_checks.extract_packages("Use npx to run things\n") == []


def test_npm_and_pip_value_options_take_their_argument():
    body = "npm install --omit dev lodash\npip install --python python3.11 foo\n"
    assert online_checks.extract_packages(body) == [("npm", "lodash"), ("pypi", "foo")]


def test_prose_with_an_apostrophe_still_splits_its_commands():
    body = "Run pip install requests; it's quick and painless\n"
    assert online_checks.extract_packages(body) == [("pypi", "requests")]


def test_prose_after_an_install_is_read_as_names_known_limit():
    # Known limit (warn-only): words after an install on the same line are names until the line or an operator
    # ends, and a conjunction does not end the list ("and pandas" is a name); "done" is a false name here.
    body = "Don't worry; pip install requests and you're done\n"
    assert online_checks.extract_packages(body) == [("pypi", "requests"), ("pypi", "done")]


def test_prose_word_that_is_not_a_stop_word_is_read_as_a_name_known_limit():
    # Known limit (warn-only): "quickly" is not on the stop list, so it is a false name here.
    assert online_checks.extract_packages("pip install requests quickly\n") == [
        ("pypi", "requests"), ("pypi", "quickly")]


def test_prose_words_after_a_bare_install_are_not_package_names():
    assert online_checks.extract_packages("Run npm install in the project directory.\n") == []
    assert online_checks.extract_packages("pip install foo for development\n") == [("pypi", "foo")]
    assert online_checks.extract_packages("run npm install locally to test\n") == []


def test_a_spaced_version_operator_takes_the_version_after_it():
    assert online_checks.extract_packages("pip install foo == 1.0rc1\n") == [("pypi", "foo")]
    assert online_checks.extract_packages("pip install foo >= 1.0 bar\n") == [("pypi", "foo"), ("pypi", "bar")]


def test_uv_pip_value_options_and_npm_access_take_their_argument():
    assert online_checks.extract_packages("uv pip install --prerelease allow foo\n") == [("pypi", "foo")]
    assert online_checks.extract_packages("uv pip install --resolution lowest --link-mode copy foo\n") == [
        ("pypi", "foo")]
    assert online_checks.extract_packages("npm install --access public foo\n") == [("npm", "foo")]


def test_a_stop_word_that_is_an_option_value_is_the_value_not_prose():
    assert online_checks.extract_packages("pip install --progress-bar on foo\n") == [("pypi", "foo")]
    assert online_checks.extract_packages("npm install --omit in lodash\n") == [("npm", "lodash")]


def test_a_prose_stop_word_with_a_full_stop_after_it_ends_the_list():
    assert online_checks.extract_packages("pip install -e . locally.\n") == []
    assert online_checks.extract_packages("npm install -g typescript globally.\n") == [("npm", "typescript")]
    assert online_checks.extract_packages("pip install requests here.\n") == [("pypi", "requests")]


def test_a_version_glued_to_an_operator_is_not_a_name():
    assert online_checks.extract_packages("pip install foo>= 1.0rc1\n") == [("pypi", "foo")]


def test_markdown_link_inside_emphasis_table_or_next_to_another_link_keeps_only_the_url():
    assert online_checks.extract_urls("_[docs](https://x.com/a)_") == ["https://x.com/a"]
    assert online_checks.extract_urls("|[docs](https://x.com/a)|desc|") == ["https://x.com/a"]
    assert online_checks.extract_urls("[a](https://x.com/a)[b](https://y.com/b)") == [
        "https://x.com/a", "https://y.com/b"]


def test_process_substitution_is_a_documented_miss_not_a_wrong_name():
    # The operators split the line, so the value option "-r" takes nothing and "foo" is not read.
    assert online_checks.extract_packages("pip install -r <(echo x) foo\n") == []


def test_quoted_parenthesis_does_not_hide_the_next_command():
    # Pass-6 blocker 1: a quoted "(" is a line operator here, and the next command is still read.
    assert online_checks.extract_packages('npm i foo; grep "(" x; npm i bar\n') == [("npm", "foo"), ("npm", "bar")]


def test_issue_reference_before_a_command_is_not_a_comment():
    # Pass-6 blocker 2: "#12" is not a comment (a comment is "#" followed by a space or the end of the line).
    assert online_checks.extract_packages("See #12; pip install foo\n") == [("pypi", "foo")]


def test_backslash_hard_break_in_prose_does_not_turn_prose_into_names():
    # Pass-6 blocker 3: the continuation is not joined, so the prose line is not read as an install.
    assert online_checks.extract_packages("pip install foo\\\nThen restart the server\n") == []


def test_nested_parentheses_do_not_hide_the_next_command():
    # Pass-6 blocker 4: each "(" and ")" is a line operator, so the nesting does not swallow the rest.
    assert online_checks.extract_packages("pip install foo (see (docs)) and npm i bar\n") == [
        ("pypi", "foo"), ("npm", "bar")]


def test_npx_verbs_after_npx_in_prose_are_not_packages():
    assert online_checks.extract_packages("npx can run any package\nnpx runs the binary\n") == []


def test_numbers_are_not_package_names():
    assert online_checks.extract_packages("pip install requests 2>&1\n") == [("pypi", "requests")]


def test_ampersand_redirection_target_is_not_a_package():
    assert online_checks.extract_packages("pip install requests &> log.txt\n") == [("pypi", "requests")]


def test_words_after_a_redirection_target_are_still_packages():
    # Red on the operator split: the redirect used to end the words, so "bar" was lost.
    assert online_checks.extract_packages("pip install foo > install.log bar\n") == [("pypi", "foo"), ("pypi", "bar")]
    assert online_checks.extract_packages("npm i foo 2>err.log bar\n") == [("npm", "foo"), ("npm", "bar")]
    assert online_checks.extract_packages("pip install foo 2>&1 bar\n") == [("pypi", "foo"), ("pypi", "bar")]


def test_every_redirection_form_drops_its_target_and_keeps_the_words_after_it():
    # Red (self-review pass 8): ">&file" redirects to a file, so "log" is a target, not a package; here-strings and
    # clobber ">|" are redirections too, and the words after their targets are packages.
    assert online_checks.extract_packages("pip install foo >&log bar\n") == [("pypi", "foo"), ("pypi", "bar")]
    assert online_checks.extract_packages("pip install foo >& log bar\n") == [("pypi", "foo"), ("pypi", "bar")]
    assert online_checks.extract_packages("pip install foo <<< bar baz\n") == [("pypi", "foo"), ("pypi", "baz")]
    assert online_checks.extract_packages("pip install foo >| out bar\n") == [("pypi", "foo"), ("pypi", "bar")]
    assert online_checks.extract_packages("pip install foo 1>&2 bar\n") == [("pypi", "foo"), ("pypi", "bar")]


def test_version_specs_stay_one_requirement_and_do_not_split_the_list():
    assert online_checks.extract_packages("pip install numpy>=1.20 pandas\n") == [
        ("pypi", "numpy"), ("pypi", "pandas")]
    assert online_checks.extract_packages("pip install foo<2 bar\n") == [("pypi", "foo"), ("pypi", "bar")]
    # The quoted requirement is a documented miss; the package after it is still read.
    assert online_checks.extract_packages('pip install "numpy<2" scipy\n') == [("pypi", "scipy")]


def test_then_ends_the_package_list_and_a_following_command_is_not_a_package():
    assert online_checks.extract_packages("Run npm install then npm test\n") == []
    assert online_checks.extract_packages("pip install foo then python app.py\n") == [("pypi", "foo")]


def test_a_word_ending_in_a_colon_ends_the_package_list():
    assert online_checks.extract_packages("pip install foo: it is fast\n") == [("pypi", "foo")]


def test_a_conjunction_ends_one_command_and_the_next_command_is_read():
    body = "Run pip install requests or npm i lodash\n"
    assert online_checks.extract_packages(body) == [("pypi", "requests"), ("npm", "lodash")]


def test_a_stray_backtick_does_not_hide_the_command():
    assert online_checks.extract_packages("Run `pip install requests\n") == [("pypi", "requests")]


def test_a_hash_inside_a_word_is_not_a_comment():
    body = "| C# | npm install foo |\nSee [setup](https://ex.com/docs#setup) then pip install bar\n"
    assert online_checks.extract_packages(body) == [("npm", "foo"), ("pypi", "bar")]


def test_a_conjunction_inside_a_package_list_keeps_the_list():
    assert online_checks.extract_packages("Run pip install numpy and pandas\n") == [
        ("pypi", "numpy"), ("pypi", "pandas")]
    assert online_checks.extract_packages("pip install -U pip and setuptools\n") == [
        ("pypi", "pip"), ("pypi", "setuptools")]
    assert online_checks.extract_packages("npm i lodash or underscore\n") == [
        ("npm", "lodash"), ("npm", "underscore")]


def test_a_hash_after_a_parenthesis_or_in_an_anchor_is_not_a_comment():
    assert online_checks.extract_packages("pip install foo (#12) and npm i bar\n") == [
        ("pypi", "foo"), ("npm", "bar")]
    assert online_checks.extract_packages("See [x](#setup) then pip install bar\n") == [("pypi", "bar")]


def test_list_markers_and_a_command_inside_a_code_span_are_read():
    assert online_checks.extract_packages("- pip install foo\n1. npm i bar\n") == [
        ("pypi", "foo"), ("npm", "bar")]
    assert online_checks.extract_packages("Run pip install `requests`\n") == [("pypi", "requests")]


def test_a_markdown_quote_in_front_of_a_command_is_read():
    assert online_checks.extract_packages("> pip install foo\n") == [("pypi", "foo")]


def test_npx_value_option_is_skipped_before_the_package():
    assert online_checks.extract_packages("npx --registry https://r.example-tool.dev foo\n") == [("npm", "foo")]


def test_npx_prose_words_are_not_packages_and_npx_with_a_package_is():
    body = "npx is great for one-off tools\nUse npx create-react-app my-app\n"
    assert online_checks.extract_packages(body) == [("npm", "create-react-app")]


def test_inline_code_spans_are_commands_of_their_own():
    body = "Install with `pip install requests` and `npm i lodash`.\n"
    assert online_checks.extract_packages(body) == [("pypi", "requests"), ("npm", "lodash")]


def test_markdown_link_text_and_target_are_both_checked():
    assert online_checks.extract_urls("[https://a.com/a](https://a.com/b)\n") == [
        "https://a.com/a", "https://a.com/b"]


def test_npx_run_as_an_instruction_is_checked():
    body = "Run npx create-react-app app\n$ npx cowsay hi\n"
    assert online_checks.extract_packages(body) == [("npm", "create-react-app"), ("npm", "cowsay")]


def test_markdown_link_whose_text_is_the_url_is_one_url():
    assert online_checks.extract_urls("[https://b.com/x](https://b.com/x)\n") == ["https://b.com/x"]


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


def test_no_data_answer_from_the_resolver_is_a_dead_link(tmp_path):
    url = "https://no-data.example-tool.dev/"
    root = _one_skill(tmp_path, f"See {url}\n")
    error = httpx.ConnectError("No address associated with hostname")
    error.__cause__ = socket.gaierror(socket.EAI_NODATA, "No address associated with hostname")
    web = Web({url: error})

    [review] = _reviews(root, web)
    assert _codes(review) == ["link-dead"]


def test_temporary_resolver_failure_is_not_a_dead_link(tmp_path):
    url = "https://real-site.example-tool.dev/"
    root = _one_skill(tmp_path, f"See {url}\n")
    error = httpx.ConnectError("Temporary failure in name resolution")
    error.__cause__ = socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
    web = Web({url: error})

    [review] = _reviews(root, web)
    assert _codes(review) == []


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


def test_online_with_kind_tasks_is_a_usage_error(tmp_path):
    parser = build_parser()
    args = parser.parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "tasks", "--online"])
    with pytest.raises(InputError, match="--online"):
        args.func(args)


def test_online_still_probes_the_good_skills_when_one_skill_fails_to_load(tmp_path, monkeypatch, capsys):
    _skill(tmp_path / "skills", "good", name="good", description="Use for good work.",
           body="pip install nosuch-pkg-xyz\n")
    broken = tmp_path / "skills" / "broken"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("---\nname: [unclosed\n---\nbody\n", encoding="utf-8")
    web = Web({PYPI.format(name="nosuch-pkg-xyz"): 404})
    monkeypatch.setattr(online_checks, "make_client", web.client)

    code, report = _scan_capture(tmp_path, capsys, "--online")
    assert code == 1  # the skill that does not load fails the check, as it does without --online
    good = next(row for row in report["skills"] if row["name"] == "good")
    assert [f["code"] for f in good["findings"]] == ["package-not-found"]


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
