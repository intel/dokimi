# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Every input loader fails the same way on a bad file: an InputError that names the file.

The rows are the loaders, one load-or-fail entry point per artifact kind; the columns are the
ways an input file goes bad. A loader never exits the process: cli.main maps the error to exit 2.
"""

from __future__ import annotations

import functools

import pytest

from dokimi.artifacts import SCHEMA_VERSIONS, load_artifact
from dokimi.novelty.atoms import load_atoms_record
from dokimi.records import load as load_records
from dokimi.report.model_compare import load_run
from dokimi.utils import InputError

MISSING, NOT_UTF8, BAD_JSON, NOT_AN_OBJECT, WRONG_SCHEMA = (
    "missing", "not_utf8", "bad_json", "not_an_object", "wrong_schema")

LOADERS = {
    **{f"artifact:{kind}": functools.partial(load_artifact, kind) for kind in sorted(SCHEMA_VERSIONS)},
    "atoms": load_atoms_record,
    "judged-run": load_run,
    "answers-records": lambda path: load_records(path, section="answers"),
}

# An object of the loader's own kind that fails its check; other kinds get the generic one.
_WRONG_SCHEMA = {
    "atoms": '{"atoms": 5, "spans": []}',
    "judged-run": '{"answers": 5}',
    "answers-records": '{"answers": [5]}',
}
_GENERIC_WRONG_SCHEMA = '{"schema_version": "not.a.real.version", "unexpected": 1}'


def _write(path, loader, mode):
    if mode == MISSING:
        return
    if mode == NOT_UTF8:
        path.write_bytes(b'\xff\xfe{"a": 1}')
    elif mode == BAD_JSON:
        path.write_text("{not json", encoding="utf-8")
    elif mode == NOT_AN_OBJECT:
        path.write_text("[1, 2]", encoding="utf-8")
    else:
        path.write_text(_WRONG_SCHEMA.get(loader, _GENERIC_WRONG_SCHEMA), encoding="utf-8")


@pytest.mark.parametrize("mode", [MISSING, NOT_UTF8, BAD_JSON, NOT_AN_OBJECT, WRONG_SCHEMA])
@pytest.mark.parametrize("loader", list(LOADERS))
def test_a_bad_input_file_is_an_input_error_naming_it(tmp_path, loader, mode):
    # pytest.raises(InputError) fails on any other exception, a SystemExit included
    path = tmp_path / "input.json"
    _write(path, loader, mode)
    with pytest.raises(InputError) as exc:
        LOADERS[loader](path)
    assert str(path) in str(exc.value)
