# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The skills bundled under ``data/skills/`` load as treatments and their links resolve.

Commands, flags and paths these skills name are checked by ``test_docs_code_refs``; this
file checks the bundle itself: the frontmatter a skill arm reads, a listing entry the
harness shows in full, and relative links to the skill's own reference files.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.harnesses.registry import known_harnesses
from dokimi.harnesses.skill_treatment import check_skill
from dokimi.repos.budget import LISTING_TEXT_CAP
from dokimi.utils import parse_frontmatter

SKILLS = Path(__file__).resolve().parents[1] / "data" / "skills"
BUNDLED = sorted(d for d in SKILLS.iterdir() if (d / "SKILL.md").is_file())
LINK = re.compile(r"\]\(<?([^)#<>\s][^)#<>]*?)>?(?:#[^)\s]*)?(?:\s+\"[^\"]*\")?\)")
JUDGE_VALIDITY = re.compile(r"python cli\.py eval judge-validity[^`\n]*")
SHELL_BLOCK = re.compile(r"^```(?:bash|sh|shell)\s*\n(.*?)^```", re.M | re.S)


def _broken_links(skill_dir: Path, root: Path = SKILLS) -> list[str]:
    broken = []
    for doc in sorted(skill_dir.rglob("*.md")):
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            if re.match(r"[a-z]+:", target):
                continue
            resolved = (doc.parent / target).resolve()
            if not resolved.exists() or not resolved.is_relative_to(skill_dir.resolve()):
                broken.append(f"{doc.relative_to(root)} -> {target}")
    return broken


def test_the_bundle_is_not_empty():
    assert {d.name for d in BUNDLED} >= {"fastcontext", "dokimi-design-evals"}


@pytest.mark.parametrize("skill_dir", BUNDLED, ids=lambda d: d.name)
def test_bundled_skill_loads_as_a_treatment_named_after_its_directory(skill_dir):
    assert check_skill(skill_dir) == skill_dir.name


@pytest.mark.parametrize("skill_dir", BUNDLED, ids=lambda d: d.name)
def test_bundled_skill_description_fits_the_listing(skill_dir):
    meta, _ = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    description = str(meta.get("description", "")).strip()
    assert 40 <= len(description) <= LISTING_TEXT_CAP, description


@pytest.mark.parametrize("skill_dir", BUNDLED, ids=lambda d: d.name)
def test_bundled_skill_links_stay_inside_and_resolve(skill_dir):
    assert _broken_links(skill_dir) == []


@pytest.mark.parametrize("skill_dir", BUNDLED, ids=lambda d: d.name)
def test_bundled_skill_shell_blocks_run_only_cli_commands(skill_dir):
    # test_docs_code_refs resolves `python cli.py` lines; a raw `docker run` drifts from a harness.
    other = []
    for doc in sorted(skill_dir.rglob("*.md")):
        for block in SHELL_BLOCK.findall(doc.read_text(encoding="utf-8")):
            lines = [ln.strip() for ln in re.sub(r"\\\r?\n", " ", block).splitlines()]
            other += [ln for ln in lines if ln and not ln.startswith(("#", "python cli.py "))]
    assert other == []


@pytest.mark.parametrize("skill_dir", BUNDLED, ids=lambda d: d.name)
def test_bundled_skill_judge_validity_commands_fail_on_an_inconclusive_run(skill_dir):
    # Without --strict an inconclusive judge-validity run exits 0 (test_judge_validity).
    for doc in sorted(skill_dir.rglob("*.md")):
        for command in JUDGE_VALIDITY.findall(doc.read_text(encoding="utf-8")):
            args = build_parser().parse_args(shlex.split(command)[2:])
            assert args.strict, f"{doc.relative_to(SKILLS)}: {command}"


def test_the_link_check_catches_a_dangling_and_an_escaping_link(tmp_path):
    skill = tmp_path / "s"
    (skill / "references").mkdir(parents=True)
    (skill / "references" / "ok.md").write_text("x")
    (tmp_path / "outside.md").write_text("x")
    (skill / "SKILL.md").write_text(
        "[a](references/ok.md) [b](references/gone.md) [c](../outside.md) [d](https://x.org)\n"
        '[e](references/gone.md "title") [f](<references/a b.md>) [g](references/ok.md#top "t")\n')
    assert _broken_links(skill, tmp_path) == [
        "s/SKILL.md -> references/gone.md", "s/SKILL.md -> ../outside.md",
        "s/SKILL.md -> references/gone.md", "s/SKILL.md -> references/a b.md"]


HARNESSES_FLAG = re.compile(r"--harnesses\s+(\S+)")


def _harness_names(skill_dir: Path) -> list[str]:
    names = []
    for doc in sorted(skill_dir.rglob("*.md")):
        for value in HARNESSES_FLAG.findall(doc.read_text(encoding="utf-8")):
            names += value.strip("`'\"").split(",")
    return names


def test_the_harness_name_check_reads_the_skills_commands():
    assert "docker-oracle" in _harness_names(SKILLS / "dokimi-design-evals")


@pytest.mark.parametrize("skill_dir", BUNDLED, ids=lambda d: d.name)
def test_bundled_skill_harness_names_are_registered(skill_dir):
    # test_docs_code_refs checks that a flag exists, not its value: a typo would pass.
    registered = {key.split(":")[0] for key in known_harnesses()}
    unknown = [n for n in _harness_names(skill_dir) if n.split(":")[0] not in registered]
    assert unknown == []
