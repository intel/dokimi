# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Arms runs, one or a matrix of them, from a typed run configuration.

``arms run``, ``arms matrix-run`` and ``subjects run`` each build an :class:`ArmsRunConfig`
once from their parsed flags; a matrix cell is that config with the cell's model, provider,
harness and plugins put in (:func:`cell_config`). :func:`run_arms` checks the config, builds
the arms, the judge and the :class:`~dokimi.eval.arm_runner.ArmRunner`, and returns the
``arms.v1`` output; :func:`run_matrix` does that for every cell, after checking them all.
Paths, saving, the rollup and exit codes stay with the commands.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, replace

from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_JUDGE_PROVIDER,
    JUDGE_MODEL_FOR_PROVIDER,
    default_judge_model,
)
from dokimi.eval.cells import HARNESS_ARMS_RUNNER, MatrixCellDescriptor
from dokimi.eval.question_text import EmptyQuestionError
from dokimi.utils import question_id


@dataclass(frozen=True)
class ArmsRunConfig:
    """Everything an arms run reads besides its arm specs and its questions.

    The field names are the ``dest`` names of the ``arms run`` flags, and a value is in the
    flag's form (``plugins`` and ``judge_panel`` stay comma-separated). A default is what
    ``arms run`` runs with when the flag is left out, so a command without the flag
    (``subjects run`` has no ``--temperature``) runs with it too.
    """

    product: str
    model: str = DEFAULT_ANSWER_MODEL
    provider: str = DEFAULT_ANSWER_PROVIDER
    harness: str = HARNESS_ARMS_RUNNER
    plugins: str = ""
    #: The matrix cell this run is, stamped into the output; ``None`` outside a matrix.
    matrix_cell: str | None = None
    context7_id: str | None = None
    baseline_arm: str = "baseline"
    stats_correction: str = "holm"
    judge: bool = False
    #: Left out, the default judge that runs on ``judge_provider``, as for ``--judge-model``.
    judge_model: str = JUDGE_MODEL_FOR_PROVIDER
    judge_provider: str = DEFAULT_JUDGE_PROVIDER
    judge_temperature: float | None = None
    judge_panel: str = ""
    top_k: int = 5
    rerank_threshold: float = 0.3
    max_iterations: int = 6
    max_tokens: int | None = None
    grounded_from_topics: bool = False
    temperature: float | None = None
    seed: int | None = None
    session_turns: int | None = None
    turn_reminder: str | None = None
    concurrency: int = 5

    def __post_init__(self) -> None:
        if self.judge_model is JUDGE_MODEL_FOR_PROVIDER:
            model = default_judge_model(self.judge_provider)
            if model is None:
                raise ValueError(f"judge provider {self.judge_provider!r} has no default judge "
                                 f"model; pass judge_model")
            object.__setattr__(self, "judge_model", model)


class LibraryIdError(ValueError):
    """No library id can be named for a doc arm, so the run stops before any answer."""


class MatrixCellError(ValueError):
    """A matrix cell refused or failed its run: the reason's message, and the cell's name."""

    def __init__(self, cell: str, reason: Exception) -> None:
        super().__init__(str(reason))
        self.cell = cell


def cell_config(config: ArmsRunConfig, cell: MatrixCellDescriptor) -> ArmsRunConfig:
    """*config* as matrix cell *cell* runs it: the cell's name, model, provider, harness, plugins."""
    return replace(config, matrix_cell=cell.name, model=cell.model, provider=cell.provider,
                   harness=cell.harness, plugins=",".join(cell.plugin_specs))


def validate_supported_matrix_harnesses(cells) -> None:
    """Reject matrix harness labels that the in-process runner cannot execute."""
    unsupported = [
        cell.harness
        for cell in cells
        if cell.harness != HARNESS_ARMS_RUNNER
    ]
    if unsupported:
        raise ValueError(
            "matrix cells only support harness='arms-runner' until other runners are wired; "
            "unsupported harnesses: "
            + ", ".join(sorted(set(unsupported)))
        )


def resolve_doc_library_id(treatments, product: str, explicit_id=None, registry=None):
    """Resolve a library id for the first doc arm, before plugin wrapping.

    Same three-step order as the ``answers``/``questions`` commands: explicit
    flag, then the product's ``context7_id`` in ``products.yaml``, then the
    client's own resolution. When no id can be named the ``ValueError`` is
    raised to the caller, which stops the run: substituting the product name
    would query an id that does not exist and record an empty-retrieval doc
    arm as a measurement. Other failures (an unreachable MCP server, say) still
    fall back to the product name and are reported by the arm itself.
    """
    if explicit_id is not None:
        return explicit_id

    from dokimi.mcp.factory import resolve_library_id_for_product
    from dokimi.treatments.arms import DocTreatment

    for treatment in treatments:
        unwrapped = getattr(treatment, "inner", treatment)
        if isinstance(unwrapped, DocTreatment):
            try:
                return resolve_library_id_for_product(
                    product, unwrapped.mcp_client, registry=registry
                )
            except ValueError:
                raise
            except Exception:
                return product
    return None


def _offered_skill_names(treatments) -> set:
    """The names of the skills any arm in *treatments* offers, past plugins and stacks."""
    from dokimi.treatments import SkillAgentTreatment, SkillCatalogTreatment

    def leaves(t):
        t = getattr(t, "inner", t)
        for sub in getattr(t, "treatments", None) or [t]:
            yield sub

    known: set = set()
    for t in treatments:
        for sub in leaves(t):
            if isinstance(sub, SkillCatalogTreatment):
                known.update(s.name for s in sub.skills)
            elif isinstance(sub, SkillAgentTreatment):
                known.add(sub.skill.name)
    return known


def validate_expected_skills(questions: list[dict], treatments) -> None:
    """Refuse ``expected_skill`` labels the selection summary cannot score.

    Checked before any call is spent: a label of the wrong type crashes the
    summary after the run, and a name no skill arm offers silently scores as a
    miss. Names are checked only when some arm offers skills.
    """
    known = _offered_skill_names(treatments)
    for i, q in enumerate(questions):
        if "expected_skill" not in q:
            continue
        label = q["expected_skill"]
        qid = question_id(q)
        qid = f"#{i}" if qid is None else qid
        names = label if isinstance(label, list) else [label] if label is not None else []
        if not all(isinstance(n, str) and n for n in names):
            raise ValueError(f"question {qid}: expected_skill must be a skill name, a list of "
                             f"names, or null; got {label!r}")
        unknown = [n for n in names if known and n not in known]
        if unknown:
            raise ValueError(f"question {qid}: expected_skill names no offered skill: "
                             f"{', '.join(unknown)}")


def check_sampling_support(config: ArmsRunConfig) -> None:
    """Reject sampling flags the answer provider would refuse on every answer."""
    from dokimi.llm import unsupported_sampling_params

    bad = unsupported_sampling_params(
        config.model, config.provider,
        temperature=config.temperature,
        seed=config.seed,
    )
    if bad:
        raise ValueError(
            f"Provider '{config.provider}' does not support {', '.join(bad)} for "
            f"model '{config.model}'. Drop the flag(s); every answer would "
            "otherwise fail and the report would show null scores."
        )
    probe_sampling(config.model, config.provider, config.temperature, config.seed)


@functools.lru_cache(maxsize=None)
def probe_sampling(model: str, provider: str, temperature, seed) -> None:
    """One short real call with the requested sampling, so a value the provider
    rejects (gpt-5 accepts only temperature=1) fails before the run, not per answer."""
    if temperature is None and seed is None:
        return
    from dokimi.llm import llm_call_with_usage

    try:
        llm_call_with_usage("Reply with OK.", model=model, provider=provider,
                            max_tokens=16, temperature=temperature, seed=seed)
    except Exception as exc:
        raise ValueError(
            f"Probe call to '{provider}/{model}' with temperature={temperature}, "
            f"seed={seed} failed, so every call in the run would too: {exc}"
        ) from exc


def _refuse_judged_session(config: ArmsRunConfig) -> None:
    """Refused before any answer is paid for: a session is measured, not judged."""
    if config.session_turns is not None and config.judge:
        raise ValueError("--session-turns is measurement-only; drop --judge")


def _treatments(config: ArmsRunConfig, specs: list[str], questions: list[dict]):
    """The arms of *specs*, wrapped in the config's plugins: (treatments, library id, plugin set)."""
    from dokimi.plugins import (
        create_plugins,
        plugin_set_metadata,
        validate_plugins_for_harness,
        wrap_treatments,
    )
    from dokimi.treatments import create_treatments

    treatments = create_treatments(specs, top_k=config.top_k, rerank_threshold=config.rerank_threshold)
    validate_expected_skills(questions, treatments)
    try:
        library_id = resolve_doc_library_id(treatments, config.product, config.context7_id)
    except ValueError as exc:
        raise LibraryIdError(str(exc)) from exc
    plugins = create_plugins([s.strip() for s in config.plugins.split(",") if s.strip()])
    validate_plugins_for_harness(plugins, config.harness)
    treatments = wrap_treatments(treatments, plugins)
    plugin_set = plugin_set_metadata(plugins)
    print(f"Arms: {', '.join(t.name for t in treatments)}")
    if config.matrix_cell:
        print(f"Matrix cell: {config.matrix_cell}")
    if plugin_set["plugins"]:
        print(f"Plugins: {plugin_set['plugin_set']} ({plugin_set['plugin_set_id']})")
    return treatments, library_id, plugin_set


def _check_judge(config: ArmsRunConfig, questions: list[dict]) -> None:
    """Refuse what the judge would refuse on every score, before any answer is paid for."""
    from dokimi.eval.question_text import require_question_text
    from dokimi.llm import unsupported_sampling_params
    from dokimi.utils import bedrock_litellm_model

    # The judge refuses a blank question; here, that is before any answer is paid for.
    # Every question is answered and then judged, so every one is checked.
    for i, q in enumerate(questions):
        require_question_text(q, i)
    judge_model = config.judge_model
    if config.judge_provider == "amazon-bedrock" and not config.judge_panel:
        # A single Bedrock judge goes through get_llm, which maps a bare name first.
        judge_model = bedrock_litellm_model(config.judge_model)
    bad_judge = unsupported_sampling_params(
        judge_model, config.judge_provider,
        temperature=config.judge_temperature,
    )
    if bad_judge:
        raise ValueError(
            f"Judge provider '{config.judge_provider}' does not support "
            f"{', '.join(bad_judge)} for model '{config.judge_model}'. Drop the "
            "flag; every score would otherwise be null."
        )
    probe_sampling(judge_model, config.judge_provider, config.judge_temperature, None)


def _build_judge(config: ArmsRunConfig):
    """The single judge or the panel the config names; an unsupported judge provider raises here."""
    from dokimi.eval import Judge, JudgePanel
    from dokimi.eval.panel import JUDGE_ROLES, JudgeConfig

    panel_roles = [r.strip() for r in config.judge_panel.split(",") if r.strip()]
    if not panel_roles:
        return Judge(model=config.judge_model, provider=config.judge_provider,
                     temperature=config.judge_temperature)
    unknown = [r for r in panel_roles if r not in JUDGE_ROLES]
    if unknown:
        raise ValueError(
            f"Unknown --judge-panel role(s): {', '.join(unknown)}. "
            f"Available: {', '.join(JUDGE_ROLES)}"
        )
    # A panel reports grader spread and agreement per answer, which a
    # single judge structurally cannot — judge choice alone moves scores.
    return JudgePanel(
        judges=[
            JudgeConfig(role=r, model=config.judge_model,
                        provider=config.judge_provider,
                        temperature=config.judge_temperature)
            for r in panel_roles
        ],
    )


def _judge_records(runner, judge, config: ArmsRunConfig, records: list[dict]):
    from dokimi.eval import JudgePanel

    if isinstance(judge, JudgePanel):
        roles = [j.role for j in judge.judges]
        print(f"Judging arms with a {len(roles)}-judge panel: {', '.join(roles)}")
    else:
        print("Judging arms...")
    return runner.judge(
        judge, config.product, records,
        baseline_arm=config.baseline_arm, concurrency=config.concurrency,
    )


def run_arms(config: ArmsRunConfig, specs: list[str], questions: list[dict]) -> dict:
    """Answer *questions* in every arm of *specs*, judge them when ``config.judge``, and
    return the ``arms.v1`` output.

    Everything that can be refused (the config, the arms, the plugins, the sampling, the
    judge) is refused before the first answer is paid for. A doc arm with no library id
    raises :class:`LibraryIdError`; anything else refused raises ``ValueError``.
    """
    from dokimi.eval.arm_runner import ArmRunner

    _refuse_judged_session(config)
    treatments, library_id, plugin_set = _treatments(config, specs, questions)
    check_sampling_support(config)
    judge = None
    if config.judge:
        _check_judge(config, questions)
        judge = _build_judge(config)

    runner = ArmRunner(
        treatments,
        model=config.model,
        provider=config.provider,
        max_iterations=config.max_iterations,
        harness=config.harness,
        plugin_set=plugin_set,
        matrix_cell=config.matrix_cell,
        max_tokens=config.max_tokens,
        grounded_from_topics=config.grounded_from_topics,
        stats_correction=config.stats_correction,
        temperature=config.temperature,
        seed=config.seed,
        session_turns=config.session_turns,
        turn_reminder=config.turn_reminder,
    )
    records = runner.run(
        library_name=config.product,
        questions=questions,
        library_id=library_id,
        concurrency=config.concurrency,
    )
    evaluations = None if judge is None else _judge_records(runner, judge, config, records)
    return runner.build_output(
        config.product, records, evaluations=evaluations, baseline_arm=config.baseline_arm,
        judge=judge,
    )


def check_matrix(config: ArmsRunConfig, cells: list[MatrixCellDescriptor]) -> None:
    """Refuse a matrix with a cell the in-process runner cannot run, or whose provider would
    refuse the sampling on every answer. Raises ``ValueError`` naming the first such cell."""
    for cell in cells:
        try:
            validate_supported_matrix_harnesses([cell])
        except ValueError as exc:
            raise ValueError(f"matrix cell '{cell.name}': {exc}") from exc
    for cell in cells:
        try:
            check_sampling_support(cell_config(config, cell))
        except ValueError as exc:
            raise ValueError(f"matrix cell '{cell.name}': {exc}") from exc


def run_matrix(config: ArmsRunConfig, cells: list[MatrixCellDescriptor], specs: list[str],
               questions: list[dict],
               on_cell: Callable[[MatrixCellDescriptor, dict], None]) -> None:
    """Run *config* in every cell in turn, handing each cell and its ``arms.v1`` output to
    *on_cell* as soon as the cell finishes, so a later cell's failure keeps the earlier ones.

    Every cell is checked (:func:`check_matrix`) before the first one is paid for. A cell
    that fails raises :class:`MatrixCellError` naming it; a bad question record
    (``EmptyQuestionError``) and :class:`LibraryIdError` are not a cell's and pass through.
    """
    check_matrix(config, cells)
    for cell in cells:
        try:
            output = run_arms(cell_config(config, cell), specs, questions)
        except (EmptyQuestionError, LibraryIdError):
            raise
        except (ValueError, OSError, RecursionError) as exc:
            raise MatrixCellError(cell.name, exc) from exc
        on_cell(cell, output)
