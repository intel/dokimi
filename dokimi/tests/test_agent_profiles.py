# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Agent persona prompts: arm naming, provenance, and the committed profile fixtures."""

import logging
from pathlib import Path

import pytest

from dokimi.agent_profiles import load_agent_profile
from dokimi.treatments.arms import (
    AgentProfileTreatment,
    BaselineTreatment,
    CombinedTreatment,
    SkillTreatment,
)
from dokimi.treatments.factory import create_treatment, create_treatments
from dokimi.utils import digest_text

PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "agent_profiles"
SKILL_DIR = Path(__file__).resolve().parents[1] / "data" / "skills"
# The committed persona fixtures. Product personas are content and live in the external
# content repository; what stays here is the pair the framework's own examples use:
#   concise_expert   an answer-style persona (terse, code-first)
#   generic-expert   a persona with no product in it at all (negative control)
# A persona ladder — several profiles differing only in what the system prompt carries —
# is built out of an external checkout and passed as `profile:<path>` arms.
COMMITTED_PROFILES = [
    "concise_expert",
    "generic-expert",
]
COMMITTED_SKILL = "fastcontext"


def write_profile(tmp_path, stem, frontmatter, body):
    p = tmp_path / f"{stem}.md"
    p.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")
    return p


# --- arm naming -----------------------------------------------------------


def test_arm_name_comes_from_frontmatter_id_not_the_filename(tmp_path):
    p = write_profile(tmp_path, "some-file", "id: chosen-id\n", "You are an expert.\n")
    assert create_treatment(f"profile:{p.as_posix()}").name == "profile:chosen-id"


def test_arm_name_falls_back_to_filename_stem_when_frontmatter_is_malformed(tmp_path):
    """A YAML typo is silent: meta becomes {} and the id falls back to the stem.

    The committed fixtures are named so that stem == id, which makes them immune.
    """
    p = write_profile(tmp_path, "typo-profile", "id: broken\n  name: [unclosed\n", "You are an expert.\n")
    assert load_agent_profile(p).id == "typo-profile"
    assert create_treatment(f"profile:{p.as_posix()}").name == "profile:typo-profile"


@pytest.mark.parametrize("profile_id", COMMITTED_PROFILES)
def test_committed_fixture_stem_equals_id(profile_id):
    """Guards the arm name in every artifact against a frontmatter typo."""
    profile = load_agent_profile(PROFILE_DIR / f"{profile_id}.md")
    assert profile.id == profile_id == profile.path.stem


# --- the prompt actually reaches the model --------------------------------


@pytest.mark.parametrize("profile_id", COMMITTED_PROFILES)
def test_prompt_reaches_agent_config_verbatim(profile_id):
    profile = load_agent_profile(PROFILE_DIR / f"{profile_id}.md")
    cfg = AgentProfileTreatment(profile).prepare("some question", "demo")
    assert cfg.system_prompt == profile.system_prompt
    assert "description:" not in cfg.system_prompt  # no frontmatter leak


def test_empty_body_is_rejected(tmp_path):
    p = write_profile(tmp_path, "hollow", "id: hollow\n", "\n")
    with pytest.raises(ValueError, match="empty body"):
        load_agent_profile(p)


# --- provenance -----------------------------------------------------------


def test_metadata_stamps_path_and_prompt_digest(tmp_path):
    p = write_profile(tmp_path, "stamped", "id: stamped\n", "You are an expert.\n")
    meta = create_treatment(f"profile:{p.as_posix()}").prepare("q", "demo").metadata
    assert meta["arm_kind"] == "agent_profile"
    assert meta["profile_path"] == str(p)
    assert meta["profile_prompt_sha256"] == digest_text("You are an expert.")
    assert meta["profile_prompt_chars"] == len("You are an expert.")


def test_editing_the_prompt_changes_the_recorded_digest(tmp_path):
    """Without this, two different prompts are indistinguishable in an artifact."""
    p = write_profile(tmp_path, "mutable", "id: mutable\n", "You are an expert.\n")
    before = create_treatment(f"profile:{p.as_posix()}").prepare("q", "demo").metadata
    write_profile(tmp_path, "mutable", "id: mutable\n", "You are an expert!\n")
    after = create_treatment(f"profile:{p.as_posix()}").prepare("q", "demo").metadata
    assert before["profile_prompt_sha256"] != after["profile_prompt_sha256"]


def test_frontmatter_only_edits_do_not_change_the_prompt_digest(tmp_path):
    """The digest covers the prompt text, which is what the model actually sees."""
    p = write_profile(tmp_path, "renamed", "id: renamed\nname: First\n", "You are an expert.\n")
    before = create_treatment(f"profile:{p.as_posix()}").prepare("q", "demo").metadata
    write_profile(tmp_path, "renamed", "id: renamed\nname: Second\n", "You are an expert.\n")
    after = create_treatment(f"profile:{p.as_posix()}").prepare("q", "demo").metadata
    assert before["profile_prompt_sha256"] == after["profile_prompt_sha256"]
    assert before["profile_name"] != after["profile_name"]


# --- channel parity: neither channel may carry an uncapped payload --------
#
# The whole `p:skilltext` vs `skills` comparison rests on the two channels being able to
# carry the same amount of text. Skills have always capped at 12,000 chars; profiles did
# not, so a long profile would have been compared against a truncated skill and the
# difference read as "the system prompt works better".


def test_profile_and_skill_caps_agree():
    from dokimi.skills import load_skill

    profile = load_agent_profile(PROFILE_DIR / "concise_expert.md")
    skill = load_skill(SKILL_DIR / COMMITTED_SKILL)
    assert AgentProfileTreatment(profile).max_chars == SkillTreatment(skill).max_chars == 12_000


def test_long_profile_is_capped_and_the_truncation_is_recorded(tmp_path, caplog):
    body = "word " * 6_000  # 30,000 chars
    p = write_profile(tmp_path, "verbose", "id: verbose\n", body)
    treatment = AgentProfileTreatment(load_agent_profile(p), max_chars=100)
    with caplog.at_level(logging.WARNING):
        cfg = treatment.prepare("q", "demo")
    assert len(cfg.system_prompt) <= 100 + len("\n…")
    assert cfg.metadata["profile_prompt_truncated"] is True
    assert cfg.metadata["profile_prompt_chars_source"] == len(body.strip())
    assert cfg.metadata["profile_prompt_chars"] == len(cfg.system_prompt)
    assert "truncated" in caplog.text


def test_capping_changes_the_digest_so_the_stamp_matches_what_was_sent(tmp_path):
    """The digest must cover the text the model saw, or a capped cell reads as a full one."""
    p = write_profile(tmp_path, "verbose", "id: verbose\n", "word " * 6_000)
    full = AgentProfileTreatment(load_agent_profile(p)).prepare("q", "demo")
    capped = AgentProfileTreatment(load_agent_profile(p), max_chars=100).prepare("q", "demo")
    assert capped.metadata["profile_prompt_sha256"] != full.metadata["profile_prompt_sha256"]
    assert capped.metadata["profile_prompt_sha256"] == digest_text(capped.system_prompt)


def test_a_profile_one_char_over_the_cap_still_reports_truncation(tmp_path):
    """The cap appends an ellipsis, so a length comparison would call this untruncated."""
    p = write_profile(tmp_path, "borderline", "id: borderline\n", "x" * 101)
    cfg = AgentProfileTreatment(load_agent_profile(p), max_chars=100).prepare("q", "demo")
    assert cfg.metadata["profile_prompt_truncated"] is True


def test_a_profile_at_the_cap_is_untouched(tmp_path):
    p = write_profile(tmp_path, "exact", "id: exact\n", "x" * 100)
    cfg = AgentProfileTreatment(load_agent_profile(p), max_chars=100).prepare("q", "demo")
    assert cfg.system_prompt == "x" * 100
    assert cfg.metadata["profile_prompt_truncated"] is False


def test_the_committed_fixtures_all_fit_under_the_cap():
    """A fixture over the cap would silently measure a prefix of itself."""
    for profile_id in COMMITTED_PROFILES:
        profile = load_agent_profile(PROFILE_DIR / f"{profile_id}.md")
        cfg = AgentProfileTreatment(profile).prepare("q", "demo")
        assert cfg.metadata["profile_prompt_truncated"] is False, profile_id


CAP = 12_000


def injected_fixture_sizes() -> dict[str, int]:
    """Rendered size of every payload that is *injected* into a prompt, by fixture name.

    Two channels, both capped at :data:`CAP`: a profile body sent as a system prompt, and a
    ``SKILL.md`` body injected as context. Bundled skill resources are a third channel with
    the same cap, but they are served on demand by ``ViewSkillTool`` and are covered by
    :func:`test_over_cap_bundled_resources_are_known` instead — conflating the two is what
    let an over-cap resource sit unnoticed while the docs claimed full headroom.
    """
    from dokimi.skills import load_skill

    sizes = {}
    for profile_path in sorted(PROFILE_DIR.glob("*.md")):
        sizes[profile_path.name] = len(load_agent_profile(profile_path).as_prompt(CAP * 10))
    for skill_dir in sorted(SKILL_DIR.iterdir()):
        if (skill_dir / "SKILL.md").exists():
            sizes[skill_dir.name] = len(load_skill(skill_dir).as_context(CAP * 10))
    return sizes


def test_no_injected_fixture_is_within_5_percent_of_the_cap():
    """Fail *before* a fixture starts getting truncated, not after.

    A fixture crossing the cap still measures a prefix of itself; the metadata says so, but
    the cell is a different treatment either way and the batch has to be re-run. Growth in a
    skill body is the realistic failure, not an author writing a 30,000-char profile. On
    failure: split the fixture, or raise the cap in *every* channel at once (skills,
    profiles, ``ViewSkillTool``) so the channel comparison stays honest.

    The message prints the current largest fixture rather than asserting a remembered
    percentage — hardcoded numbers in prose went stale the first time a fixture was edited.
    The scan covers whatever is on disk under ``data/``, including local fixtures not yet
    committed, since those get measured too.
    """
    sizes = injected_fixture_sizes()
    # Without this the whole guard is vacuous: an empty data/ directory reports "1 passed"
    # while measuring nothing, and the 12,000-char invariant the docs lean on would be
    # permanently green and meaningless.
    profiles = [name for name in sizes if name.endswith(".md")]
    skills = [name for name in sizes if not name.endswith(".md")]
    assert profiles and skills, (
        f"the cap guard measured {len(profiles)} profile(s) and {len(skills)} skill(s) under "
        f"{PROFILE_DIR} and {SKILL_DIR}; both channels must be represented or the 12,000-char "
        "invariant is only being checked on one of them."
    )
    budget = int(CAP * 0.95)
    oversized = [
        f"{name}: {chars} chars ({chars / CAP:.0%} of cap)"
        for name, chars in sorted(sizes.items())
        if chars > budget
    ]
    largest = max(sizes.items(), key=lambda kv: kv[1])
    assert not oversized, (
        f"Injected fixtures within 5% of the {CAP:,}-char cap: {'; '.join(oversized)}. "
        f"Largest on disk: {largest[0]} at {largest[1]} chars ({largest[1] / CAP:.0%}). "
        "See Skill.as_context for where the cap comes from and what changing it costs."
    )


# Known-truncated committed bundled resources. Empty since the product skills moved to the
# external content repository — the entry that used to be here was one of their resource
# files. Anything *new* going over the cap fails until it is split or listed here with a
# reason, which is what keeps the truncation visible in code instead of hidden.
OVER_CAP_RESOURCES: set[str] = set()


def test_over_cap_bundled_resources_are_known():
    """``ViewSkillTool`` caps bundled files at the same 12,000 chars as the injected
    channels, so a long resource is served as a prefix. That is tolerable only while we know
    which files it happens to.

    The scan covers every committed ``.md`` the tool can serve, ``SKILL.md`` included:
    ``call()`` reaches it through the same ``max_chars`` cap. Skipping ``SKILL.md`` left the
    loop with nothing to measure once the multi-file product skills moved out, so the test
    passed by scanning zero files while its own guard reported it was not vacuous."""
    import subprocess

    # Committed files only. An uncommitted scratch skill under data/ is the author's
    # business; what must not drift is what ships.
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "-z", "--", str(SKILL_DIR)],
            capture_output=True, check=True, cwd=SKILL_DIR.parents[1],
        ).stdout.decode("utf-8").split("\0")
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - no git available
        pytest.skip("git is unavailable, cannot tell committed fixtures from scratch ones")

    repo_root = SKILL_DIR.parents[1]
    over_cap = set()
    scanned = []
    for rel in tracked:
        if not rel.endswith(".md"):
            continue
        path = repo_root / rel
        if not path.is_file():
            continue
        scanned.append(path.relative_to(SKILL_DIR).as_posix())
        if len(path.read_text(encoding="utf-8")) > CAP:
            over_cap.add(path.relative_to(SKILL_DIR).as_posix())

    # The scan walks a directory, so it would report success on an empty one. The guard has
    # to count the files actually measured, not the files that happen to be committed.
    assert scanned, (
        f"no committed skill markdown found under {SKILL_DIR} — this test would pass by "
        "scanning nothing. Keep a committed skill fixture, or delete the test with the cap."
    )

    assert not (over_cap - OVER_CAP_RESOURCES), (
        f"New bundled resources exceed the {CAP:,}-char cap and will be served truncated: "
        f"{sorted(over_cap - OVER_CAP_RESOURCES)}. Split them, or add them to "
        "OVER_CAP_RESOURCES with a reason."
    )
    assert not (OVER_CAP_RESOURCES - over_cap), (
        f"OVER_CAP_RESOURCES is stale — these now fit under the cap: "
        f"{sorted(OVER_CAP_RESOURCES - over_cap)}. Drop them from the allowlist so it keeps "
        "meaning 'known to be truncated'."
    )


def test_a_viewed_skill_file_over_the_cap_is_recorded_as_truncated(tmp_path):
    """The injected channels record truncation; the file-view channel used not to, which is
    how an over-cap committed resource stayed invisible."""
    from dokimi.skills import load_skill
    from dokimi.treatments.tools import ViewSkillTool

    d = tmp_path / "big-resource-skill"
    (d / "patterns").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: big-resource-skill\ndescription: A skill with a long resource.\n---\nBody.\n",
        encoding="utf-8",
    )
    (d / "patterns" / "long.md").write_text("x" * 25_000, encoding="utf-8")

    tool = ViewSkillTool(load_skill(d))
    rendered = tool.call(file="patterns/long.md")
    assert rendered.endswith("…")
    assert tool.telemetry_metadata()["skill_file_truncated"] is True
    assert tool.telemetry_metadata()["skill_file_chars_source"] == {"patterns/long.md": 25_000}


def test_a_viewed_skill_file_under_the_cap_records_no_truncation(tmp_path):
    from dokimi.skills import load_skill
    from dokimi.treatments.tools import ViewSkillTool

    d = tmp_path / "small-resource-skill"
    (d / "patterns").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: small-resource-skill\ndescription: A skill with a short resource.\n---\nBody.\n",
        encoding="utf-8",
    )
    (d / "patterns" / "short.md").write_text("x" * 100, encoding="utf-8")

    tool = ViewSkillTool(load_skill(d))
    tool.call(file="patterns/short.md")
    meta = tool.telemetry_metadata()
    assert meta["skill_file_truncated"] is False
    assert "skill_file_chars_source" not in meta


def test_skill_truncation_is_recorded_on_the_same_terms(tmp_path, caplog):
    """`as_context` truncates silently; an unrecorded truncation makes the artifact lie."""
    from dokimi.skills import load_skill

    d = tmp_path / "verbose-skill"
    d.mkdir()
    body = "word " * 6_000
    (d / "SKILL.md").write_text(
        f"---\nname: verbose-skill\ndescription: A long skill.\n---\n{body}", encoding="utf-8"
    )
    treatment = SkillTreatment(load_skill(d), max_chars=100)
    with caplog.at_level(logging.WARNING):
        cfg = treatment.prepare("q", "demo")
    assert cfg.metadata["skill_truncated"] is True
    assert cfg.metadata["skill_chars_source"] == len(body.strip())
    # `skill_chars` is the body after the cap, so it is comparable with the cap and with
    # the source; the rendered block is a third number and carries the header too.
    assert cfg.metadata["skill_chars"] == 100
    assert cfg.metadata["skill_chars_injected"] == len(cfg.injected_context[0]["content"])
    assert "truncated" in caplog.text


def test_short_skill_records_no_truncation():
    from dokimi.skills import load_skill

    skill = load_skill(SKILL_DIR / COMMITTED_SKILL)
    cfg = SkillTreatment(skill).prepare("q", "demo")
    assert cfg.metadata["skill_truncated"] is False
    # Nothing was cut, so the two body sizes agree — the rendered block is larger, which
    # is the confusion the third field exists to prevent.
    assert cfg.metadata["skill_chars"] == cfg.metadata["skill_chars_source"]
    assert cfg.metadata["skill_chars"] == len(skill.body.strip())
    assert cfg.metadata["skill_chars_injected"] == len(cfg.injected_context[0]["content"])
    assert cfg.metadata["skill_chars_injected"] > cfg.metadata["skill_chars"]


# --- combined arms must not silently drop a persona -----------------------


def test_combined_arm_warns_and_records_dropped_system_prompts(tmp_path, caplog):
    a = write_profile(tmp_path, "first", "id: first\n", "Prompt A.\n")
    b = write_profile(tmp_path, "second", "id: second\n", "Prompt B.\n")
    combined = CombinedTreatment(
        [create_treatment(f"profile:{a.as_posix()}"), create_treatment(f"profile:{b.as_posix()}")]
    )
    with caplog.at_level(logging.WARNING):
        cfg = combined.prepare("q", "demo")
    assert cfg.system_prompt == "Prompt A."
    assert cfg.metadata["dropped_system_prompts"] == ["profile:second"]
    assert "only 'profile:first' is sent" in caplog.text


def test_combined_arm_stays_quiet_when_only_one_prompt_is_present(tmp_path, caplog):
    """docs+skill combos supply no system prompt — they must not start warning."""
    a = write_profile(tmp_path, "solo", "id: solo\n", "Prompt A.\n")
    combined = CombinedTreatment([BaselineTreatment(), create_treatment(f"profile:{a.as_posix()}")])
    with caplog.at_level(logging.WARNING):
        cfg = combined.prepare("q", "demo")
    assert cfg.system_prompt == "Prompt A."
    assert "dropped_system_prompts" not in cfg.metadata
    assert caplog.text == ""


# --- what a persona is allowed to contain ---------------------------------
#
# A persona ladder is only interpretable as a comparison while the rungs stay distinct.
# Nothing in the loader enforces that -- a well-meaning edit can turn a persona into a second
# copy of the skill it is compared against, at which point the two arms measure the same
# thing and earlier cells are silently incomparable to later ones. These tests pin the
# contrast for the committed personas and for the detector itself.


def five_grams(text: str) -> set[tuple[str, ...]]:
    words = text.lower().split()
    return {tuple(words[i : i + 5]) for i in range(len(words) - 4)}


# A persona that names an API is documentation, and it dates the way documentation dates.
# The shapes are deliberately wider than a dotted module name: a keyword argument or a bare
# call is just as much an API name, and the first version of this test let all of them
# through. They are product-agnostic on purpose — the check has to survive the persona being
# about a different library than the one it was written for.
API_SHAPES = (
    r"\b[a-z_][a-z0-9_]*\s*\.\s*[a-z_]\w*\s*\(",  # dotted calls, spaced or not
    r"\b[a-z_][a-z0-9_]*\(\s*\)",  # bare zero-arg calls, e.g. asnumpy()
    r"\b[a-z_][a-z0-9_]*\s*=\s*[\"'\w]",  # keyword arguments / assignments
)


@pytest.mark.parametrize("profile_id", COMMITTED_PROFILES)
def test_deliverable_persona_carries_no_api_names(profile_id):
    """A persona states how to think. The moment it lists an API, it is documentation --
    and documentation in a system prompt also rots without a test noticing."""
    import re

    prompt = load_agent_profile(PROFILE_DIR / f"{profile_id}.md").system_prompt
    leaked = sorted({m for shape in API_SHAPES for m in re.findall(shape, prompt)})
    assert leaked == [], f"{profile_id} names concrete APIs: {leaked}"
    assert "```" not in prompt, f"{profile_id} contains a code block"


def test_the_api_name_check_would_actually_fire():
    """The first version of this guard only matched one literal dotted prefix, so a persona
    could grow keyword arguments and bare calls untouched. Pin that it no longer can."""
    import re

    contaminated = (
        "Call asnumpy() once at the edge, pass usm_type='device' and sycl_queue=q to "
        "dpnp.tensor.empty(), and set dtype=float32."
    )
    leaked = {m for shape in API_SHAPES for m in re.findall(shape, contaminated)}
    assert leaked, "the API-name guard is vacuous"


def test_a_persona_that_paraphrases_its_skill_is_detected():
    """A persona arm and a skill arm only isolate the channel while their *content* differs.
    Nothing in the loader enforces that, so the five-gram overlap check is the detector — and
    a detector that cannot fire is worse than none, because it reads as a guarantee.
    """
    skill_body = (
        "Locate the right files and line ranges before answering, then read narrow windows "
        "around those citations instead of scanning the whole repository."
    )
    paraphrase = (
        "You are an expert. Locate the right files and line ranges before answering, then "
        "stop."
    )
    distinct = (
        "You are an expert. State your assumption in one line when a question is ambiguous, "
        "and lead with the smallest example that settles it."
    )

    assert five_grams(paraphrase) & five_grams(skill_body), (
        "the overlap check no longer detects reused wording"
    )
    assert not (five_grams(distinct) & five_grams(skill_body))


@pytest.mark.parametrize("profile_id", COMMITTED_PROFILES)
def test_committed_persona_does_not_paraphrase_the_committed_skill(profile_id):
    """The same check applied to what is actually in the tree, so the committed pair stays
    usable as a persona-vs-skill comparison."""
    from dokimi.skills import load_skill

    persona = five_grams(load_agent_profile(PROFILE_DIR / f"{profile_id}.md").system_prompt)
    skill = five_grams(load_skill(SKILL_DIR / COMMITTED_SKILL).as_context(CAP))
    assert persona and skill, "one side of the comparison is empty"
    shared = persona & skill
    assert not shared, f"{profile_id} reuses skill wording: {sorted(shared)[:3]}"


def test_negative_control_persona_mentions_no_product():
    """The negative control is only a control while it names no product at all. The denylist
    is the product registry rather than a hand-written list, so registering a new product
    also starts guarding against it."""
    import re

    from dokimi.registry import ProductRegistry

    keys = ProductRegistry().keys()
    assert keys, "the product registry is empty, so this guard checks nothing"

    prompt = load_agent_profile(PROFILE_DIR / "generic-expert.md").system_prompt.lower()
    for key in keys:
        for token in {key, key.replace("_", "-"), key.replace("_", " ")}:
            assert not re.search(rf"\b{re.escape(token)}\b", prompt), (
                f"the generic control mentions the registered product {key}"
            )


def test_the_product_mention_check_would_actually_fire():
    import re

    from dokimi.registry import ProductRegistry

    key = sorted(ProductRegistry().keys())[0]
    contaminated = f"You are an expert in {key.replace('_', ' ')} and nothing else."
    hits = [
        k for k in ProductRegistry().keys()
        for token in {k, k.replace("_", "-"), k.replace("_", " ")}
        if re.search(rf"\b{re.escape(token)}\b", contaminated)
    ]
    assert hits, "the product-mention guard is vacuous"


# --- the measurement arms build together ---------------------------------


def test_the_measurement_arms_build_in_one_run():
    specs = [
        "baseline",
        *(f"profile:{(PROFILE_DIR / f'{p}.md').as_posix()}" for p in COMMITTED_PROFILES),
    ]
    assert [t.name for t in create_treatments(specs)] == [
        "baseline",
        *(f"profile:{p}" for p in COMMITTED_PROFILES),
    ]


def test_two_profiles_sharing_an_id_are_refused(tmp_path):
    a = write_profile(tmp_path, "one", "id: same\n", "Prompt A.\n")
    b = write_profile(tmp_path, "two", "id: same\n", "Prompt B.\n")
    with pytest.raises(ValueError):
        create_treatments([f"profile:{a.as_posix()}", f"profile:{b.as_posix()}"])
