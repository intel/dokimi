# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Export benchmark artifacts to shareable, versioned dataset files.

Converts the JSON artifacts (questions/answers/eval/arms/task_runs) into tabular
form for reproducible sharing and analysis: JSONL (no extra deps), Parquet, or a
Hugging Face ``datasets`` dataset saved to disk. A dataset card (README.md) with
provenance is written alongside. For ``task_runs`` the per-cell telemetry is
lifted into ``metric_*`` columns so analysis tools see real columns.
"""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.artifacts import SCHEMA_VERSIONS, load_artifact

# Which list inside each artifact carries the per-row records.
RECORD_KEY = {
    "questions": "questions",
    "answers": "answers",
    "eval": "evaluations",
    "arms": "answers",
    "task_runs": "results",
}

# Telemetry/metrics fields lifted out of each task-run row's nested ``metrics``
# block into top-level columns (so analysis tools see them as real columns
# rather than one JSON-encoded blob).
_TASK_RUN_METRIC_COLS = (
    "cost_usd", "n_calls", "prompt_tokens", "completion_tokens",
    "cache_read_tokens", "cache_write_tokens", "total_tokens",
    "latency_sec", "ttft_sec", "mode", "error",
)

# Top-level metadata fields copied onto every row (when present).
META_KEYS = (
    "schema_version",
    "library",
    "library_id",
    "model",
    "provider",
    "judge_model",
    "judge_provider",
    "generated_at",
    "evaluated_at",
)


def _jsonify(value):
    """Make a value tabular-friendly: encode nested containers as JSON strings."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def flatten(kind: str, data: dict) -> list[dict]:
    """Flatten an artifact into a list of tabular rows.

    Top-level metadata is attached to each row; nested dict/list fields are
    JSON-encoded so the result has a flat, stable column set.
    """
    if kind not in RECORD_KEY:
        raise KeyError(f"Unknown artifact kind: {kind!r}")
    record_key = RECORD_KEY[kind]
    if record_key not in data:
        raise KeyError(f"Artifact is missing the required {record_key!r} array for kind {kind!r}")
    records = data[record_key]
    if not isinstance(records, list):
        raise TypeError(f"Artifact {record_key!r} must be a list, got {type(records).__name__}")
    meta = {k: data[k] for k in META_KEYS if k in data}
    rows: list[dict] = []
    for rec in records:
        row = dict(meta)
        if isinstance(rec, dict):
            for k, v in rec.items():
                row[k] = _jsonify(v)
        else:
            row["value"] = _jsonify(rec)
        if kind == "task_runs":
            _lift_task_run_metrics(rec, row)
        rows.append(row)
    return rows


def _lift_task_run_metrics(rec: object, row: dict) -> None:
    """Promote selected ``metrics`` fields to top-level columns for analysis.

    The full ``metrics``/``operations`` blocks stay as JSON-encoded columns
    (set by the generic flatten); this only adds flat, typed columns for the
    telemetry an analyst actually pivots on.
    """
    if not isinstance(rec, dict):
        return
    metrics = rec.get("metrics") or {}
    for col in _TASK_RUN_METRIC_COLS:
        if col in metrics:
            row[f"metric_{col}"] = _jsonify(metrics[col])


def to_jsonl(rows: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def to_parquet(rows: list[dict], path: Path) -> None:
    from datasets import Dataset

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Dataset.from_list(rows).to_parquet(str(path))


def to_hf(rows: list[dict], out_dir: Path) -> None:
    from datasets import Dataset

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    Dataset.from_list(rows).save_to_disk(str(out_dir))


def dataset_card(kind: str, source: Path, n_rows: int, columns: list[str], *,
                 source_version: str | None = None) -> str:
    """The dataset README. Rows are in the current schema version's shape; ``source_version``
    is the version the input file was (default: the current one), e.g. ``questions.v1`` for a set
    upgraded on export, or ``"unknown"`` for an unstamped file exported without validation."""
    version = SCHEMA_VERSIONS.get(kind, "unknown")
    source_version = source_version or version
    source_line = ("" if source_version == version
                   else f"- **Source schema version:** {source_version}"
                        f" (records upgraded to {version} on export)\n")
    cols = "\n".join(f"- `{c}`" for c in columns)
    return (
        f"# Dokimi {kind} dataset\n\n"
        f"Exported from `{Path(source).name}` ({source_version}).\n\n"
        f"- **Kind:** {kind}\n"
        f"- **Rows:** {n_rows}\n"
        f"- **Schema version:** {version}\n"
        f"{source_line}\n"
        f"## Columns\n\n{cols}\n\n"
        f"## Provenance\n\n"
        f"Generated by `dokimi dataset export`. Nested fields are "
        f"JSON-encoded strings. Pin a revision when sharing.\n"
    )


def export_artifact(
    kind: str,
    input_path: Path,
    out_dir: Path,
    fmt: str = "jsonl",
    *,
    validate: bool = True,
) -> Path:
    """Export an artifact to ``out_dir`` in ``fmt`` (jsonl|parquet|hf) + a card.

    Returns the path to the primary data file/dir. A ``questions`` input may be questions.v1 or
    questions.v2; its records are exported in the v2 shape (v1 ``text`` becomes ``question``),
    so sets exported from either version share one column set.
    """
    source_version: str | None = None
    if kind == "questions":
        from dokimi.questions.artifact import load_questions

        loaded = load_questions(input_path, validate=validate)
        data, source_version = loaded.artifact, loaded.source_version or "unknown"
    else:
        data = load_artifact(kind, input_path, validate=validate)
    rows = flatten(kind, data)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "jsonl":
        primary = out_dir / f"{kind}.jsonl"
        to_jsonl(rows, primary)
    elif fmt == "parquet":
        primary = out_dir / f"{kind}.parquet"
        to_parquet(rows, primary)
    elif fmt == "hf":
        primary = out_dir / "hf"
        to_hf(rows, primary)
    else:
        raise ValueError(f"Unknown format: {fmt!r} (expected jsonl|parquet|hf)")

    columns = sorted({c for row in rows for c in row}) if rows else []
    (out_dir / "README.md").write_text(
        dataset_card(kind, Path(input_path), len(rows), columns, source_version=source_version),
        encoding="utf-8",
    )
    return primary
