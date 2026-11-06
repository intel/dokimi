# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""atheris harness over fuzz/targets.py: the first input byte picks the target.

    python fuzz/fuzz_parsers.py fuzz/corpus -max_total_time=60

Any libFuzzer flag works. A failing input is written to ``crash-<sha1>``;
replay it with ``python fuzz/fuzz_parsers.py crash-<sha1>``.
"""
import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent))

with atheris.instrument_imports(include=["dokimi", "targets"]):
    from targets import TARGETS

_NAMES = sorted(TARGETS)


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    target = TARGETS[_NAMES[fdp.ConsumeIntInRange(0, len(_NAMES) - 1)]]
    target(fdp.ConsumeUnicodeNoSurrogates(len(data)))


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
