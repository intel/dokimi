# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for model × harness evaluation cells."""

from __future__ import annotations

import json

import pytest

from dokimi.eval.cells import (
    Cell,
    CrossCellDeltaError,
    group_by_cell,
    is_known_harness,
    load_matrix_cells,
    safe_delta,
    same_cell,
    select_matrix_cell,
)


def test_cell_key_is_stable_and_distinguishing():
    a = Cell("gpt-4o", "single-shot")
    b = Cell("gpt-4o", "single-shot")
    c = Cell("gpt-4o", "agent")
    assert a.key == b.key
    assert a.key != c.key
    assert a == b and a != c


def test_cell_from_row_defaults_plugin_set_to_none():
    cell = Cell.from_row({"model": "m", "harness": "agent"})
    assert cell.plugin_set == "none"
    assert cell.key == "m|agent|none"


def test_is_known_harness():
    assert is_known_harness("single-shot")
    assert is_known_harness("agent")
    assert is_known_harness("arms-runner")
    assert is_known_harness("openclaw-agent")
    assert is_known_harness("terminal-bench")
    assert is_known_harness("terminal-bench:terminus")  # concrete instance
    assert not is_known_harness("terminal-bench:")
    assert not is_known_harness("made-up-harness")


def test_same_cell_accepts_rows_and_cells():
    r1 = {"model": "m", "harness": "agent", "plugin_set": "none"}
    r2 = {"model": "m", "harness": "agent"}  # plugin_set defaulted
    assert same_cell(r1, r2)
    assert same_cell(Cell.from_row(r1), r2)
    assert not same_cell(r1, {"model": "m", "harness": "single-shot"})


def test_safe_delta_within_cell():
    treated = {"model": "m", "harness": "agent"}
    baseline = {"model": "m", "harness": "agent"}
    assert safe_delta(treated, baseline, 4.2, 3.8) == 0.4
    assert safe_delta(treated, baseline, None, 3.8) is None  # missing score


def test_safe_delta_refuses_across_models():
    treated = {"model": "model-b", "harness": "agent"}
    baseline = {"model": "model-a", "harness": "agent"}
    with pytest.raises(CrossCellDeltaError):
        safe_delta(treated, baseline, 4.2, 3.8)


def test_safe_delta_refuses_across_harnesses():
    treated = {"model": "m", "harness": "agent"}
    baseline = {"model": "m", "harness": "single-shot"}
    with pytest.raises(CrossCellDeltaError):
        safe_delta(treated, baseline, 4.2, 3.8)


def test_safe_delta_refuses_across_plugin_sets():
    treated = {"model": "m", "harness": "agent", "plugin_set": "caveman-terse"}
    baseline = {"model": "m", "harness": "agent", "plugin_set": "none"}
    with pytest.raises(CrossCellDeltaError):
        safe_delta(treated, baseline, 4.2, 3.8)


def test_group_by_cell():
    rows = [
        {"model": "a", "harness": "single-shot"},
        {"model": "a", "harness": "single-shot"},
        {"model": "a", "harness": "agent"},
        {"model": "b", "harness": "agent"},
    ]
    grouped = group_by_cell(rows)
    assert set(grouped.keys()) == {
        "a|single-shot|none",
        "a|agent|none",
        "b|agent|none",
    }
    assert len(grouped["a|single-shot|none"]) == 2


def test_load_matrix_cells_accepts_matrix_block(tmp_path):
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps({
            "matrix": {
                "cells": [
                    {
                        "id": "agent-caveman",
                        "model": "gpt-6.1-sol",
                        "provider": "openai",
                        "harness": "agent",
                        "plugins": ["plugin:caveman:lite"],
                    }
                ]
            }
        }),
        encoding="utf-8",
    )

    cells = load_matrix_cells(path)

    assert cells[0].name == "agent-caveman"
    assert cells[0].plugin_specs == ("plugin:caveman:lite",)
    assert select_matrix_cell(cells, "agent-caveman").harness == "agent"


def test_load_matrix_cells_accepts_object_plugin_descriptors(tmp_path):
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps({
            "matrix": {
                "cells": [
                    {
                        "id": "agent-caveman",
                        "model": "gpt-6.1-sol",
                        "provider": "openai",
                        "harness": "agent",
                        "plugins": [
                            {"id": "caveman", "ref": "plugin:caveman:lite", "config": {"level": "lite"}}
                        ],
                    }
                ]
            }
        }),
        encoding="utf-8",
    )

    cells = load_matrix_cells(path)

    assert cells[0].plugin_specs == ("plugin:caveman:lite",)


@pytest.mark.parametrize("payload", [{"cells": []}, {"matrix": {"cells": []}}, []])
def test_load_matrix_cells_rejects_empty_cell_list(tmp_path, payload):
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="at least one cell"):
        load_matrix_cells(path)


def test_load_matrix_cells_rejects_unknown_harness(tmp_path):
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps({
            "cells": [
                {
                    "name": "bad",
                    "model": "m",
                    "provider": "openai",
                    "harness": "made-up",
                }
            ]
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown harness"):
        load_matrix_cells(path)


def test_select_matrix_cell_reports_available_names(tmp_path):
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps([
            {"name": "base", "model": "m", "provider": "openai", "harness": "agent"}
        ]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="available cells: base"):
        select_matrix_cell(load_matrix_cells(path), "missing")
