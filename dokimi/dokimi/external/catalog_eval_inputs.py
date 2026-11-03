# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Local data input checks and byte identities for the static catalog adapter."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from dokimi.skills.loader import VENDORED_DIR_NAMES


class UnsafePath(ValueError):
    """A catalog path cannot be inspected without following a link or escaping."""


def relative_path(value: object, *, literal_backslash: bool = False) -> str:
    """Accept only normalized, catalog-relative POSIX paths; git-listed POSIX names may contain a backslash."""
    if not isinstance(value, str) or not value or "\x00" in value or ("\\" in value and not literal_backslash):
        raise UnsafePath(f"unsafe relative path: {value!r}")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.drive or ".." in posix.parts:
        raise UnsafePath(f"unsafe relative path: {value!r}")
    return posix.as_posix()


def redirected(path: Path) -> bool:
    """A symlink, or a Windows junction/reparse point, which ``is_symlink`` does not report."""
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False


def checked_root(root: Path) -> Path:
    """Refuse a linked root; linked ancestors resolve once and everything below is checked."""
    candidate = Path(root).absolute()
    try:
        if redirected(candidate):
            raise ValueError(f"catalog root is a symlink: {candidate}")
        if not candidate.is_dir():
            raise ValueError(f"catalog root is not a directory: {candidate}")
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot inspect catalog root {candidate}: {exc}") from exc


def checked_path(root: Path, path: Path) -> None:
    """Inspect every ancestor without reading a symlink's target."""
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise UnsafePath(f"path escapes catalog root: {path}") from exc
    for part in (path, *path.parents):
        if part == root:
            break
        if redirected(part):
            raise UnsafePath(f"symlink is not a catalog input: {part.relative_to(root)}")


def finding(code: str, message: str, path: str = "", level: str = "error") -> dict[str, str]:
    return {"level": level, "code": code, "message": message, "path": path}


def digest(value: Any) -> str:
    """Hash canonical data, independent of checkout location or traversal order."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class Inputs:
    """Track all catalog bytes and reject mutations across existing loader reads."""

    def __init__(self, root: Path):
        self.root = root
        self.hashes: dict[str, str] = {}

    def read(self, path: Path) -> bytes:
        checked_path(self.root, path)
        try:
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode) or not mode & 0o444:
                raise OSError("input is not a readable regular file")
            content = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        relative, current = path.relative_to(self.root).as_posix(), hashlib.sha256(content).hexdigest()
        if self.hashes.setdefault(relative, current) != current:
            raise ValueError(f"catalog input changed while checking: {relative}")
        return content

    def text(self, path: Path) -> str:
        try:
            return self.read(path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"input is not UTF-8: {path}: {exc}") from exc

    def files(self, path: Path) -> list[Path]:
        """Walk without following links; check entries before asking their types."""
        checked_path(self.root, path)
        if not path.exists():
            return []
        if path.is_file():
            return [path]
        result: list[Path] = []
        pending = [path]
        while pending:
            current = pending.pop()
            if current.is_file():
                result.append(current)
                continue
            try:
                children = sorted(current.iterdir())
            except OSError as exc:
                raise ValueError(f"cannot inspect {current}: {exc}") from exc
            for child in children:
                checked_path(self.root, child)
            pending.extend(reversed([c for c in children if c.name not in VENDORED_DIR_NAMES or not c.is_dir()]))
        return result

    def bundle(self, path: Path) -> str:
        """Bind every package resource or task verifier/oracle/environment byte."""
        files = self.files(path)
        entries = []
        for file in files:
            self.read(file)
            entries.append([file.relative_to(path).as_posix(),
                            self.hashes[file.relative_to(self.root).as_posix()]])
        return digest(entries)

    def verify(self) -> None:
        """A loader must have evaluated the same bytes this receipt identifies."""
        for relative in list(self.hashes):
            self.read(self.root / relative)
