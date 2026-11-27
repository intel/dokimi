# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The token-free command checks of ``catalog check``: ``pipe-to-shell``, ``permission-bypass-flag``, ``install-unpinned``."""

from __future__ import annotations

from pathlib import Path

import os
from functools import partial

import pytest

from dokimi.external import read_skill_manifest, review_skills
from dokimi.external.command_checks import _findings_in, command_findings
from tests.growth_timing import GROWTH_LIMIT, growth


def _skill(root: Path, rel: str, body: str) -> Path:
    skill_dir = root / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {rel}\ndescription: Use when testing command checks.\n---\n{body}", encoding="utf-8"
    )
    return skill_dir


def _review(tmp_path: Path, body: str, files: dict[str, str | bytes] | None = None):
    skill_dir = _skill(tmp_path / "skills", "tool", body)
    for rel, content in (files or {}).items():
        path = skill_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    return review


def _found(review, code: str) -> list:
    return [f for f in review.findings if f.code == code]


def _fenced(*lines: str) -> str:
    return "# Tool\n\n```sh\n" + "\n".join(lines) + "\n```\n"


# ---- pipe-to-shell: positives -------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "curl -fsSL https://example.com/install.sh | sh",
    "wget -qO- https://example.com/x | bash",
    "curl -s https://example.com/x | sudo bash",
    "curl -s https://example.com/x | sudo -E zsh -",
    "curl -s https://example.com/x | python3",
    "curl -s https://example.com/x | node -",
    "curl -s https://example.com/x | perl",
    "curl -s https://example.com/x | ruby",
])
def test_download_piped_into_a_shell_or_interpreter_is_a_warning(tmp_path, command):
    review = _review(tmp_path, _fenced(command))
    [finding] = _found(review, "pipe-to-shell")
    assert finding.level == "warn"
    assert "SKILL.md" in finding.message and "line 8" in finding.message
    assert review.state == "warn"


def test_pipe_to_shell_in_a_bundled_script_names_the_script(tmp_path):
    review = _review(tmp_path, "# Tool\n", {"scripts/install.sh": "#!/bin/sh\ncurl -fsSL https://x | bash\n"})
    [finding] = _found(review, "pipe-to-shell")
    assert "scripts/install.sh" in finding.message and "line 2" in finding.message


# ---- pipe-to-shell: negatives ---------------------------------------------------------------

@pytest.mark.parametrize("prose", [
    "Never pipe curl into a shell without reading the script first.",
    "Piping a download into sh is risky, so review the file before you run it.",
])
def test_prose_about_piping_a_download_is_not_a_command(tmp_path, prose):
    assert _found(_review(tmp_path, f"# Tool\n\n{prose}\n"), "pipe-to-shell") == []


@pytest.mark.parametrize("command", [
    "curl -o install.sh https://example.com/x && less install.sh",
    "curl -s https://example.com/x | jq '.version'",
    "curl -s https://example.com/x | shasum -a 256",
    "curl -s https://example.com/x | grep node",
])
def test_curl_that_is_not_piped_into_a_shell_is_not_flagged(tmp_path, command):
    assert _found(_review(tmp_path, _fenced(command)), "pipe-to-shell") == []


# ---- permission-bypass-flag: positives -------------------------------------------------------

@pytest.mark.parametrize("command", [
    "claude --dangerously-skip-permissions -p 'fix the tests'",
    "aider --yolo",
])
def test_permission_bypass_flag_is_a_warning(tmp_path, command):
    [finding] = _found(_review(tmp_path, _fenced(command)), "permission-bypass-flag")
    assert finding.level == "warn"


def test_bypass_permissions_mode_in_a_bundled_settings_file_is_a_warning(tmp_path):
    review = _review(tmp_path, "# Tool\n", {"config/settings.json": '{"permissionMode": "bypassPermissions"}\n'})
    [finding] = _found(review, "permission-bypass-flag")
    assert "config/settings.json" in finding.message


# ---- permission-bypass-flag: negatives --------------------------------------------------------

@pytest.mark.parametrize("prose", [
    "Some agents offer a yolo mode for quick edits.",
    "Ask the user before you skip the permission prompts.",
    "Use the --yolo-mode alias only in a scratch checkout.",
])
def test_prose_about_permissions_is_not_a_flag(tmp_path, prose):
    assert _found(_review(tmp_path, f"# Tool\n\n{prose}\n"), "permission-bypass-flag") == []


# ---- install-unpinned: positives --------------------------------------------------------------

def test_unpinned_pip_and_npm_installs_are_counted_in_one_warning(tmp_path):
    review = _review(tmp_path, _fenced("pip install requests numpy", "npm install left-pad"))
    [finding] = _found(review, "install-unpinned")
    assert finding.level == "warn"
    assert "3 install" in finding.message


def test_pnpm_add_counts_each_unpinned_name(tmp_path):
    # issue #946: only pip, npm and npx were read, so `pnpm add -D parcel html-inline` was not counted
    [finding] = _found(_review(tmp_path, _fenced("pnpm add -D parcel html-inline")), "install-unpinned")
    assert "2 install" in finding.message and "pnpm parcel" in finding.message


@pytest.mark.parametrize("line", ["pnpm install lodash", "pnpm i lodash"])
def test_pnpm_install_and_i_count_like_add(tmp_path, line):
    # #946 and docs/external-content.md: a `pnpm` install that names a package with no version is `install-unpinned`
    assert "1 install" in _found(_review(tmp_path, _fenced(line)), "install-unpinned")[0].message


@pytest.mark.parametrize("line", [
    "pnpm install",  # no name: installs from the lockfile
    "pnpm add lodash@4.17.21",
    "pnpm add ./pkg",
    "pnpm add --filter web --dir app lodash@4.17.21",  # the values of --filter and --dir are not packages
    "npm install -g pnpm",  # pnpm is the argument of npm here, not the command
    "pnpm run build",
])
def test_pnpm_pinned_or_nameless_is_not_counted(tmp_path, line):
    # docs/external-content.md: a pinned package, a local path or no package is not counted
    # the one name `npm install -g pnpm` counts is npm's, listed as `npm pnpm`
    assert not any("(pnpm " in f.message for f in _found(_review(tmp_path, _fenced(line)), "install-unpinned"))


def test_pnpm_filter_value_is_not_a_package(tmp_path):
    # #946: the value of `--filter` is a workspace selector, not a package
    [finding] = _found(_review(tmp_path, _fenced("pnpm add --filter web lodash")), "install-unpinned")
    assert "1 install" in finding.message and "pnpm lodash" in finding.message


def test_pnpm_workspace_root_flag_takes_no_value(tmp_path):
    # #946 review: `-w` must not swallow the package that follows
    # `-w` is --workspace-root in pnpm, a flag; in npm it is --workspace and takes a value
    [finding] = _found(_review(tmp_path, _fenced("pnpm add -w lodash")), "install-unpinned")
    assert "pnpm lodash" in finding.message


@pytest.mark.parametrize("line", [
    "pnpm --filter web add lodash",
    "pnpm -F web i lodash",
    "pnpm --dir app --silent install lodash",
    "pnpm -w add lodash",
    "pnpm --filter=web add lodash",
    "pnpm --dir=app --store-dir=s add lodash",
])
def test_pnpm_options_before_the_subcommand_do_not_hide_the_install(tmp_path, line):
    # #946 review: pnpm takes options before its subcommand, in `--opt value` and `--opt=value` form
    [finding] = _found(_review(tmp_path, _fenced(line)), "install-unpinned")
    assert "1 install" in finding.message and "pnpm lodash" in finding.message


def test_pnpm_options_before_a_missing_subcommand_do_not_backtrack(tmp_path):
    def line(k):
        return "pnpm " + "-F -F " * k + "run"

    ratio, review = growth(lambda k: partial(_review, tmp_path, _fenced(line(k))), 6)
    assert ratio < GROWTH_LIMIT
    assert _found(review, "install-unpinned") == []


def test_npx_names_the_first_package_it_runs(tmp_path):
    [finding] = _found(_review(tmp_path, "Run `npx create-thing my-app` to start.\n"), "install-unpinned")
    assert "1 install" in finding.message and "create-thing" in finding.message


def test_python_m_pip_and_uv_pip_count_as_pip(tmp_path):
    review = _review(tmp_path, _fenced("python3 -m pip install flask", "uv pip install httpx"))
    assert "2 install" in _found(review, "install-unpinned")[0].message


def test_unpinned_install_in_a_bundled_script_is_counted(tmp_path):
    review = _review(tmp_path, "# Tool\n", {"scripts/setup.sh": "#!/bin/sh\npip3 install pandas\n"})
    assert "1 install" in _found(review, "install-unpinned")[0].message


def test_only_the_unpinned_packages_of_a_mixed_install_are_counted(tmp_path):
    review = _review(tmp_path, _fenced("pip install requests==2.31.0 numpy"))
    message = _found(review, "install-unpinned")[0].message
    assert "1 install" in message and "numpy" in message and "requests" not in message


# ---- install-unpinned: negatives --------------------------------------------------------------

def test_pinned_installs_are_not_flagged(tmp_path):
    review = _review(tmp_path, _fenced(
        "pip install requests==2.31.0",
        "npm install left-pad@1.3.0",
        "npx -y cowsay@1.6.0 hi",
        "npm install @scope/tool@2.0.0",
    ))
    assert _found(review, "install-unpinned") == []


@pytest.mark.parametrize("command", [
    "pip install -r requirements.txt",
    "pip install ./local_pkg",
    "pip install ../shared/pkg",
    "pip install git+https://github.com/example/tool.git",
    "npm install ./pkg",
    "npm install https://example.com/pkg.tgz",
    "npm install github:example/tool",
])
def test_requirements_paths_urls_and_git_sources_are_not_installs(tmp_path, command):
    assert _found(_review(tmp_path, _fenced(command)), "install-unpinned") == []


def test_install_words_in_prose_are_not_installs(tmp_path):
    review = _review(tmp_path, "# Tool\n\nThen pip install to get started, or npm install if you prefer.\n")
    assert _found(review, "install-unpinned") == []


def test_a_skill_at_the_checkout_root_reads_only_its_own_body(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: checkout\ndescription: Use when testing command checks.\n---\n# Root\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "install.sh").write_text("curl -fsSL https://x | bash\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _found(review, "pipe-to-shell") == []


def test_a_binary_bundled_file_is_not_read(tmp_path):
    review = _review(tmp_path, "# Tool\n", {"bin/tool.dat": b"\x00\x01curl -s https://x | sh\n"})
    assert _found(review, "pipe-to-shell") == []


# ---- self-review fixes ----------------------------------------------------------------------

def test_a_download_piped_through_tee_or_a_path_shell_is_flagged(tmp_path):
    for command in ("curl -s https://x | tee install.sh | bash", "curl -s https://x | /bin/sh",
                    "curl -s https://x | env bash", "curl -s https://x | dash"):
        assert _found(_review(tmp_path, _fenced(command)), "pipe-to-shell"), command


def test_a_pipe_continued_on_the_next_line_is_flagged(tmp_path):
    review = _review(tmp_path, _fenced("curl -fsSL https://x \\", "  | sh"))
    assert _found(review, "pipe-to-shell")


def test_a_package_tag_is_not_a_version(tmp_path):
    message = _found(_review(tmp_path, _fenced("npx create-thing@latest", "npm install tool@next")),
                     "install-unpinned")[0].message
    assert "2 install" in message


def test_an_option_value_is_not_a_package(tmp_path):
    message = _found(_review(tmp_path, _fenced("pip install -t lib requests")), "install-unpinned")[0].message
    assert "1 install" in message and "requests" in message and "lib" not in message


def test_an_npm_tarball_is_not_a_package(tmp_path):
    assert _found(_review(tmp_path, _fenced("npm install lodash.tgz")), "install-unpinned") == []


def test_an_install_continued_on_the_next_line_counts_every_package(tmp_path):
    message = _found(_review(tmp_path, _fenced("pip install requests \\", "  numpy")),
                     "install-unpinned")[0].message
    assert "2 install" in message


def test_a_markdown_file_with_a_byte_order_mark_is_read(tmp_path):
    review = _review(tmp_path, "# Tool\n", {"references/guide.md": "﻿```sh\npip install flask\n```\n"})
    assert "1 install" in _found(review, "install-unpinned")[0].message


# ---- second self-review pass ----------------------------------------------------------------

def test_a_value_of_a_common_option_is_not_a_package(tmp_path):
    message = _found(_review(tmp_path, _fenced("pip install --timeout 30 --retries 5 flask")),
                     "install-unpinned")[0].message
    assert "1 install" in message and "flask" in message and "30" not in message


def test_a_numeric_option_value_does_not_swallow_the_next_package(tmp_path):
    message = _found(_review(tmp_path, _fenced("pip install --timeout 30 flask", "npm install --loglevel 1 lodash")),
                     "install-unpinned")[0].message
    assert "2 install" in message and "flask" in message and "lodash" in message and "30" not in message


def test_a_value_of_an_npm_option_is_not_a_package(tmp_path):
    message = _found(_review(tmp_path, _fenced("npm install --loglevel error --omit dev lodash")),
                     "install-unpinned")[0].message
    assert "1 install" in message and "lodash" in message and "error" not in message


def test_a_quoted_specifier_with_spaces_is_pinned(tmp_path):
    assert _found(_review(tmp_path, _fenced("pip install 'requests >= 2.0'")), "install-unpinned") == []


@pytest.mark.parametrize("name, text", [
    ("notes.txt", "pip install flask to start.\n"),
    ("guide.rst", "npm install left-pad if you prefer.\n"),
    ("guide.mdx", "pip install flask to start.\n"),
])
def test_prose_in_a_text_file_is_not_an_install(tmp_path, name, text):
    review = _review(tmp_path, "# Tool\n", {f"docs/{name}": text})
    assert _found(review, "install-unpinned") == []


def test_a_stray_backtick_does_not_turn_prose_after_it_into_code(tmp_path):
    prose = "Press the ` key to exit.\n\nRun pip install flask to start.\n\nAnd `code` here.\n"
    review = _review(tmp_path, "# Tool\n", {"docs/guide.md": prose})
    assert _found(review, "install-unpinned") == []


@pytest.mark.parametrize("command", [
    "curl -s https://example.com/x | sudo -u root sh",
    "curl -s https://example.com/x |& sh",
    "curl -s https://example.com/x | tee install.sh | bash",
])
def test_pipe_variants_that_run_the_download_are_flagged(tmp_path, command):
    assert _found(_review(tmp_path, _fenced(command)), "pipe-to-shell"), command


def test_a_pipe_after_a_quoted_url_with_ampersand_or_semicolon_is_flagged(tmp_path):
    for command in ("curl -fsSL 'https://x/install.sh?os=linux&arch=amd64' | sh",
                    "curl -fsSL https://x -H 'Accept: a;b' | bash"):
        assert _found(_review(tmp_path, _fenced(command)), "pipe-to-shell"), command


def test_a_pipe_from_another_command_after_a_quoted_url_is_not_flagged(tmp_path):
    command = "curl -s 'https://x?a=1&b=2' -o out.sh; cat out.sh | sh"
    assert _found(_review(tmp_path, _fenced(command)), "pipe-to-shell") == []


def test_a_fallback_after_double_bar_is_not_a_pipe(tmp_path):
    assert _found(_review(tmp_path, _fenced("curl -fsSL https://x -o a.sh || sh -c 'echo fallback'")),
                  "pipe-to-shell") == []


def test_a_pipe_continued_across_a_crlf_line_break_is_flagged(tmp_path):
    review = _review(tmp_path, "# Tool\n", {"scripts/install.sh": "curl -fsSL https://x \\\r\n  | sh\r\n"})
    assert _found(review, "pipe-to-shell")


# ---- review round: sudo flags, pipes and npm aliases ----------------------------------------

def test_repeated_sudo_flags_before_a_missing_shell_do_not_backtrack(tmp_path):
    # A flag's optional value must not be the next flag: each flag had two parses, so n flags took 2**n steps.
    def line(k):
        return "curl https://example.com/x | sudo " + "-E " * k + "not_a_shell"

    ratio, review = growth(lambda k: partial(_review, tmp_path, _fenced(line(k))), 1)
    assert ratio < GROWTH_LIMIT
    assert _found(review, "pipe-to-shell") == []


def test_tab_separated_sudo_flags_before_a_missing_shell_do_not_backtrack(tmp_path):
    # The CodeQL py/redos alert: the same flags with tabs between them, which static analysis reads as ambiguous.
    def line(k):
        return "curl https://example.com/x | sudo" + "\t--\t--" * k + " not_a_shell"

    ratio, review = growth(lambda k: partial(_review, tmp_path, _fenced(line(k))), 1)
    assert ratio < GROWTH_LIMIT
    assert _found(review, "pipe-to-shell") == []


def test_a_long_run_of_tab_dash_tokens_does_not_backtrack(tmp_path):
    # CodeQL py/redos on the sudo flags: 5000 repetitions of "\t--" in a sudo pipe, a pip, an npm and an npx line.
    def lines(k):
        return [
            "curl https://example.com/x | sudo " + "\t--" * k + " not_a_shell",
            "pip install " + "\t--" * k + " requests==2.31.0",
            "npm install " + "\t--" * k + " left-pad@1.3.0",
            "npx " + "\t--" * k + " tsc",
        ]

    ratio, review = growth(lambda k: partial(_review, tmp_path, _fenced(*lines(k))), 312)
    assert ratio < GROWTH_LIMIT
    assert _found(review, "pipe-to-shell") == []


def test_a_pipe_at_the_end_of_a_line_continues_on_the_next_line(tmp_path):
    review = _review(tmp_path, _fenced("curl https://example.com/install.sh |", "sh"))
    assert _found(review, "pipe-to-shell")
    crlf = _review(tmp_path / "crlf", "# Tool\n", {"scripts/i.sh": "curl https://x |\r\nsh\r\n"})
    assert _found(crlf, "pipe-to-shell")


def test_a_pinned_npm_alias_is_not_unpinned(tmp_path):
    assert _found(_review(tmp_path, _fenced("npm install compat@npm:lodash@4.17.21")), "install-unpinned") == []
    assert _found(_review(tmp_path / "scoped", _fenced("npm install a@npm:@scope/pkg@2.0.0")),
                  "install-unpinned") == []
    assert _found(_review(tmp_path / "npx", _fenced("npx compat@npm:lodash@4.17.21")), "install-unpinned") == []


def test_an_npm_alias_without_a_pinned_version_is_unpinned(tmp_path):
    for command, name in (("npm install compat@npm:lodash", "compat"),
                          ("npm install compat@npm:lodash@latest", "compat"),
                          ("npx compat@npm:lodash", "compat")):
        message = _found(_review(tmp_path / name, _fenced(command)), "install-unpinned")[0].message
        assert name in message, command


def test_a_redirect_target_is_not_a_package(tmp_path):
    assert _found(_review(tmp_path, _fenced("pip install requests==2.31.0 > install.log")), "install-unpinned") == []
    assert _found(_review(tmp_path / "npm", _fenced("npm install left-pad@1.3.0 > install.log")),
                  "install-unpinned") == []
    assert _found(_review(tmp_path / "fd", _fenced("pip install requests==2.31.0 2>&1 >>log.txt")),
                  "install-unpinned") == []


def test_a_redirect_does_not_hide_the_package_it_follows(tmp_path):
    message = _found(_review(tmp_path, _fenced("pip install numpy > install.log")), "install-unpinned")[0].message
    assert "1 install" in message and "numpy" in message and "install.log" not in message


def test_a_quoted_comparison_specifier_is_still_a_pin(tmp_path):
    assert _found(_review(tmp_path, _fenced("pip install 'requests>=2' > install.log")), "install-unpinned") == []


@pytest.mark.parametrize("command", [
    "npm install tool@https://example.com/download",
    "npm install tool@git+https://example.com/repo.git",
    "npm install tool@github:user/repo",
    "npm install tool@file:../x",
    "npx tool@https://example.com/download",
    "npx tool.tgz",
])
def test_a_non_registry_source_or_archive_is_not_counted(tmp_path, command):
    assert _found(_review(tmp_path, _fenced(command)), "install-unpinned") == []


def test_a_quoted_requirement_marker_does_not_hide_the_package(tmp_path):
    message = _found(_review(tmp_path, _fenced("pip install 'flask; python_version >= \"3.10\"'")),
                     "install-unpinned")[0].message
    assert "1 install" in message and "flask" in message


@pytest.mark.parametrize("command, name", [
    ("pip install numpy>install.log", "numpy"),
    ("npm install lodash>install.log", "lodash"),
    ("npx cowsay>install.log", "cowsay"),
])
def test_an_attached_redirect_does_not_hide_the_package(tmp_path, command, name):
    message = _found(_review(tmp_path, _fenced(command)), "install-unpinned")[0].message
    assert name in message and "install.log" not in message


def test_every_npx_package_selection_is_judged(tmp_path):
    assert _found(_review(tmp_path, _fenced("npx --package=typescript@5.0.0 tsc")), "install-unpinned") == []
    assert _found(_review(tmp_path / "sep", _fenced("npx --package typescript@5.0.0 tsc")), "install-unpinned") == []
    message = _found(_review(tmp_path / "multi", _fenced("npx -p typescript@5.0.0 -p ts-node ts-node")),
                     "install-unpinned")[0].message
    assert "npx ts-node" in message and "typescript" not in message
    attached = _found(_review(tmp_path / "attached", _fenced("npx -pts-node ts-node")), "install-unpinned")
    assert "npx ts-node" in attached[0].message


def test_npx_flags_after_the_program_are_not_packages(tmp_path):
    # a flag of the program run is its own; an npx option that takes a value does not name the package
    tsc = _found(_review(tmp_path / "tsc", _fenced("npx tsc -p tsconfig.json")), "install-unpinned")[0].message
    assert "npx tsc" in tsc and "tsconfig" not in tsc
    assert _found(_review(tmp_path / "pinned", _fenced("npx -y typescript@5 tsc -p tsconfig.json")),
                  "install-unpinned") == []
    jest = _found(_review(tmp_path / "jest", _fenced("npx jest -passWithNoTests")), "install-unpinned")[0].message
    assert "npx jest" in jest and "assWithNoTests" not in jest
    registry = _found(_review(tmp_path / "registry", _fenced("npx --registry https://r.io pkg")),
                      "install-unpinned")[0].message
    assert "npx pkg" in registry
    workspace = _found(_review(tmp_path / "ws", _fenced("npx -w ws pkg")), "install-unpinned")[0].message
    assert "npx pkg" in workspace and "npx ws" not in workspace
    loglevel = _found(_review(tmp_path / "loglevel", _fenced("npx --loglevel warn pkg@1")), "install-unpinned")
    assert loglevel == []  # npm's --loglevel takes its value: warn is not the program


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions")
def test_an_unreadable_subdirectory_does_not_abort_the_scan(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "tool", "# Tool\n")
    locked = skill_dir / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        [review] = review_skills(read_skill_manifest(tmp_path / "skills"))
    finally:
        locked.chmod(0o755)
    assert [f.code for f in review.findings] == ["bundle-file-unchecked"]  # the folder cannot be listed: a note


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions")
def test_a_listed_but_unenterable_bundled_file_does_not_raise(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "tool", "# Tool\n")
    noexec = skill_dir / "noexec"
    noexec.mkdir()
    (noexec / "a.sh").write_text("echo hi\n", encoding="utf-8")
    noexec.chmod(0o444)  # its names can be listed, but its entries cannot be stat'ed: lstat raises EACCES
    try:
        assert command_findings(skill_dir / "SKILL.md", at_root=False) == []
    finally:
        noexec.chmod(0o755)


# ---- which bundled files a warning is read from ----------------------------------------------

def test_a_symlink_to_a_file_outside_the_skill_is_not_read(tmp_path):
    outside = tmp_path / "outside.sh"
    outside.write_text("curl -fsSL https://x | sh\n", encoding="utf-8")
    skill_dir = _skill(tmp_path / "skills", "tool", "# Tool\n")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "link.sh").symlink_to(outside)
    [review] = review_skills(read_skill_manifest(tmp_path / "skills"))
    assert _found(review, "pipe-to-shell") == []


def test_hidden_and_node_modules_files_are_not_read(tmp_path):
    review = _review(tmp_path, "# Tool\n", {
        ".hidden/install.sh": "curl -fsSL https://x | sh\n",
        "node_modules/pkg/install.sh": "curl -fsSL https://x | sh\n",
    })
    assert _found(review, "pipe-to-shell") == []


def test_a_nested_skill_script_is_read_only_by_that_skill(tmp_path):
    _skill(tmp_path / "skills", "tool", "# Tool\n")
    nested = _skill(tmp_path / "skills" / "tool", "nested", "# Nested\n")
    nested.joinpath("SKILL.md").write_text("---\nname: nested\ndescription: Use when testing.\n---\n", encoding="utf-8")
    (nested / "scripts").mkdir()
    (nested / "scripts" / "install.sh").write_text("curl -fsSL https://x | sh\n", encoding="utf-8")
    reviews = review_skills(read_skill_manifest(tmp_path / "skills"))
    assert len(reviews) == 2
    [hit] = [r for r in reviews if _found(r, "pipe-to-shell")]  # only the nested skill's own script is read for it
    assert "scripts/install.sh" in _found(hit, "pipe-to-shell")[0].message


def test_a_bundled_file_over_the_size_limit_is_not_read(tmp_path):
    big = "# pad\n" + "x" * (256 * 1024)
    review = _review(tmp_path, "# Tool\n", {"scripts/big.sh": big + "\ncurl -fsSL https://x | sh\n"})
    assert _found(review, "pipe-to-shell") == []


def test_only_the_first_bundled_files_up_to_the_count_limit_are_read(tmp_path):
    files: dict[str, str | bytes] = {f"scripts/f{i:03d}.txt": "# note\n" for i in range(200)}
    files["scripts/zz-last.sh"] = "curl -fsSL https://x | sh\n"  # the 201st file in sorted order
    assert _found(_review(tmp_path, "# Tool\n", files), "pipe-to-shell") == []


def test_unpinned_installs_are_counted_across_files(tmp_path):
    review = _review(tmp_path, "# Tool\n", {"scripts/a.sh": "pip install flask\n", "scripts/b.sh": "npm i left-pad\n"})
    message = _found(review, "install-unpinned")[0].message
    assert "2 installs" in message


# ---- review of PR #456 (round 2): code spans, non-registry sources, the read budget ---------

def test_a_code_span_closing_a_line_is_not_a_fence_opener(tmp_path):
    # ```pip install flask``` is one code span: the install is pip's, and the prose after it is not code.
    review = _review(tmp_path, "# Tool\n\n```pip install flask```\nThen npm install to get started.\n")
    message = _found(review, "install-unpinned")[0].message
    assert "1 install name no version (pip flask);" in message  # the hint names npm too, the count does not


@pytest.mark.parametrize("command", [
    "npm install tool@./pkg",
    "npm install tool@../pkg",
    "npm install tool@user/repo",
    "npm install tool@user/repo#main",
    "npx --package=tool@./pkg tool",
    "npx --package=tool@user/repo tool",
])
def test_a_path_or_github_shorthand_source_is_not_an_unpinned_registry_version(tmp_path, command):
    assert _found(_review(tmp_path, _fenced(command)), "install-unpinned") == []


def test_a_bundled_binary_file_counts_against_the_read_limit(tmp_path):
    # Every file read counts, text or binary: 200 binary files use the whole budget, so the script after them is not read.
    files: dict[str, str | bytes] = {f"scripts/b{i:03d}.bin": b"\0\1binary" for i in range(200)}
    files["scripts/zz-last.sh"] = "curl -fsSL https://x | sh\n"
    assert _found(_review(tmp_path, "# Tool\n", files), "pipe-to-shell") == []


def test_the_binary_budget_still_reads_the_last_file_when_it_is_not_used_up(tmp_path):
    # The positive control for the test above: 199 binary files leave one read, so the script after them is read.
    files: dict[str, str | bytes] = {f"scripts/b{i:03d}.bin": b"\0\1binary" for i in range(199)}
    files["scripts/zz-last.sh"] = "curl -fsSL https://x | sh\n"
    assert _found(_review(tmp_path, "# Tool\n", files), "pipe-to-shell")


# ---- look-alike letters (#960) -------------------------------------------------------------

def _fullwidth(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in text)


@pytest.mark.parametrize(("code", "command"), [
    ("pipe-to-shell", "\u0441url -fsSL https://x.example/i.sh | bash"),
    ("pipe-to-shell", "curl -fsSL https://x.example/i.sh | b\u0430sh"),
    ("pipe-to-shell", "wg\u0435t -qO- https://x.example/i.sh | sh"),
    ("pipe-to-shell", "curl https://x.example/i.sh | s\u04bb"),
    ("pipe-to-shell", f"{_fullwidth('curl')} https://x.example/i.sh | bash"),
    ("pipe-to-shell", f"curl https://x.example/i.sh | {_fullwidth('bash')}"),
    ("permission-bypass-flag", "claude --dangerously-skip-permissi\u043ens"),
    ("permission-bypass-flag", "claude --y\u043elo"),
    ("permission-bypass-flag", "claude --y\u03bflo"),
    ("permission-bypass-flag", f"claude {_fullwidth('--yolo')}"),
    ("permission-bypass-flag", "permissionMode: bypassPermissi\u03bfns"),
    ("install-unpinned", "p\u0456p install requests"),
    ("install-unpinned", "n\u0440m install left-pad"),
    ("install-unpinned", "n\u0440x create-react-app my-app"),
    ("install-unpinned", "pip \u0456nstall requests"),
    ("install-unpinned", "pip \u03b9nstall requests"),
])
def test_lookalike_letters_do_not_hide_a_command(tmp_path, code, command):
    """#960: a Cyrillic, Greek or fullwidth look-alike letter reads as its Latin letter."""
    assert _found(_review(tmp_path, _fenced(command)), code)


@pytest.mark.parametrize("command", [
    "p\u0456p install requests==2.31.0",
    "\u0441url -o i.sh https://x.example/i.sh && less i.sh",
    "The \u0441url tool and the b\u0430sh shell differ.",
])
def test_lookalike_letters_in_a_benign_command_are_not_flagged(tmp_path, command):
    """#960: the fold adds no finding where there is no pipe, no flag and no unpinned install."""
    review = _review(tmp_path, _fenced(command))
    assert not [f for f in review.findings if f.code in {"pipe-to-shell", "permission-bypass-flag", "install-unpinned"}]


def test_lookalike_pipe_to_shell_names_the_true_line(tmp_path):
    """#960: the fold keeps the length, so the line of a finding is the line in the file."""
    body = "# Tool\n\nIntro.\n\n```sh\n\u0441url https://x.example/i.sh | bash\n```\n"
    [finding] = _found(_review(tmp_path, body), "pipe-to-shell")
    assert "(line 10)" in finding.message


def test_pipe_to_shell_is_kept_when_folding_would_end_the_match(tmp_path):
    """#960: a fullwidth ``;`` folds to ``;`` and ends the command, but the raw text is a pipe to a shell."""
    body = _fenced("curl https://x.example/；install.sh | bash")
    assert _found(_review(tmp_path, body), "pipe-to-shell")


def test_lookalike_letter_and_fullwidth_semicolon_still_pipe_to_shell(tmp_path):
    """#960: the letters fold but the fullwidth ``;`` does not end the command; only that reading reaches the pipe."""
    body = _fenced("сurl https://x.example/；install.sh | bash")
    assert _found(_review(tmp_path, body), "pipe-to-shell")


def test_fullwidth_command_with_a_fullwidth_semicolon_is_still_a_pipe_to_shell(tmp_path):
    """#960: the fullwidth letters fold to a command while the fullwidth ``;`` in the URL stays a URL character."""
    body = _fenced(f"{_fullwidth('curl')} https://example.com/；install.sh | bash")
    assert _found(_review(tmp_path, body), "pipe-to-shell")


def test_unpinned_install_before_a_fullwidth_semicolon_is_still_counted(tmp_path):
    """#960: a fullwidth ``;`` in an index URL is not a separator, so ``requests`` is still the unpinned package."""
    body = _fenced("pip install --index-url https://example.com/；simple requests")
    [finding] = _found(_review(tmp_path, body), "install-unpinned")
    assert "1 install " in finding.message


def test_lookalike_install_is_counted_once_across_readings(tmp_path):
    """#960: the readings that both find an install do not count it twice."""
    [finding] = _found(_review(tmp_path, _fenced("pіp install requests")), "install-unpinned")
    assert "1 install " in finding.message


def test_distinct_installs_found_in_different_readings_are_both_counted():
    """#960: the first install is only in the raw reading, the second only in the folded one."""
    from dokimi.external.command_checks import _unpinned_in_readings

    text = "pip install --index-url https://example.com/；simple requests\n" + _fullwidth("pip") + "　install requests\n"
    assert len(_unpinned_in_readings(text, False)) == 2


@pytest.mark.parametrize("command, count", [
    ("pip install r\u0435quests", 1),
    (_fullwidth("uv") + " pip install requests", 1),
    (_fullwidth("pip") + "\u3000install --index-url https://example.com/；simple requests", 1),
])
def test_one_install_is_one_package_across_readings(tmp_path, command, count):
    """#960: the readings split ``r\u0435quests`` as ``r`` and ``requests``, and a folded prefix moves the match start."""
    [finding] = _found(_review(tmp_path, _fenced(command)), "install-unpinned")
    assert f"{count} install " in finding.message


@pytest.mark.parametrize("command, packages", [
    ("pip install requests\uff1bpip install flask", ["pip requests", "pip flask"]),
    ("pip install foo \uff5c grep x", ["pip foo"]),
])
def test_fullwidth_separator_ends_a_command_in_every_reading(command, packages):
    """#960: the raw reading reads what follows a fullwidth separator as arguments; the folded one ends the command."""
    from dokimi.external.command_checks import _unpinned_in_readings

    assert _unpinned_in_readings(command, False) == packages


def test_a_fullwidth_semicolon_inside_an_index_url_does_not_drop_the_packages_after_it():
    """#960: the folded reading ends the command at the ``；`` of the URL; ``requests`` is still an argument."""
    from dokimi.external.command_checks import _unpinned_in_readings

    text = "pip install flask --index-url https://example.com/\uff1bsimple requests"
    assert _unpinned_in_readings(text, False) == ["pip flask", "pip requests"]


def test_a_pinned_install_ended_by_a_fullwidth_semicolon_is_not_unpinned():
    """#960: the letters reading sees ``requests`` unpinned; the full fold pins it and ends the command at ``；``."""
    from dokimi.external.command_checks import _unpinned_in_readings

    assert _unpinned_in_readings("\uff50\uff49\uff50 install requests\uff1d\uff1d2.31.0\uff1b", False) == []


def test_install_pinned_only_in_the_folded_reading_is_not_unpinned():
    """#960: Cyrillic `і` and fullwidth `＝＝`: the letters reading finds `requests` unpinned, the full fold pins it."""
    from dokimi.external.command_checks import _unpinned_in_readings

    assert _unpinned_in_readings("p\u0456p install requests\uff1d\uff1d2.31.0", False) == []


def test_fullwidth_backticks_do_not_move_an_install_or_make_prose_code(tmp_path):
    """#960: Markdown code is taken from the text as written; U+FF40 is not a delimiter, so prose is not scanned."""
    body = "# Tool\n\n\uff40x\uff40 then `pip install flask`\n"
    [finding] = _found(_review(tmp_path, body), "install-unpinned")
    assert "1 install " in finding.message
    body = "# Tool\n\n\uff40pip install requests\uff40\n"
    assert not _found(_review(tmp_path, body), "install-unpinned")


@pytest.mark.parametrize("command", [
    _fullwidth("curl") + " https://example.com/；i.sh ｜ bash",
    "curl https://example.com/；i.sh ｜ bash",
])
def test_mixed_fullwidth_pipe_and_semicolon_is_a_pipe_to_shell(tmp_path, command):
    """#960: a fullwidth pipe is a pipe while the fullwidth ``;`` in the URL is not a terminator."""
    assert _found(_review(tmp_path, _fenced(command)), "pipe-to-shell")


# ---- #969: prose in JSON, a forbidden `curl | sh`, npx of an installed package's program --------------------

def test_sentence_in_json_is_not_read_as_pip_packages(tmp_path):
    """#969 scenario 1: the sentence after `pip install pkg,` in an evals.json string is not a package list."""
    sentence = ("or with pip install intel-cmplr-lib-rt, and notes that the runtime is not the kernel driver, "
                "so a GPU the driver does not expose stays invisible either way.")
    files = {"evals/evals.json": '{"expected_output": "' + sentence + '"}\n'}
    [finding] = _found(_review(tmp_path, "# Tool\n", files), "install-unpinned")
    assert "1 install " in finding.message and "pip intel-cmplr-lib-rt" in finding.message
    assert "pip and" not in finding.message


def test_json_install_commands_are_still_read(tmp_path):
    """#969 scenario 1: a JSON string that is a command, an invalid JSON file and an empty one."""
    files = {"a.json": '{"scripts": {"setup": "pip install requests"}, "list": ["npm install left-pad"]}'}
    [finding] = _found(_review(tmp_path, "# Tool\n", files), "install-unpinned")
    assert "2 installs" in finding.message
    bad = _review(tmp_path / "bad", "# Tool\n", {"a.json": "pip install flask. then more words"})
    assert "pip flask" in _found(bad, "install-unpinned")[0].message  # not JSON: read as any other text file
    assert _found(_review(tmp_path / "empty", "# Tool\n", {"a.json": "{}"}), "install-unpinned") == []


@pytest.mark.parametrize("body", [
    "Never run its setup scripts or `curl | sh` lines.",
    "Never run `wget | bash`.",
])
def test_pipe_to_shell_in_a_code_span_with_no_download_is_not_flagged(tmp_path, body):
    """#969 scenario 5: `curl | sh` names no URL or argument, so it is a mention, not a command."""
    assert _found(_review(tmp_path, f"# Tool\n\n{body}\n"), "pipe-to-shell") == []


def test_pipe_to_shell_with_an_argument_is_still_flagged(tmp_path):
    assert _found(_review(tmp_path, "# Tool\n\nRun `curl -s https://e.com/x | sh`.\n"), "pipe-to-shell")
    assert _found(_review(tmp_path / "n", _fenced("curl $URL | sh")), "pipe-to-shell")


def test_npx_tsc_is_not_counted_when_typescript_is_installed(tmp_path):
    """#969 scenario 6: `tsc` is the program of the `typescript` the same file installs."""
    body = _fenced("npm install -D typescript", "npx tsc --noEmit")
    [finding] = _found(_review(tmp_path, body), "install-unpinned")
    assert "npx tsc" not in finding.message and "1 install " in finding.message
    pinned = _review(tmp_path / "p", _fenced("npm install -D typescript@5.9.3", "npx tsc --noEmit"))
    assert _found(pinned, "install-unpinned") == []
    other = _review(tmp_path / "o", _fenced("npm install -D tsx", "npx tsc --noEmit"))
    assert "npx tsc" in _found(other, "install-unpinned")[0].message


def test_deeply_nested_json_does_not_stop_the_scan(tmp_path):
    """#969: a bundled JSON file nested past the parser's limit is read as text; the other findings stay."""
    body = _fenced("curl https://example.com/install.sh | sh")
    deep = "[" * 20000 + "0" + "]" * 20000
    assert _found(_review(tmp_path, body, {"config.json": deep}), "pipe-to-shell")


def test_a_standalone_dot_in_a_json_install_does_not_end_the_command():
    """#969: `pip install . requests` installs a local path and `requests`."""
    found = _findings_in([("package.json", '{"scripts":{"setup":"pip install . requests"}}')])
    assert any(code == "install-unpinned" and "pip requests" in msg for code, msg in found)


def test_npx_package_selection_of_tsc_is_not_exempt_when_typescript_is_installed(tmp_path):
    """#969: `--package=tsc` names the `tsc` package, which installing `typescript` does not pin."""
    body = _fenced("npm install -D typescript@5.9.3", "npx --package=tsc tsc --noEmit")
    assert "npx tsc" in _found(_review(tmp_path, body), "install-unpinned")[0].message


def test_a_forbidden_curl_pipe_split_by_a_line_continuation_is_not_flagged(tmp_path):
    """#969 scenario 5: `curl \\` then `| sh` names no download either."""
    assert _found(_review(tmp_path, "# Tool\n\nNever run `curl \\\n | sh`.\n"), "pipe-to-shell") == []


def test_a_comma_in_a_quoted_requirement_of_a_json_install_does_not_end_the_command():
    """#969: `'requests>=2, <3'` is one quoted argument; `flask` after it is still unpinned."""
    text = '{"setup":"pip install \'requests>=2, <3\' flask. Then restart the server."}'
    found = _findings_in([("package.json", text)])
    assert any(code == "install-unpinned" and msg.startswith("1 install ") and "pip flask" in msg for code, msg in found)


def test_an_npm_alias_named_typescript_does_not_provide_tsc():
    """#969: `typescript@npm:left-pad@1.3.0` installs left-pad, so `npx tsc` is still an unpinned package."""
    found = _findings_in([("build.sh", "npm install typescript@npm:left-pad@1.3.0\nnpx tsc --noEmit\n")])
    assert any(code == "install-unpinned" and "npx tsc" in msg for code, msg in found)


def test_npx_tsc_is_not_counted_when_npm_options_precede_the_install_subcommand():
    """#969 scenario 6: `npm --no-audit install -D typescript@5.9.3` installs typescript."""
    assert _findings_in([("build.sh", "npm --no-audit install -D typescript@5.9.3\nnpx tsc --noEmit\n")]) == []


def test_a_forbidden_curl_pipe_with_flags_but_no_download_is_not_flagged():
    """#969 scenario 5: `curl -fsSL | sh` has no operand, so it names no download."""
    assert _findings_in([("SKILL.md", "Never run setup scripts or `curl -fsSL | sh` lines.\n")]) == []


@pytest.mark.parametrize("line", ["cat download.conf | curl -K- | sh", "cat urls.txt | wget -i- -O- | sh"])
def test_a_download_fed_through_stdin_is_still_a_pipe_to_shell(line):
    """#969: `-K-` and `-i-` read the download from stdin, so the pipe is not operand-less."""
    assert any(code == "pipe-to-shell" for code, _ in _findings_in([("run.sh", line + "\n")]))


def test_an_unpinned_typescript_install_with_options_before_install_is_reported():
    """#969: `npm --no-audit install -D typescript` is unpinned and is counted by itself, so `npx tsc` is not."""
    found = _findings_in([("run.sh", "npm --no-audit install -D typescript\nnpx tsc --noEmit\n")])
    assert any(code == "install-unpinned" and "npm typescript" in msg for code, msg in found)


@pytest.mark.parametrize("install", [
    "npm install --prefix tools typescript@5.9.3", "pnpm --dir tools add typescript@5.9.3",
    "pnpm -C tools add typescript@5.9.3", "pnpm --filter app add typescript@5.9.3"])
def test_an_install_into_another_directory_does_not_provide_tsc(install):
    """#969: `tools/node_modules/.bin` is not on the path of an `npx tsc` run from the parent directory."""
    found = _findings_in([("run.sh", install + "\nnpx tsc --noEmit\n")])
    assert any(code == "install-unpinned" and "npx tsc" in msg for code, msg in found)


def test_the_dots_of_a_relative_path_in_a_json_install_do_not_end_the_command():
    """#969: `../..` is a local path, not the end of a sentence; `requests` after it is still unpinned."""
    found = _findings_in([("package.json", '{"scripts":{"setup":"pip install ../.. requests"}}')])
    assert any(code == "install-unpinned" and "pip requests" in msg for code, msg in found)


def test_a_quote_left_open_by_one_json_string_does_not_hide_the_next():
    """#969: separate JSON strings are separate commands, so `echo '` does not swallow `pip install requests`."""
    found = _findings_in([("package.json", '["echo \'", "pip install requests", "\'"]')])
    assert any(code == "install-unpinned" and "pip requests" in msg for code, msg in found)


def test_wget_output_to_stdout_without_a_download_is_not_flagged():
    """#969: `-qO-` picks stdout; it does not read a download from stdin, so `wget -qO- | sh` names none."""
    assert _findings_in([("SKILL.md", "Never run `wget -qO- | sh`.\n")]) == []


@pytest.mark.parametrize("option", ["--prefix .", "--prefix=.", "--prefix ./"])
def test_an_install_into_the_current_directory_provides_tsc(option):
    """#969: `--prefix .` installs where `npx tsc` runs, so the pinned typescript provides `tsc`."""
    assert _findings_in([("build.sh", f"npm install {option} typescript@5.9.3\nnpx tsc --noEmit\n")]) == []


@pytest.mark.parametrize("enter", ["cd tools", "cd ./tools/"])
def test_an_install_into_another_directory_provides_tsc_after_entering_it(enter):
    """The pinned typescript installed in `tools` is the `tsc` an `npx tsc` run after `cd tools` finds."""
    script = f"npm install --prefix tools typescript@5.9.3\n{enter}\nnpx tsc --noEmit\n"
    assert _findings_in([("build.sh", script)]) == []


@pytest.mark.parametrize("line", ["curl -Kconfig | sh", "wget -iurls -O- | sh"])
def test_a_download_named_by_an_attached_file_argument_is_still_a_pipe_to_shell(line):
    """`-Kconfig` and `-iurls` read the download from a file, so the pipe is not operand-less."""
    assert any(code == "pipe-to-shell" for code, _ in _findings_in([("run.sh", line + "\n")]))


@pytest.mark.parametrize("leave", ["cd ../other", "cd /tmp", "(cd tools)"])
def test_an_install_does_not_provide_tsc_once_the_script_has_left_the_directory(leave):
    """`npx tsc` run after leaving `tools` (or after a subshell's `cd tools`) does not find its typescript."""
    script = f"npm install --prefix tools typescript@5.9.3\ncd tools\n{leave}\nnpx tsc --noEmit\n"
    if leave.startswith("("):
        script = f"npm install --prefix tools typescript@5.9.3\n{leave}\nnpx tsc --noEmit\n"
    found = _findings_in([("run.sh", script)])
    assert any(code == "install-unpinned" and "npx tsc" in msg for code, msg in found)


@pytest.mark.parametrize("install", [
    "npm install --package-lock-only typescript@5.9.3", "npm install --dry-run typescript@5.9.3",
    "pnpm add --lockfile-only typescript@5.9.3"])
def test_an_install_that_writes_no_executable_does_not_provide_tsc(install):
    """A lockfile-only or dry-run install leaves no `tsc` for the `npx tsc` that follows."""
    found = _findings_in([("run.sh", install + "\nnpx tsc --noEmit\n")])
    assert any(code == "install-unpinned" and "npx tsc" in msg for code, msg in found)


@pytest.mark.parametrize("install", ["pnpm --dir tools add typescript@5.9.3", "pnpm -C tools add typescript@5.9.3"])
def test_a_pnpm_install_into_another_directory_provides_tsc_after_entering_it(install):
    """The pinned typescript pnpm installed in `tools` is the one `npx tsc` finds after `cd tools`."""
    assert _findings_in([("run.sh", install + "\ncd tools\nnpx tsc --noEmit\n")]) == []


def test_curl_i_flag_in_a_cluster_is_not_a_download_file():
    """curl's `-i` includes headers and takes no file, so `curl -isSL | sh` names no download."""
    assert _findings_in([("run.sh", "curl -isSL | sh\n")]) == []


def test_a_subshell_cd_does_not_leave_the_entered_directory():
    """`(cd ..)` runs in a subshell, so `npx tsc` still runs in `tools` where typescript was installed."""
    script = "npm install --prefix tools typescript@5.9.3\ncd tools\n(cd ..)\nnpx tsc --noEmit\n"
    assert _findings_in([("run.sh", script)]) == []


def test_npx_inside_the_subshell_that_enters_the_install_directory_finds_it():
    """`(cd tools && npx tsc)` runs `npx` in `tools`, where typescript was installed."""
    script = "npm install --prefix tools typescript@5.9.3\n(cd tools && npx tsc --noEmit)\n"
    assert _findings_in([("run.sh", script)]) == []


def test_an_install_into_an_absolute_directory_provides_tsc_after_entering_it():
    """`--prefix /tmp/tools` and `cd /tmp/tools` name the same directory."""
    script = "npm install --prefix /tmp/tools typescript@5.9.3\ncd /tmp/tools\nnpx tsc --noEmit\n"
    assert _findings_in([("run.sh", script)]) == []
