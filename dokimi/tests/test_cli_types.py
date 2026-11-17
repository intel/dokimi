# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What each flag with a shared argparse ``type=`` validator takes and refuses (#229 P3-2).

Every row goes through the real parser: an accepted value becomes the number it names, a refused
one exits 2 with the line argparse prints for that flag.
"""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser

# The arguments each command needs before the flag under test.
_BEFORE = {
    "--rare-max": ["trace", "prefilter", "AGENTS.md", "s.jsonl"],
    "--in-use-rate": ["trace", "prefilter", "AGENTS.md", "s.jsonl"],
    "--typed-max-len": ["eval", "judge", "validate", "--answers", "a.json"],
    "--verbatim-words": ["eval", "leak", "skill", "q.json"],
    "--min-fraction": ["eval", "leak", "skill", "q.json"],
    "--max-cost-usd": ["tools", "preference", "--groups", "g.json"],
    "skill-coverage --max-usd": ["eval", "skill-coverage", "SKILL.md", "evals.json"],
    "novelty --max-usd": ["skill", "novelty", "analyze", "--artifact", "skill:x"],
}

ACCEPTED = [
    ("--rare-max", "0", 0),
    ("--rare-max", "7", 7),
    ("--typed-max-len", "0", 0),
    ("--typed-max-len", "512", 512),
    ("--verbatim-words", "1", 1),
    ("--min-fraction", "1", 1.0),
    ("--min-fraction", "0.25", 0.25),
    ("--in-use-rate", "0", 0.0),
    ("--in-use-rate", "1", 1.0),
    ("--max-cost-usd", "0.5", 0.5),
    ("--max-cost-usd", "1e3", 1000.0),
    ("skill-coverage --max-usd", "1e3", 1000.0),
    ("novelty --max-usd", "0.01", 0.01),
]

REFUSED = [
    ("--rare-max", "-1", "must be >= 0, got -1"),
    ("--rare-max", "abc", "expected an integer, got 'abc'"),
    ("--rare-max", "1.5", "expected an integer, got '1.5'"),
    ("--rare-max", "1e3", "expected an integer, got '1e3'"),
    ("--typed-max-len", "-1", "must be >= 0, got -1"),
    ("--typed-max-len", "-01", "must be >= 0, got -1"),
    ("--typed-max-len", "abc", "expected an integer, got 'abc'"),
    ("--typed-max-len", "1e3", "expected an integer, got '1e3'"),
    ("--verbatim-words", "0", "must be >= 1, got 0"),
    ("--verbatim-words", "-2", "must be >= 1, got -2"),
    ("--verbatim-words", "many", "expected an integer, got 'many'"),
    ("--min-fraction", "0", "expected a number in (0, 1], got '0'"),
    ("--min-fraction", "1.5", "expected a number in (0, 1], got '1.5'"),
    ("--min-fraction", "nan", "expected a number in (0, 1], got 'nan'"),
    ("--min-fraction", "half", "expected a number in (0, 1], got 'half'"),
    ("--in-use-rate", "-0.1", "must be a number from 0 to 1, not -0.1"),
    ("--in-use-rate", "1.01", "must be a number from 0 to 1, not 1.01"),
    ("--in-use-rate", "nan", "must be a number from 0 to 1, not nan"),
    ("--in-use-rate", "abc", "must be a number from 0 to 1, not abc"),
    ("--max-cost-usd", "0", "must be a positive number of USD, got '0'"),
    ("--max-cost-usd", "-1", "must be a positive number of USD, got '-1'"),
    ("--max-cost-usd", "nan", "must be a positive number of USD, got 'nan'"),
    ("--max-cost-usd", "inf", "must be a positive number of USD, got 'inf'"),
    ("--max-cost-usd", "abc", "must be a positive number of USD, got 'abc'"),
    ("skill-coverage --max-usd", "0", "must be a positive number of USD, got '0'"),
    ("skill-coverage --max-usd", "inf", "must be a positive number of USD, got 'inf'"),
    ("skill-coverage --max-usd", "abc", "must be a positive number of USD, got 'abc'"),
    ("novelty --max-usd", "-1", "must be a positive number of USD, got '-1'"),
    ("novelty --max-usd", "nan", "must be a positive number of USD, got 'nan'"),
    ("novelty --max-usd", "abc", "must be a positive number of USD, got 'abc'"),
]


def _parse(key: str, value: str):
    flag = key.split()[-1]
    return flag, build_parser().parse_args([*_BEFORE[key], f"{flag}={value}"])


@pytest.mark.parametrize(("key", "value", "expected"), ACCEPTED)
def test_a_flag_takes_the_number_its_value_names(key, value, expected):
    flag, ns = _parse(key, value)
    parsed = getattr(ns, flag.lstrip("-").replace("-", "_"))
    assert parsed == expected and type(parsed) is type(expected)


@pytest.mark.parametrize(("key", "value", "message"), REFUSED)
def test_a_flag_refuses_a_value_with_exit_2_and_its_message(key, value, message, capsys):
    with pytest.raises(SystemExit) as exc:
        _parse(key, value)
    assert exc.value.code == 2
    assert f"error: argument {key.split()[-1]}: {message}\n" in capsys.readouterr().err
