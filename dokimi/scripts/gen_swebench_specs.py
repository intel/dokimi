# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Regenerate ``dokimi/harnesses/swebench_specs.json`` from the ``swebench`` package.

dokimi does not depend on ``swebench`` (it pulls in modal, datasets, docker and
pre-commit); the loader reads this table instead. Run it from a throwaway env:

    uv run --no-project --with swebench==4.1.0 python scripts/gen_swebench_specs.py
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "dokimi" / "harnesses" / "swebench_specs.json"
_KEYS = ("test_cmd", "eval_commands", "install")


def main() -> int:
    specs_py = importlib.import_module("swebench.harness.constants.python").MAP_REPO_VERSION_TO_SPECS_PY
    parsers = importlib.import_module("swebench.harness.log_parsers").MAP_REPO_TO_PARSER
    repos = {}
    for repo, versions in sorted(specs_py.items()):
        parser = parsers.get(repo)
        if parser is None:  # no log parser upstream either: the repo cannot be graded
            continue
        repos[repo] = {
            "parser": parser.__name__.removeprefix("parse_log_"),
            "versions": {v: {k: s[k] for k in _KEYS if s.get(k)} for v, s in sorted(versions.items())},
        }
    table = {"source": f"swebench=={importlib.metadata.version('swebench')}", "repos": repos}
    OUT.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(repos)} repos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
