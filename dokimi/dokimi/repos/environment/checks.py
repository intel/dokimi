# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Ways to build and check a change that the tree offers, per ecosystem.

Each record says how it is known. ``declared``: the repository names it (a script, a make
target, a config section, a pre-commit hook). ``toolchain``: the ecosystem's tool provides the
command whether or not the repository has anything for it to run (``go test`` with a ``go.mod``),
so it is a place to start, not evidence of tests. Nothing here runs a command.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from typing import Any

import yaml

from dokimi.repos.l0.adapters import _MAKE_RULE
from dokimi.repos.l0.core import Scan

from .files import is_generated

KINDS = ("build", "test", "lint", "typecheck")

_NODE_LINT_NAME = re.compile(r"(^|[:/_-])lints?([:/_-]|$)", re.I)
_NODE_LINT_TOOL = re.compile(r"\b(?:eslint|prettier|xo|biome|standard|tslint|oxlint|rome)\b(?!-)")
_NODE_TYPE_NAME = re.compile(r"(^|[:/_-])(?:type-?check|check[:_-]?types?|typings?)([:/_-]|$)", re.I)
_NODE_TYPE_TOOL = re.compile(r"\btsc\b[^\n]{0,256}?--noemit\b|\btsd\b|\bvue-tsc\b|\bsvelte-check\b|\battw\b", re.I)
_NODE_INSTALL = re.compile(r"\b(npm (ci|install|i)|yarn (install|add)|pnpm (install|i|add)|bun (install|i|add))\b")
_SEGMENTS = re.compile(r"&&|\|\||[;\n]")
_INI_SECTION = re.compile(r"^\[+([^\]#;]+)\]+", re.M)
_JVM_LINT = r"checkstyle|pmd|spotbugs|spotless|ktlint|detekt"
_TEST_DIR = re.compile(r"(^|/)(tests?|unit|unittests?)/", re.I)
# pre-commit hook ids that lint or type-check; anything else in the config still counts as lint
_PRECOMMIT_TYPE = re.compile(r"mypy|pyright|pytype|tsc|typecheck", re.I)


@dataclass(frozen=True)
class Check:
    ecosystem: str
    kind: str  # build | test | lint | typecheck
    basis: str  # declared | toolchain
    evidence: str  # the file that says so
    name: str | None = None  # script, target, section or hook id
    directory: str = "."  # the project root it applies to; `.` for a tree-wide build system

    def to_dict(self) -> dict[str, Any]:
        return {"ecosystem": self.ecosystem, "kind": self.kind, "basis": self.basis, "directory": self.directory,
                "evidence": self.evidence, "name": self.name}


def _at(root: str, name: str) -> str:
    return name if root == "." else f"{root}/{name}"


def package_scripts(scan: Scan, path: str = "package.json") -> dict[str, str]:
    """``scripts`` of a ``package.json`` (empty when it is missing or not an object)."""
    try:
        doc = json.loads((scan.text(path) or "{}").lstrip("\ufeff"))
    except (json.JSONDecodeError, RecursionError, ValueError):
        return {}
    scripts = doc.get("scripts") if isinstance(doc, dict) else None
    return {k: v for k, v in scripts.items() if isinstance(v, str)} if isinstance(scripts, dict) else {}


_NPM_PLACEHOLDER = re.compile(r"^\s*echo\b[^\n]*&&\s*exit\s+[1-9]\s*$", re.I)  # what `npm init` writes


def _node(scan: Scan, root: str) -> list[Check]:
    pkg = _at(root, "package.json")
    if pkg not in scan.file_set:
        return []
    out = []
    for name, body in sorted(package_scripts(scan, pkg).items()):
        run = " ; ".join(s for s in _SEGMENTS.split(body) if not _NODE_INSTALL.search(s))
        if name == "build":
            out.append(Check("node", "build", "declared", pkg, name, root))
        if name in ("test", "test:unit", "test:ci") and not _NPM_PLACEHOLDER.match(body):
            out.append(Check("node", "test", "declared", pkg, name, root))
        if _NODE_LINT_NAME.search(name) or _NODE_LINT_TOOL.search(run):
            out.append(Check("node", "lint", "declared", pkg, name, root))
        if _NODE_TYPE_NAME.search(name) or _NODE_TYPE_TOOL.search(run):
            out.append(Check("node", "typecheck", "declared", pkg, name, root))
    return out


def _make(scan: Scan, root: str) -> list[Check]:
    mk = next((_at(root, m) for m in ("GNUmakefile", "makefile", "Makefile") if _at(root, m) in scan.file_set), None)
    if mk is None:
        return []
    targets = {t.lower() for m in _MAKE_RULE.finditer(scan.text(mk)) for t in m.group(1).split()
               if "$" not in t and "%" not in t}
    names = {"build": ("build", "all", "compile"), "test": ("test", "tests", "check"),
             "lint": ("lint", "fmt", "format"), "typecheck": ("typecheck", "type-check", "types")}
    return [Check("make", kind, "declared", mk, t, root) for kind, ts in names.items() for t in ts if t in targets]


def _cmake(scan: Scan) -> list[Check]:
    if "CMakeLists.txt" not in scan.file_set and "CMakePresets.json" not in scan.file_set:
        return []
    evidence = "CMakeLists.txt" if "CMakeLists.txt" in scan.file_set else "CMakePresets.json"
    if is_generated(evidence):
        return []
    out = [Check("cmake", "build", "toolchain", evidence)]
    sources = "\n".join(scan.text(f) for f in scan.files if posixpath.basename(f) == "CMakeLists.txt" and not is_generated(f))
    if re.search(r"\b(?:enable_testing|add_test|ctest_|gtest_discover_tests|catch_discover_tests)\s*\(", sources,
                 re.I):
        out.append(Check("cmake", "test", "declared", evidence, "ctest"))
    return out


def _bazel(scan: Scan) -> list[Check]:
    marker = next((m for m in ("MODULE.bazel", "WORKSPACE.bazel", "WORKSPACE") if m in scan.file_set), None)
    builds = [f for f in scan.files if posixpath.basename(f) in ("BUILD", "BUILD.bazel") and not is_generated(f)]
    if marker is None and not builds:
        return []
    evidence = marker or builds[0]
    if is_generated(evidence):
        return []
    out = [Check("bazel", "build", "toolchain", evidence)]
    scan.preload(builds)
    test_rule = next((f for f in builds if re.search(r"\b\w+_test\s*\(", scan.text(f))), None)
    if test_rule:
        out.append(Check("bazel", "test", "declared", test_rule, "*_test rule"))
    return out


def _toolchain(scan: Scan, root: str, ecosystem: str, marker: str, kinds: tuple[str, ...]) -> list[Check]:
    marker = _at(root, marker)
    return [Check(ecosystem, k, "toolchain", marker, None, root) for k in kinds] if marker in scan.file_set else []


def _dotnet(scan: Scan) -> list[Check]:
    marker = next((f for f in scan.files if re.search(r"\.(sln|slnx|csproj|fsproj|vbproj)$", f, re.I) and not is_generated(f)), None)
    marker = marker or ("global.json" if "global.json" in scan.file_set and not is_generated("global.json") else None)
    return [Check("dotnet", k, "toolchain", marker) for k in ("build", "test")] if marker else []


def _autotools(scan: Scan) -> list[Check]:
    marker: str | None = next((m for m in ("configure.ac", "configure.in", "autogen.sh") if m in scan.file_set), None)
    marker = marker or next((f for f in scan.files if posixpath.basename(f) == "Makefile.am"), None)
    if marker is None:
        return []
    out = [Check("autotools", "build", "toolchain", marker)]
    am_files = [f for f in scan.files if posixpath.basename(f) == "Makefile.am"]
    if any(re.search(r"\b(?:TESTS|check_PROGRAMS|check_SCRIPTS|check_LTLIBRARIES)\b", scan.text(f)) for f in am_files):
        out.append(Check("autotools", "test", "declared", am_files[0], "make check"))
    return out


def _python(scan: Scan, root: str) -> list[Check]:
    if not any(_at(root, m) in scan.file_set for m in ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile")):
        return []
    pyproject, setup_cfg = _at(root, "pyproject.toml"), _at(root, "setup.cfg")
    sections = {(s.strip(), m) for m in (pyproject, setup_cfg) for s in _INI_SECTION.findall(scan.text(m))}
    out = []
    if any(s == "build-system" for s, _ in sections):
        out.append(Check("python", "build", "declared", pyproject, "build-system", root))
    elif _at(root, "setup.py") in scan.file_set:
        out.append(Check("python", "build", "declared", _at(root, "setup.py"), None, root))
    for marker, kind, name in (("tox.ini", "test", None), ("pytest.ini", "test", None),
                               (".flake8", "lint", None), ("ruff.toml", "lint", None), (".ruff.toml", "lint", None),
                               ("mypy.ini", "typecheck", None), ("pyrightconfig.json", "typecheck", None)):
        if _at(root, marker) in scan.file_set:
            out.append(Check("python", kind, "declared", _at(root, marker), name, root))
    for section, file in sorted(sections):
        if section in ("tool.pytest.ini_options", "tool:pytest"):
            out.append(Check("python", "test", "declared", file, section, root))
        elif section in ("tool.black", "tool.isort", "tool.ruff", "tool.pylint", "flake8", "isort", "tool:isort"):
            out.append(Check("python", "lint", "declared", file, section, root))
        elif section in ("tool.mypy", "tool.pyright") or section.startswith("mypy"):
            out.append(Check("python", "typecheck", "declared", file, section, root))
    conftest = next((f for f in scan.files if posixpath.basename(f) == "conftest.py"
                     and (root == "." or f.startswith(root + "/"))), None)
    if conftest and not any(c.kind == "test" for c in out):
        out.append(Check("python", "test", "declared", conftest, None, root))
    return out


def _gradle(scan: Scan) -> list[Check]:
    files = [f for f in scan.files if re.search(r"(^|/)(build|settings)\.gradle(\.kts)?$", f, re.I)]
    if not files and "gradlew" not in scan.file_set:
        return []
    evidence = files[0] if files else "gradlew"
    out = [Check("gradle", k, "toolchain", evidence) for k in ("build", "test")]
    for f in files:
        text = scan.text(f)
        blocks = " ".join(re.findall(r"\bplugins\s*\{([\s\S]*?)\}", text, re.I))
        applied = re.search(rf"\bapply\s+plugin:\s*[\"'][^\"']*\b(?:{_JVM_LINT})\b", text, re.I)
        if applied or re.search(rf"\b(?:{_JVM_LINT})\b", blocks, re.I):
            out.append(Check("gradle", "lint", "declared", f))
            break
    return out


def _maven(scan: Scan) -> list[Check]:
    poms = [f for f in scan.files if posixpath.basename(f) == "pom.xml"]
    if not poms:
        return []
    out = [Check("maven", k, "toolchain", poms[0]) for k in ("build", "test")]
    lint = next((f for f in poms if re.search(rf"<artifactId>\s*[^<]*\b(?:{_JVM_LINT})\b[^<]*plugin[^<]*</artifactId>",
                                              scan.text(f), re.I)), None)
    if lint:
        out.append(Check("maven", "lint", "declared", lint))
    return out


def _pre_commit(scan: Scan) -> list[Check]:
    if ".pre-commit-config.yaml" not in scan.file_set:
        return []
    try:
        cfg = yaml.safe_load(scan.text(".pre-commit-config.yaml")) or {}
    except (yaml.YAMLError, ValueError, RecursionError):
        return []
    out = []
    repos = cfg.get("repos") if isinstance(cfg, dict) else None
    for repo in repos if isinstance(repos, list) else []:
        hooks = repo.get("hooks") if isinstance(repo, dict) else None
        for hook in hooks if isinstance(hooks, list) else []:
            hid = hook.get("id") if isinstance(hook, dict) else None
            if isinstance(hid, str):
                kind = "typecheck" if _PRECOMMIT_TYPE.search(hid) else "lint"
                out.append(Check("pre-commit", kind, "declared", ".pre-commit-config.yaml", hid))
    return out


ECOSYSTEMS = ("node", "make", "cmake", "bazel", "go", "rust", "python", "gradle", "maven", "dotnet", "autotools",
              "pre-commit")


# A directory holding one of these is a project of its own: its scripts and config apply there.
PROJECT_MARKERS = frozenset({"package.json", "Makefile", "makefile", "GNUmakefile", "pyproject.toml", "setup.py",
                             "setup.cfg", "Pipfile", "go.mod", "Cargo.toml"})


def project_roots(scan: Scan) -> list[str]:
    """The root and every directory below it with a project manifest, outside generated and test trees."""
    roots = {"."}
    for f in scan.files:
        if posixpath.basename(f) in PROJECT_MARKERS and "/" in f and not is_generated(f) and not _TEST_DIR.search(f):
            roots.add(posixpath.dirname(f))
    return sorted(roots, key=lambda r: (r != ".", r))


def detect_checks(scan: Scan) -> list[Check]:
    """Every check surface found, per project root for per-project ecosystems, in ecosystem order."""
    found: list[Check] = []
    for root in project_roots(scan):
        found += (_node(scan, root) + _make(scan, root)
                  + _toolchain(scan, root, "go", "go.mod", ("build", "test", "lint"))
                  + _toolchain(scan, root, "rust", "Cargo.toml", ("build", "test", "lint", "typecheck"))
                  + _python(scan, root))
    # these read the whole tree: one build graph, wherever its files are
    found += _cmake(scan) + _bazel(scan) + _gradle(scan) + _maven(scan) + _dotnet(scan) + _autotools(scan)
    found += _pre_commit(scan)
    return sorted(dict.fromkeys(found), key=lambda c: (c.directory != ".", c.directory, ECOSYSTEMS.index(c.ecosystem),
                                                        KINDS.index(c.kind), c.evidence, c.name or ""))
