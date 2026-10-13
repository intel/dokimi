# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reproducibility manifest for a benchmark run.

Captures enough provenance to replay a run: the git commit, Python/platform,
resolved versions of key dependencies, hashes of the spec and the selected
document set, and any models/providers used. Emitted alongside artifacts so a
result can be tied back to exactly what produced it.
"""

from __future__ import annotations

import hashlib
import platform
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from dokimi import process

# Direct dependencies whose versions materially affect results.
TRACKED_DEPS = (
    "litellm", "openai", "anthropic", "ragas", "datasets",
    "jsonschema", "numpy", "PyYAML", "httpx",
)


def _git(*args: str) -> str | None:
    try:
        out = process.git(*args, timeout=5)
    except OSError:
        return None
    return out.stdout.strip() if out.ok else None


def file_sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def hash_files(paths) -> dict:
    """Return a combined, order-independent hash over a set of files."""
    digest = hashlib.sha256()
    count = 0
    for p in sorted(str(x) for x in paths):
        sha = file_sha256(Path(p))
        if sha is not None:
            digest.update(sha.encode())
            count += 1
    return {"count": count, "sha256": digest.hexdigest() if count else None}


def dep_versions(names=TRACKED_DEPS) -> dict:
    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def build_run_manifest(
    *,
    spec_path: Path | None = None,
    doc_paths=None,
    models: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """Assemble a provenance manifest for the current run."""
    manifest: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": {
            "sha": _git("rev-parse", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": dep_versions(),
    }
    if spec_path is not None:
        manifest["spec"] = {"path": str(spec_path), "sha256": file_sha256(Path(spec_path))}
    if doc_paths is not None:
        manifest["docs"] = hash_files(doc_paths)
    if models:
        manifest["models"] = models
    if extra:
        manifest["extra"] = extra
    return manifest


def write_run_manifest(manifest: dict, path: Path) -> None:
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
