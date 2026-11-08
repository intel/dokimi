# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""atheris harness over fuzz/targets.py (``targets.run`` decodes the input).

    python fuzz/fuzz_parsers.py fuzz/corpus -max_total_time=60

Any libFuzzer flag works. A failing input is written to ``crash-<sha1>``;
replay it with ``python fuzz/fuzz_parsers.py crash-<sha1>``.
"""
import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent))

with atheris.instrument_imports(include=["dokimi", "targets"]):
    from targets import run


def main() -> None:
    atheris.Setup(sys.argv, run)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
