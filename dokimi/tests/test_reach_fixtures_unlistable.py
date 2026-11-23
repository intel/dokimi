# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1105: ``fixtures_digest`` raises for a directory it cannot list, not a digest of a smaller fixture tree.

The digest is the matrix's ``fixtures_digest`` (docs/reach-matrix.md): matrices with different digests are not merged.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from dokimi.repos import conformance as cf


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "t"
    (tree / "case" / "sub").mkdir(parents=True)
    (tree / "case" / "AGENTS.md").write_text("x\n", encoding="utf-8")
    (tree / "case" / "sub" / "CLAUDE.md").write_text("y\n", encoding="utf-8")
    return tree


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root lists a mode 111 directory")
@pytest.mark.parametrize("parts", [(), ("case",), ("case", "sub")])  # the fixtures root, a case, a directory below a listable one
def test_a_directory_that_cannot_be_listed_raises(tmp_path: Path, parts: tuple[str, ...]):
    tree = _tree(tmp_path)
    unlistable = tree.joinpath(*parts)
    complete = cf.fixtures_digest(tree)
    unlistable.chmod(0o111)
    try:
        with pytest.raises(PermissionError, match=unlistable.name):
            cf.fixtures_digest(tree)
    finally:
        unlistable.chmod(0o700)
    assert cf.fixtures_digest(tree) == complete


def test_a_link_to_a_directory_and_a_dangling_link_are_hashed_by_their_target(tmp_path: Path):
    tree = _tree(tmp_path)
    try:
        os.symlink("case", tree / "dirlink", target_is_directory=True)
        os.symlink("nowhere", tree / "dangling")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    parts = (b"case/AGENTS.md", b"Fx\n", b"case/sub/CLAUDE.md", b"Fy\n",  # sorted by relative path: the link to a directory is not entered
             b"dangling", b"Lnowhere", b"dirlink", b"Lcase")
    expected = hashlib.sha256(b"".join(len(part).to_bytes(8, "big") + part for part in parts)).hexdigest()
    assert cf.fixtures_digest(tree) == expected
