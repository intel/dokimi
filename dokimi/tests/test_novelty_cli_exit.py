# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exit codes of `novelty` through `cli.main`: bad input exits 2, a model failure exits 1."""

from __future__ import annotations

import json
import sys

import pytest

from dokimi import cli
from dokimi.commands import novelty as novelty_cmd


def test_extraction_failure_exits_1_not_2(tmp_path, monkeypatch, capsys):
    # A model failure is a runtime failure (exit 1, as before), not bad input (exit 2).
    from dokimi.novelty import atoms as atoms_mod

    def failing_extract(*args, **kwargs):
        raise atoms_mod.ExtractionError("service unavailable")

    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\nDo not enable the legacy driver.\n",
                     encoding="utf-8")
    monkeypatch.setattr(atoms_mod, "extract_atoms", failing_extract)
    monkeypatch.setattr(novelty_cmd, "_client", lambda *a, **k: type("C", (), {"model": "m"})())
    monkeypatch.setattr(sys, "argv", ["dokimi", "novelty", "atoms", "--artifact", f"skill:{skill}",
                                      "--out", str(tmp_path / "atoms.json")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    # SystemExit with a message exits 1; cli.main's exit 2 applies only to InputError.
    assert exc.value.code == "service unavailable"


def test_items_bad_probes_file_exits_2_through_cli_main(tmp_path, monkeypatch, capsys):
    # The command raises InputError; cli.main prints it and exits 2, the code for bad input.
    path = tmp_path / "novelty.json"
    path.write_text(json.dumps({"model": "m", "paired": {}}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["dokimi", "novelty", "items", "--probes", str(path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "no probe runs" in capsys.readouterr().err
