# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``install-unpinned`` reads pnpm, counts each install once and skips the quoted text of ``echo`` and ``printf`` (#946)."""

from __future__ import annotations

from functools import partial

import pytest

from dokimi.external import command_checks
from dokimi.external.skill_markdown import code_spans
from tests.growth_timing import GROWTH_LIMIT, growth
from tests.test_catalog_command_checks import _fenced, _found, _review



def test_pnpm_quoted_dir_with_a_space_does_not_hide_the_install(tmp_path):
    # #946 review: the value of `--dir` may be a quoted path with a space
    [finding] = _found(_review(tmp_path, _fenced('pnpm --dir "my app" add lodash')), "install-unpinned")
    assert "1 install" in finding.message and "pnpm lodash" in finding.message


def test_pnpm_catalog_name_is_not_a_package(tmp_path):
    # #946 review: the value of `--save-catalog-name` names a catalog, not a package
    [finding] = _found(_review(tmp_path, _fenced("pnpm add --save-catalog-name default lodash")), "install-unpinned")
    assert "1 install" in finding.message and "pnpm lodash" in finding.message and "pnpm default" not in finding.message



# ---- #946: a quoted echo is not an install, and the same install shown twice is one ------------

@pytest.mark.parametrize("line", [
    'echo "pnpm add lodash"',
    "echo 'pnpm add lodash'",
    'echo -n "pip install requests"',
    'printf "npm install left-pad\\n"',
    'cd app && echo "npx create-thing"',
])
def test_a_command_in_echoed_text_is_not_an_install(tmp_path, line):
    # #946: the command is printed, not run
    assert _found(_review(tmp_path, _fenced(line)), "install-unpinned") == []


@pytest.mark.parametrize("line", [
    'bash -c "pip install requests"',  # the quoted command runs
    'echo "done" && pip install requests',  # the quote closed before the install
    'echo "a" "b"; pnpm add lodash',
])
def test_a_quoted_command_that_runs_is_still_an_install(tmp_path, line):
    assert "1 install" in _found(_review(tmp_path, _fenced(line)), "install-unpinned")[0].message


def test_the_same_install_shown_twice_counts_once(tmp_path):
    # #946 scenario 2: three mentions of one package are one package
    review = _review(tmp_path, _fenced("pip install anthropic", "pip install anthropic"),
                     {"a.md": _fenced("pip install anthropic")})
    [finding] = _found(review, "install-unpinned")
    assert "1 install name" in finding.message


def test_a_fence_and_a_code_span_of_one_install_count_once(tmp_path):
    body = _fenced("pnpm add lodash") + "The setup command above is `pnpm add lodash`.\n"
    assert "1 install name" in _found(_review(tmp_path, body), "install-unpinned")[0].message


def test_the_same_name_under_two_managers_counts_twice(tmp_path):
    review = _review(tmp_path, _fenced("pip install requests", "npm install requests"))
    assert "2 installs" in _found(review, "install-unpinned")[0].message


def test_a_command_substitution_in_echoed_text_runs(tmp_path):
    # #946 review: `$(...)` inside double quotes is executed
    assert "1 install" in _found(_review(tmp_path, _fenced('echo "$(pip install requests)"')),
                                 "install-unpinned")[0].message


def test_pnpm_registry_before_the_subcommand_does_not_hide_the_install(tmp_path):
    # #946 review: an npm value option is a value option before the subcommand too
    [finding] = _found(_review(tmp_path, _fenced("pnpm --registry https://registry.npmjs.org add lodash")),
                       "install-unpinned")
    assert "pnpm lodash" in finding.message


def test_printf_arguments_after_the_format_are_text(tmp_path):
    # #946: a command in the quoted text of `echo` or `printf` is printed, not run, whichever argument holds it
    assert _found(_review(tmp_path, _fenced("printf '%s\\n' 'pip install requests'")), "install-unpinned") == []


def test_a_multiline_echoed_string_is_text(tmp_path):
    # #946: a quoted string of `echo` may run over several lines; the command in it is still printed text
    assert _found(_review(tmp_path, _fenced('echo "Example:', "pip install requests", '"')), "install-unpinned") == []


def test_a_command_after_a_closed_echo_substitution_runs(tmp_path):
    # #946 review: the `)` ends the echo, so the `bash -c` after it is not echoed text
    assert "1 install" in _found(_review(tmp_path, _fenced('$(echo bash) -c "pip install requests"')),
                                 "install-unpinned")[0].message


def test_an_escaped_substitution_in_echoed_text_is_text(tmp_path):
    # #946 review: `\$(` is a literal, so nothing runs
    assert _found(_review(tmp_path, _fenced('echo "\\$(pip install requests)"')), "install-unpinned") == []


@pytest.mark.parametrize("body", [
    _fenced("echo Don't panic") + _fenced("pip install requests") + _fenced("echo 'ok'"),
    "Run `echo it's` first, then `pip install requests`, then `echo 'ok'`.\n",
])
def test_an_apostrophe_in_one_block_does_not_hide_an_install_in_the_next(tmp_path, body):
    # #946 review of #1046: an echo's quote never closes in a later fenced block or code span
    assert "pip requests" in _found(_review(tmp_path, body), "install-unpinned")[0].message


def test_echo_after_a_shell_keyword_is_echo(tmp_path):
    # #946 review: `then echo "..."` prints text like a bare `echo`
    assert _found(_review(tmp_path, _fenced('if true; then echo "pip install requests"; fi')),
                  "install-unpinned") == []


def test_pnpm_option_with_an_attached_quoted_value_does_not_hide_the_install(tmp_path):
    # #1046 review: `--dir="my app"` holds a space; the install after it is still read
    [finding] = _found(_review(tmp_path, _fenced('pnpm --dir="my app" add lodash')), "install-unpinned")
    assert "pnpm lodash" in finding.message


@pytest.mark.parametrize("line, names", [
    ('echo "$(printf "%s" "done"; pip install requests)"', "pip requests"),
    ('echo $(bash -c "pip install requests")', "pip requests"),
    ('echo "Installing (pip): $(pip install requests)"', "pip requests"),
    ('echo "\\\\$(pip install requests)"', "pip requests"),  # two backslashes: the `$(` is not escaped
    ('printf "%s" "pnpm add lodash $(npm install left-pad)"', "npm left-pad"),
    ('echo "pip install requests $(pip install numpy)"', "pip numpy"),
    ('echo "pip install requests `pip install numpy`"', "pip numpy"),
])
def test_a_substitution_in_echoed_text_runs_whatever_surrounds_it(tmp_path, line, names):
    [finding] = _found(_review(tmp_path, _fenced(line)), "install-unpinned")
    assert "1 install" in finding.message and names in finding.message


@pytest.mark.parametrize("line", [
    'command echo "pnpm add lodash"',
    "LC_ALL=C printf '%s\\n' 'pip install requests'",
    'echo >&2 "pip install requests"',
    'printf >&2 "%s\\n" "pnpm add lodash"',
    'echo "pip install requests" 2>&1',
])
def test_echo_with_a_prefix_or_a_redirection_is_still_text(tmp_path, line):
    assert _found(_review(tmp_path, _fenced(line)), "install-unpinned") == []


@pytest.mark.parametrize("line", [
    'echo "Example: ; echo \'"; pip install requests; echo "\'"',
    'printf "Example: ; printf \'"; pip install requests; printf "\'"',
])
def test_echo_inside_a_quoted_argument_does_not_open_a_command(tmp_path, line):
    assert "pip requests" in _found(_review(tmp_path, _fenced(line)), "install-unpinned")[0].message


@pytest.mark.parametrize("line, name", [
    ('echo "pip install requests" | sh', "pip requests"),
    ("printf '%s\\n' 'pnpm add lodash' | bash", "pnpm lodash"),
    ('echo "npm install left-pad" | sudo bash', "npm left-pad"),
    ('echo "pip install requests" | fish', "pip requests"),
    ("printf '%s\\n' 'pip install requests' | fish", "pip requests"),
    ("echo \"$(echo 'pip install requests')\" | sh", "pip requests"),
    ("echo \"$(printf '%s' 'pip install requests')\" | sh", "pip requests"),
])
def test_echo_piped_to_a_shell_is_run(tmp_path, line, name):
    assert name in _found(_review(tmp_path, _fenced(line)), "install-unpinned")[0].message


@pytest.mark.parametrize("line", [
    'echo "pip install requests" |\nsh',
    'printf "%s\\n" "pip install requests" | \\\n  sh',
    'echo "pip install requests" | sudo -E bash',
    'echo "pip install requests" | /bin/bash',
    'echo "pip install requests" | env bash',
    'echo "pip install requests" |&\nzsh',
])
def test_echo_piped_to_a_shell_after_a_newline_sudo_env_or_path_is_run(tmp_path, line):
    assert "pip requests" in _found(_review(tmp_path, _fenced(line)), "install-unpinned")[0].message


def test_a_deeply_nested_substitution_does_not_raise(tmp_path):
    body = _fenced("echo " + "$(" * 600 + "true" + ")" * 600, "pip install requests")
    assert "pip requests" in _found(_review(tmp_path, body), "install-unpinned")[0].message


def test_an_unclosed_string_with_nested_substitutions_is_scanned_in_linear_time(tmp_path):
    ratio, review = growth(lambda k: partial(_review, tmp_path, _fenced('"$(' * k + "pip install requests")), 10)
    assert ratio < GROWTH_LIMIT
    assert "pip requests" in _found(review, "install-unpinned")[0].message


@pytest.mark.parametrize("command", ["echo", "printf"])
def test_a_quote_in_a_comment_does_not_hide_the_next_install(tmp_path, command):
    body = _fenced(f"{command} ready # don't install anything", "pip install requests", "# end of 'example'")
    assert "pip requests" in _found(_review(tmp_path, body), "install-unpinned")[0].message


def test_a_hash_inside_a_word_is_not_a_comment(tmp_path):
    body = _fenced('echo "a" b#c "pip install requests"')
    assert _found(_review(tmp_path, body), "install-unpinned") == []


@pytest.mark.parametrize("line, names", [
    ("pnpm add --reporter silent react", "pnpm react"),
    ("pnpm install --modules-dir node_modules react", "pnpm react"),
    ("pnpm add --virtual-store-dir vs --lockfile-dir ld --global-dir gd react", "pnpm react"),
])
def test_pnpm_more_value_options_are_not_packages(tmp_path, line, names):
    assert _found(_review(tmp_path, _fenced(line)), "install-unpinned")[0].message.startswith("1 install name no version (" + names)


@pytest.mark.parametrize("line", ["pnpm dlx create-vite", "pnpx create-vite", "pnpm --dir app dlx create-vite"])
def test_pnpm_dlx_and_pnpx_run_a_package_as_npx_does(tmp_path, line):
    assert "pnpm create-vite" in _found(_review(tmp_path, _fenced(line)), "install-unpinned")[0].message


def test_pnpm_dlx_with_a_pinned_package_is_not_flagged(tmp_path):
    assert _found(_review(tmp_path, _fenced("pnpm dlx create-vite@5.0.0")), "install-unpinned") == []


@pytest.mark.parametrize("line", [
    "echo \"$(echo 'pip install requests' >/dev/null)\" | sh",
    "echo \"$(echo 'pip install requests' >&2)\" | sh",
    "echo 'pip install requests' > /dev/null | sh",
    "echo \"$(printf '%s' 'pip install requests' >>log)\" | sh",
])
def test_echo_with_redirected_output_feeds_a_shell_nothing(tmp_path, line):
    assert _found(_review(tmp_path, _fenced(line)), "install-unpinned") == []


@pytest.mark.parametrize("redirect", ["2>/dev/null", "2>>/dev/null", ">&1", "1>&1", "2>&1"])
def test_echo_with_stderr_or_self_redirection_still_feeds_a_shell(tmp_path, redirect):
    found = _found(_review(tmp_path, _fenced(f"echo 'pip install requests' {redirect} | sh")), "install-unpinned")
    assert "pip requests" in found[0].message


@pytest.mark.parametrize("command", ["echo", "printf"])
def test_a_comment_in_a_substitution_piped_to_a_shell_is_not_an_install(tmp_path, command):
    line = f"echo \"$({command} ':' # pip install requests\n)\" | sh"
    assert _found(_review(tmp_path, _fenced(line)), "install-unpinned") == []


@pytest.mark.parametrize("command", ["echo", "printf"])
@pytest.mark.parametrize("redirect", ["2>&1 >&2", "2>&1 1>&2"])
def test_a_stdout_duplicated_onto_stderr_after_stderr_onto_stdout_still_feeds_a_shell(tmp_path, command, redirect):
    found = _found(_review(tmp_path, _fenced(f"{command} 'pip install requests' {redirect} | sh")), "install-unpinned")
    assert "pip requests" in found[0].message


@pytest.mark.parametrize("redirect", [">&2", ">&2 2>&1", "2>&1 >/dev/null"])
def test_a_stdout_sent_away_before_or_without_stderr_joining_it_feeds_a_shell_nothing(tmp_path, redirect):
    line = f"echo 'pip install requests' {redirect} | sh"
    assert _found(_review(tmp_path, _fenced(line)), "install-unpinned") == []


@pytest.mark.parametrize("redirect", ["2>&1 2>/dev/null >&2", "2>&1 2>>log >&2", "12>&1 >&2",
                                      "10>/dev/null 2>&10 >&2", "_2>&1 >&2"])
def test_a_stdout_sent_to_stderr_that_no_longer_joins_the_pipe_feeds_a_shell_nothing(tmp_path, redirect):
    line = f"echo 'pip install requests' {redirect} | sh"
    assert _found(_review(tmp_path, _fenced(line)), "install-unpinned") == []


@pytest.mark.parametrize("command", ["echo", "printf"])
def test_a_stderr_duplicated_onto_itself_keeps_joining_the_pipe(tmp_path, command):
    line = f"{command} 'pip install requests' 2>&1 2>&2 >&2 | sh"
    assert "pip requests" in _found(_review(tmp_path, _fenced(line)), "install-unpinned")[0].message


@pytest.mark.parametrize("command", ["echo", "printf"])
@pytest.mark.parametrize("word", ["example-2", "a.2", "x:2", "'q'2"])
def test_a_2_ending_an_argument_is_not_a_descriptor(tmp_path, command, word):
    line = f"{command} 'pip install requests' {word}>&1 >&2 | sh"
    assert _found(_review(tmp_path, _fenced(line)), "install-unpinned") == []


def test_nested_echo_not_piped_to_a_shell_is_still_printed(tmp_path):
    assert _found(_review(tmp_path, _fenced("echo \"$(echo 'pip install requests')\"")), "install-unpinned") == []


@pytest.mark.parametrize("line, names", [
    ("pnpm dlx --reporter silent create-vite@5.0.0", None),
    ("pnpm dlx --reporter silent create-vite", "pnpm create-vite"),
    ("pnpx --dir app create-vite", "pnpm create-vite"),
    ("pnpm dlx --dir app create-vite@5.0.0", None),
])
def test_pnpm_dlx_value_options_after_dlx_are_pnpms(tmp_path, line, names):
    found = _found(_review(tmp_path, _fenced(line)), "install-unpinned")
    assert (found[0].message.startswith("1 install name no version (" + names + ")") if names else found == [])


@pytest.mark.parametrize("command", [
    "pnpm --package=typescript@5.0.4 dlx tsc --version",
    "pnpm --package typescript@5.0.4 dlx tsc --version",
    "pnpm -p typescript@5.0.4 dlx tsc --version",
])
def test_a_pinned_package_selection_before_pnpm_dlx_is_pinned(tmp_path, command):
    assert _found(_review(tmp_path, _fenced(command)), "install-unpinned") == []


@pytest.mark.parametrize("command", ["pnpm --package=typescript dlx tsc", "pnpm --package typescript dlx tsc"])
def test_an_unpinned_package_selection_before_pnpm_dlx_is_the_package(tmp_path, command):
    message = _found(_review(tmp_path, _fenced(command)), "install-unpinned")[0].message
    assert message.startswith("1 install name no version (pnpm typescript)")


@pytest.mark.parametrize("command", ["echo", "printf"])
def test_a_command_in_a_comment_after_an_echo_is_not_an_install(tmp_path, command):
    assert _found(_review(tmp_path, _fenced(f"{command} ready # 'pip install requests'")), "install-unpinned") == []


def test_a_package_selection_inside_another_options_value_is_not_a_selection(tmp_path):
    body = _fenced("pnpm --dir 'app --package=typescript@5.0.4' dlx create-vite")
    assert _found(_review(tmp_path, body), "install-unpinned")[0].message.startswith(
        "1 install name no version (pnpm create-vite)")


def test_code_spans_of_prose_come_from_the_linear_scanner_and_each_ends_a_block(monkeypatch):
    # #1073 with #946: spans are read by `code_spans`, and a BLOCK_END follows each so a quote does not run on
    seen = []

    def spans(text):
        seen.append(text)
        return code_spans(text)

    monkeypatch.setattr(command_checks, "code_spans", spans)
    code = command_checks._code_text("Run `echo it's` then ``pip install a`b``.\n", True)
    assert seen
    end = command_checks.BLOCK_END
    assert code == f"echo it's\n{end}\npip install a`b\n{end}"
