# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Build-system adapters for L0: Bazel, make, CMake, pytest and pre-commit (contract §8).

Each adapter checks the commands and labels the guidance names against the
build files of the same snapshot. ``repo-eval.yaml`` ``build_systems`` picks
the adapters; without it they are detected from the files. A rule that is one
repository's policy (refusing ``//...``, for example) is not checked here; the
repository adds it through its own ``static`` entry point.
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import shlex
from typing import Callable, Iterator

import yaml

from .core import Scan, ancestors, dynamic
from .markdown import Fence

ADAPTERS = ("bazel", "make", "cmake", "pytest", "pre-commit")
_DETECT = {
    "bazel": ("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel", ".bazelrc"),
    "make": ("Makefile", "makefile", "GNUmakefile"),
    "cmake": ("CMakeLists.txt",),
    "pytest": ("pytest.ini", "conftest.py", "tox.ini"),
    "pre-commit": (".pre-commit-config.yaml",),
}
BAZEL_NATIVE = frozenset({
    "load", "package", "glob", "select", "cc_library", "cc_binary", "cc_test", "cc_import", "filegroup", "genrule",
    "exports_files", "alias", "config_setting", "test_suite", "py_library", "py_binary", "py_test", "sh_binary",
    "sh_test", "sh_library", "java_library", "java_binary", "java_test", "bazel_dep", "module", "use_extension",
    "use_repo", "http_archive", "git_override", "local_path_override", "archive_override", "register_toolchains",
    "workspace", "constraint_setting", "constraint_value", "platform", "toolchain", "licenses", "package_group",
})
# A label starts a word: `https://host/x` and `// comment` are not labels.
_LABEL_RE = re.compile(r"(?<![\w:/])(?:@@?[\w.~+-]*)?//[\w/.\-+]*(?::[\w.\-+/=,@~]+)?")
_LABEL_PARTS = re.compile(r"^(@@?[\w.~+-]*)?//([\w/.\-+]*?)(?::([\w.\-+/=,@~]+))?$")
STARLARK_BUILTINS = frozenset({
    "all", "any", "bool", "dict", "enumerate", "fail", "float", "getattr", "hasattr", "int", "len", "list", "max",
    "min", "print", "range", "repr", "reversed", "set", "sorted", "str", "tuple", "type", "zip", "depset", "struct",
    "Label", "existing_rule", "existing_rules", "package_name", "repository_name", "subpackages",
})
# One Starlark token: a comment, a string (either quote, a prefix, triple quotes), a word, a line end, or one other
# character.
_TOKEN = re.compile(r"""(?P<comment>\#[^\n]*)
    |(?P<str>[rRbB]{0,2}(?:\"\"\"(?:\\.|[^\\])*?\"\"\"|'''(?:\\.|[^\\])*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'))
    |(?P<id>[A-Za-z_]\w*)|(?P<nl>\n)|(?P<op>\S)""", re.X | re.S)
# Greedy, one line per rule: a lazy group beside ``[ \t]*`` is quadratic. Callers split the group.
_MAKE_RULE = re.compile(r"^([^\s:#=][^:#=\n]*)::?(?!=)", re.M)
_MAKE_OPT_ARG = frozenset({"-f", "-C", "-I", "-o", "-W", "--file", "--directory", "--makefile"})
_MAKE_OPT_NUM = frozenset({"-j", "-l", "--jobs", "--load-average"})  # take a number, which may be left out
_PYTEST_OPT_ARG = frozenset({"-k", "-m", "-p", "-c", "-o", "-W", "-r", "--rootdir", "--confcutdir", "--basetemp", "-n",
                             "--cov", "--cov-report", "--cov-config", "--log-level", "--import-mode", "--timeout",
                             "--deselect", "--ignore", "--junitxml", "--durations", "--maxfail", "--tb"})
# a dotted module name for `pytest --pyargs`; `test_x.py` is a file name, which pytest reads as a path
_MODULE_NAME = re.compile(r"^[A-Za-z_]\w*(?:\.(?!py$)[A-Za-z_]\w*)*$")

Handler = Callable[[Scan, str, int, list[str], str], None]


def detect(scan: Scan) -> list[str]:
    """Adapters whose marker files exist at the repository root (any ``conftest.py`` for pytest)."""
    found = [a for a in ADAPTERS if any(m in scan.file_set for m in _DETECT[a])]
    if "pytest" not in found and (
            "conftest.py" in scan.by_name or "[tool.pytest" in scan.text("pyproject.toml")
            or "[tool:pytest]" in scan.text("setup.cfg")):
        found.append("pytest")
    return [a for a in ADAPTERS if a in found]


# ---------------------------------------------------------------- bazel
def _bazel_facts(scan: Scan) -> dict:
    def make() -> dict:
        configs: set[str] = set()
        aliases: set[str] = set()
        todo, seen = [".bazelrc"], set()
        unread: dict[str, str] = {}  # an rc file that was not read: its configs are unknown, not absent
        while todo:
            rc = todo.pop()
            if rc in seen:
                continue
            seen.add(rc)
            text = scan.text(rc)
            if (why := scan.unread_reason(rc)):
                unread[rc] = why
            configs |= set(re.findall(r"^\s*\w+:([\w.-]+)", text, re.M))
            aliases |= set(re.findall(r"--flag_alias=([\w-]+)=", text))
            todo += [m.replace("%workspace%/", "") for m in re.findall(r"^\s*(?:try-)?import\s+(\S+)", text, re.M)]
        module = re.search(r'module\(\s*name\s*=\s*"([^"]+)"', scan.text("MODULE.bazel"))
        bzl = [f for f in scan.files if f.endswith(".bzl")]
        scan.preload(bzl)
        defs: dict[str, tuple[str, list[str]]] = {}
        twice: set[str] = set()  # defined in more than one .bzl file: which one a BUILD loads is not known here
        for f in bzl:
            for m in re.finditer(r"^def (\w+)\(", scan.text(f), re.M):
                params = _def_params(scan.text(f), m.end())
                if m.group(1) in defs:
                    twice.add(m.group(1))
                defs.setdefault(m.group(1), (f, params))
        return {"configs": configs, "aliases": aliases, "module": module.group(1) if module else None, "defs": defs,
                "twice": twice, "unread": unread}
    return scan.cached("bazel", make)


def _tokens(text: str, pos: int = 0) -> Iterator[tuple[str, str, bool]]:
    """``(kind, value, first_on_line)`` per token of Starlark ``text`` from ``pos``; comments and blanks are skipped.

    The value of a string is its body without the quotes; escapes stay as written.
    """
    first = True
    for m in _TOKEN.finditer(text, pos):
        kind = m.lastgroup or "op"
        if kind == "nl":
            first = True
        elif kind != "comment":
            val = m.group()
            if kind == "str":
                val = val.lstrip("rRbB")
                val = val[3:-3] if val[:3] in ('"""', "'''") else val[1:-1]
            yield kind, val, first
            first = False


def _def_params(text: str, pos: int) -> list[str]:
    """Parameter names of a ``def`` whose ``(`` ends just before ``pos``. Names are taken at the top level only, so
    a comment, a string or a bracketed default (``flags = {"a": 1, "b": 2}``) neither adds nor hides a parameter."""
    params: list[str] = []
    depth, stars, seen = 0, "", False
    for kind, val, _ in _tokens(text, pos):
        if kind == "op" and val in "([{":
            depth += 1
        elif kind == "op" and val in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and kind == "op" and val == ",":
            stars, seen = "", False
        elif depth == 0 and not seen and kind == "op" and val == "*":
            stars += "*"
        elif depth == 0 and not seen and kind == "id":
            params.append(stars + val)
            seen = True
    return params


def _literal(value: list[tuple[str, str]], list_ok: bool) -> bool:
    """``value`` is one string, or (``list_ok``) a list of strings."""
    if not list_ok:
        return len(value) == 1 and value[0][0] == "str"
    return bool(value) and value[0] == ("op", "[") and value[-1] == ("op", "]") and all(
        k == "str" or v in "[]," for k, v in value)


def _build_names(text: str) -> tuple[set[str], bool]:
    """The target and output names a BUILD file spells out, and whether it also makes names it does not spell out.

    Spelled out: ``name = "x"`` or ``out = "x"`` (either quote), ``outs = ["x.h", ...]``, and the files of a
    top-level ``exports_files([...])`` (every name of a plain list). Computed: any other value of those arguments
    (``"t_" + s``, ``"t_%s" % s``, a variable), ``exports_files(glob(...))`` and the like, a call at the start of a
    line, or in a comprehension that starts a line, to anything but a native rule or a builtin (a macro, which may
    generate targets). A ``%{...}`` outside a string, or inside a name, is a template placeholder, filled in on
    rendering; one inside another string (``"%{repo_root}/lib/x.a"``) makes no target.
    """
    names: set[str] = set()
    computed = False
    toks = list(_tokens(text))
    stack: list[str] = []
    comp = False  # inside a `[...]` or `{...}` at the top level that starts a line
    for i, (kind, val, first) in enumerate(toks):
        prev = toks[i - 1] if i else ("", "", False)
        nxt = toks[i + 1] if i + 1 < len(toks) else ("", "", False)
        if kind == "op" and val in "([{":
            comp = comp or (not stack and first and val in "[{")
            stack.append(val)
        elif kind == "op" and val in ")]}":
            if stack:
                stack.pop()
            comp = comp and bool(stack)
        elif kind == "id" and val == "exports_files" and nxt[1] == "(" and not stack:
            found, comp_args = _exports_names(toks, i + 2)
            names |= found
            computed = computed or comp_args
        elif kind == "id" and nxt[1] == "(" and nxt[0] == "op" and prev[1] != ".":
            if ((not stack and first) or comp) and val not in BAZEL_NATIVE and val not in STARLARK_BUILTINS:
                computed = True
        elif (kind == "op" and val == "=" and prev[0] == "id" and prev[1] in ("name", "out", "outs")
              and stack[-1:] == ["("] and toks[i - 2][1] in ("(", ",") and nxt[1] != "="):
            depth, value, j = 0, [], i + 1
            while j < len(toks):
                k, v, _ = toks[j]
                j += 1
                if k == "op" and v in "([{":
                    depth += 1
                elif k == "op" and v in ")]}":
                    if depth == 0:
                        break
                    depth -= 1
                elif depth == 0 and k == "op" and v == ",":
                    break
                value.append((k, v))
            if _literal(value, prev[1] == "outs"):
                names.update(v for k, v in value if k == "str")
            else:
                computed = True
    bare = any(t[:2] == ("op", "%") and n[:2] == ("op", "{") for t, n in zip(toks, toks[1:], strict=False))
    return names, computed or bare or any("%{" in n for n in names)  # `%{...}` code or a name: filled in on rendering


def _exports_names(toks: list[tuple[str, str, bool]], start: int) -> tuple[set[str], bool]:
    """The files of the first argument of ``exports_files(``, whose arguments start at ``toks[start]``: its strings
    when it is a plain list, and ``True`` for anything else (``glob([...])``, a variable, a sum), which exports
    files not spelled out. Later arguments (``visibility``, ``licenses``) are not files."""
    arg: list[tuple[str, str]] = []
    depth = 0
    for kind, val, _ in toks[start:]:
        if kind == "op" and val in "([{":
            depth += 1
        elif kind == "op" and val in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and kind == "op" and val == ",":
            break
        arg.append((kind, val))
    if arg[:2] == [("id", "srcs"), ("op", "=")]:
        arg = arg[2:]
    if not arg:
        return set(), False
    if _literal(arg, True):
        return {v for k, v in arg if k == "str"}, False
    return set(), True


def check_label(scan: Scan, file: str, line: int, label: str, ctx: str) -> None:
    m = _LABEL_PARTS.match(label.lstrip("-"))
    if not m:
        return
    repo, pkg, target = m.groups()
    if not repo and pkg in ("conditions", "visibility"):  # //conditions:default, //visibility:public
        return
    if repo and _other_repository(scan, file, line, label, (repo.lstrip("@"), pkg, target), ctx):
        return
    if pkg == "..." or pkg.endswith("/..."):
        base = pkg[:-3].rstrip("/") or "."
        scan.add(file, line, "bazel_label", label, "ok" if scan.exists(base) else "fail",
                 f"package tree {base}" + ("" if scan.exists(base) else " does not exist"), ctx)
        return
    build = next((posixpath.join(pkg, b) for b in ("BUILD.bazel", "BUILD") if posixpath.join(pkg, b) in scan.file_set),
                 None)  # Bazel reads BUILD.bazel before BUILD
    if build is None:
        scan.add(file, line, "bazel_label", label, "fail", f"no BUILD file in package '{pkg or '//'}'", ctx)
        return
    name = target or posixpath.basename(pkg)
    names, computed = scan.cached(f"bazel_build:{build}", lambda: _build_names(scan.text(build)))
    why = scan.unread_reason(build)
    if name in names or name in ("all", "*", "all-targets"):
        scan.add(file, line, "bazel_label", label, "ok", f'name = "{name}" in {build}', ctx)
    elif posixpath.join(pkg, name) in scan.file_set:
        scan.add(file, line, "bazel_label", label, "ok", f"file in package {pkg}", ctx)
    elif computed or why:
        scan.add(file, line, "bazel_label", label, "unresolved",
                 f"{build} was not read ({why})" if why else
                 f"no literal name = \"{name}\" in {build}; a macro or a computed name there may generate it", ctx)
    else:
        scan.add(file, line, "bazel_label", label, "fail", f'no target "{name}" in {build}', ctx)


def _other_repository(scan: Scan, file: str, line: int, label: str, parts: tuple[str, str, str | None],
                      ctx: str) -> bool:
    """Claim a label whose repository part matters, and say so: another repository's label, or this module named
    alone (``@onedal//``, the prefix of its own labels)."""
    repo, pkg, target = parts
    if repo not in ("", _bazel_facts(scan)["module"]):
        _check_external(scan, file, line, label, parts, ctx)
        return True
    if not pkg and not target:
        scan.add(file, line, "bazel_label", label, "ok", f"@{repo} is this module", ctx)
        return True
    return False


def _check_external(scan: Scan, file: str, line: int, label: str, parts: tuple[str, str, str | None], ctx: str) -> None:
    """A label in another repository: checked when this tree holds that repository's BUILD file (see
    :func:`_external_builds`), else ``unresolved``. A target that may be a file (``x.h``, or a name the BUILD file
    uses as a string, as a source is) is never ``fail``."""
    repo, pkg, target = parts
    builds = _external_builds(scan, repo) if not pkg and target and "/" not in target else []
    unread = {b: why for b in builds if (why := scan.unread_reason(b))}
    if (why := scan.unread_reason("MODULE.bazel")):  # it may declare @repo as a bazel_dep, which no BUILD file says
        unread["MODULE.bazel"] = why
    if not builds or target is None:
        scan.add(file, line, "bazel_label", label, "unresolved",
                 "; ".join(f"{b} was not read ({w})" for b, w in unread.items()) if unread
                 else f"external repository @{repo}", ctx)
        return
    names: set[str] = set()
    computed = False
    for build in builds:
        found, comp = scan.cached(f"bazel_build:{build}", lambda b=build: _build_names(scan.text(b)))
        names |= found
        computed = computed or comp
    where = ", ".join(builds)
    if target in names or target in ("all", "*", "all-targets"):
        scan.add(file, line, "bazel_label", label, "ok", f'name = "{target}" in {where}, the BUILD file of @{repo}', ctx)
    elif unread or computed or "." in target or target in scan.cached(
            f"bazel_strs:{where}", lambda: {v for b in builds for k, v, _ in _tokens(scan.text(b)) if k == "str"}):
        scan.add(file, line, "bazel_label", label, "unresolved",
                 "; ".join(f"{b} was not read ({w})" for b, w in unread.items()) if unread else
                 f'no literal name = "{target}" in {where}; it may be computed there or be a file of @{repo}', ctx)
    else:
        scan.add(file, line, "bazel_label", label, "fail", f'no target "{target}" in {where}, the BUILD file of @{repo}',
                 ctx)


def _external_builds(scan: Scan, repo: str) -> list[str]:
    """Tracked BUILD files of the external repository ``repo``: named for it (``repo.BUILD``, ``BUILD.repo``,
    ``repo.tpl.BUILD``, ``repo_win.BUILD.bazel``) and named in turn by a ``.bzl`` file, ``MODULE.bazel`` or
    ``WORKSPACE``, as a ``build_file`` or a template is. None for a ``bazel_dep`` of ``MODULE.bazel``: the
    registry supplies that repository, and a leftover WORKSPACE-era BUILD file is not it."""
    def make() -> list[str]:
        if repo in scan.cached("bazel_deps", lambda: _bazel_deps(scan.text("MODULE.bazel"))):
            return []
        paths, repos = scan.cached("bazel_refs", lambda: _bazel_refs(scan))
        r = re.escape(repo)
        named = re.compile(rf"^(?:BUILD\.{r}(?:\.bazel)?|{r}(?:[._][\w.-]*?)?\.BUILD(?:\.bazel)?)$")
        longer = [o for o in repos if o != repo and o.startswith(repo)]  # `lib_tools.BUILD` is @lib_tools', not @lib's
        return [f for f in scan.files if f in paths and named.match(b := posixpath.basename(f))
                and not any(re.match(rf"^(?:BUILD\.)?{re.escape(o)}(?:[._]|$)", b) for o in longer)]
    return scan.cached(f"bazel_external:{repo}", make)


def _bazel_deps(text: str) -> set[str]:
    """The repository names ``bazel_dep(...)`` calls of a ``MODULE.bazel`` make visible: ``repo_name``, else
    ``name``."""
    text = _TOKEN.sub(lambda m: "" if m.lastgroup == "comment" else m.group(), text)
    out: set[str] = set()
    for m in re.finditer(r"\bbazel_dep\(([^)]*)\)", text):
        name = re.search(r"""\brepo_name\s*=\s*["']([^"']+)""", m.group(1)) or re.search(
            r"""\bname\s*=\s*["']([^"']+)""", m.group(1))
        if name:
            out.add(name.group(1))
    return out


def _bazel_refs(scan: Scan) -> tuple[set[str], set[str]]:
    """The tracked paths that a string of a ``.bzl`` file, ``MODULE.bazel`` or ``WORKSPACE`` names (a label
    ``//pkg:f``, ``:f`` or a bare ``f`` next to that file, or a path ``pkg/f``), and the repository names those files
    declare (``name = "x"``). Comments do not count."""
    paths: set[str] = set()
    repos: set[str] = set()
    for f in scan.files:
        if not (f.endswith(".bzl") or f in ("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel")):
            continue
        here = posixpath.dirname(f)
        toks = list(_tokens(scan.text(f)))
        for i, (kind, val, _) in enumerate(toks):
            if kind != "str":
                continue
            if i >= 2 and toks[i - 1][1] == "=" and toks[i - 2][1] == "name":
                repos.add(val)
            m = re.match(r"^(?:@@?[\w.~+-]*)?//([^:]*):(.+)$", val)
            if m:
                paths.add(posixpath.normpath(posixpath.join(m.group(1), m.group(2))))
            else:
                paths.add(posixpath.normpath(posixpath.join(here, val.lstrip(":"))))
                paths.add(posixpath.normpath(val))
    return paths, repos


def label_claims(scan: Scan, file: str, line: int, text: str) -> bool:
    """Check every Bazel label in ``text``; ``True`` if it held any."""
    labels = [m.group() for m in _LABEL_RE.finditer(text)
              if _is_label(m.group()) and text[m.end():m.end() + 1] not in ("$", "`", "<", "{")]  # `//:t_$X`, `//a/<b>`
    if labels and "bazel" in scan.adapters:
        for label in labels:
            check_label(scan, file, line, label, text)
    return bool(labels)


def _is_label(label: str) -> bool:
    """A package, a target or a repository, and no host name (`//github.com/x` is a protocol-relative URL)."""
    m = _LABEL_PARTS.match(label.lstrip("-"))
    if not m:
        return False
    repo, pkg, target = m.groups()
    return bool(pkg or target or (repo or "").strip("@")) and "." not in pkg.split("/", 1)[0]


def _bazel(scan: Scan, file: str, line: int, toks: list[str], text: str) -> None:
    args = list(toks[1:])
    while args and args[0].startswith("-"):  # startup options
        args.pop(0)
    if len(args) < 2:
        return  # `bazel test` in prose names the verb only
    facts = _bazel_facts(scan)
    rest = args[1:]
    if "--" in rest:  # what follows goes to the built binary, not to bazel
        rest = rest[:rest.index("--")]
    for i, t in enumerate(rest):
        if t.startswith("--config"):
            value = t.split("=", 1)[1] if "=" in t else (rest[i + 1] if i + 1 < len(rest) else "")
            if dynamic(value):
                continue
            ok = value in facts["configs"]
            if not ok and facts["unread"]:
                why = "; ".join(f"{rc} was not read ({r})" for rc, r in sorted(facts["unread"].items()))
                scan.add(file, line, "bazel_config", value, "unresolved", why, text)
                continue
            scan.add(file, line, "bazel_config", value, "ok" if ok else "fail",
                     "config in .bazelrc" if ok else "no such config in .bazelrc or its imports", text)
        elif t.startswith("--") and t[2:].split("=")[0] in facts["aliases"]:
            scan.add(file, line, "bazel_flag", t, "ok", "flag_alias in .bazelrc", text)
        elif "//" in t and _is_label(t.lstrip("-")):
            check_label(scan, file, line, t, text)


_LOAD_LABEL = re.compile(r"""^load\(\s*["'](?:@|//|:)""", re.M)
# Calls only Bazel has. A name plain Python also uses (`test_suite`, `package`, `platform`, `glob`) is left out.
_BUILD_ONLY = BAZEL_NATIVE - {"load", "package", "glob", "select", "alias", "test_suite", "module", "platform",
                              "toolchain", "workspace", "licenses", "exports_files", "package_group"}


def reads_as_build(scan: Scan, fence: Fence) -> bool:
    """A fence in another language (Python) holds BUILD code: it loads a label, or a line starts a call to a
    native rule only Bazel has (``cc_library``, ``http_archive``). A call to a macro of a ``.bzl`` file proves
    nothing: ``run(`` and ``build(`` are as often plain Python."""
    code = "\n".join(text for _, text in fence.lines)
    return bool(_LOAD_LABEL.search(code)) or any(
        m.group(1) in _BUILD_ONLY for m in re.finditer(r"^[ \t]*(\w+)\(", code, re.M))


def check_starlark(scan: Scan, file: str, fence: Fence) -> None:
    code = "\n".join(text for _, text in fence.lines)
    facts = _bazel_facts(scan)
    for m in re.finditer(r"^(\w+)\(", code, re.M):
        name = m.group(1)
        line = fence.lines[code[:m.start()].count("\n")][0]
        snippet = code[m.start():m.start() + 80]
        if name == "load":
            _check_load(scan, file, line, code[m.end():], snippet)
            continue
        if name in BAZEL_NATIVE:
            continue
        if name not in facts["defs"]:
            scan.add(file, line, "starlark_macro", name, "unresolved",
                     "not defined in a tracked .bzl file; it may come from an external rule set", snippet)
            continue
        where, params = facts["defs"][name]
        scan.add(file, line, "starlark_macro", name, "ok", f"defined in {where}", snippet)
        kwargs = any(p.startswith("**") for p in params) or name in facts["twice"]
        for kw in re.findall(r"^\s{4}(\w+)\s*=", _call_body(code, m.end() - 1), re.M):
            if kw not in params:
                scan.add(file, line, "starlark_kwarg", f"{name}({kw}=)", "unresolved" if kwargs else "fail",
                         f"{name} is defined more than once or takes **kwargs" if kwargs
                         else f"not a parameter of {name} in {where}", snippet)
    for m in _TOKEN.finditer(code):  # a string of either quote (triple-quoted too), not one inside a comment
        if m.lastgroup != "str":
            continue
        label = m.group().lstrip("rRbB")
        label = label[3:-3] if label[:3] in ('"""', "'''") else label[1:-1]
        if _LABEL_PARTS.match(label) and _is_label(label):
            n, text = fence.lines[code.count("\n", 0, m.start())]
            check_label(scan, file, n, label, text)


def _check_load(scan: Scan, file: str, line: int, args: str, snippet: str) -> None:
    """``load("//pkg:x.bzl", "a", b = "c")``: each symbol is a public top-level name (a ``def`` or an assignment)
    of ``x.bzl``, when the label is absolute, in this repository and in the tree. A missing file is the label's
    own claim; a name ``x.bzl`` itself loads is not exported by it."""
    strs = _load_strings(args)
    m = _LABEL_PARTS.match(strs[0]) if strs else None
    if not m or (m.group(1) or "@").lstrip("@") not in ("", _bazel_facts(scan)["module"]):
        return  # relative to a package the snippet does not name, or in another repository
    path = posixpath.join(m.group(2), m.group(3) or "")
    if path not in scan.file_set:
        return
    public, other = scan.cached(f"bzl_public:{path}", lambda: _bzl_public(scan.text(path)))
    why = scan.unread_reason(path)
    for sym in strs[1:]:
        ok = sym in public
        if why and not ok:  # the names of a file that was not read are not known
            scan.add(file, line, "starlark_load", f"{sym} from {strs[0]}", "unresolved", f"{path} was not read ({why})",
                     snippet)
            continue
        status = "ok" if ok else "unresolved" if other else "fail"
        scan.add(file, line, "starlark_load", f"{sym} from {strs[0]}", status,
                 f"defined in {path}" if ok else f"no public top-level {sym} in {path}"
                 + ("; it binds names in a form not read here" if other else ""), snippet)


def _bzl_public(text: str) -> tuple[set[str], bool]:
    """The public top-level names of a ``.bzl`` file (``def x(``, ``x = ...``, ``x, _y = ...``), and whether it also
    binds names in a form not read here (``(x, y) = ...``, ``[x] = ...``)."""
    masked_text = _TOKEN.sub(lambda m: re.sub(r"[^\n]", " ", m.group()) if m.lastgroup in ("str", "comment")
                             else m.group(), text)  # a docstring or a comment binds nothing
    names: set[str] = set()
    for m in re.finditer(r"^def\s+([A-Za-z]\w*)\s*\(|^([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*,?\s*=(?!=)", masked_text, re.M):
        names.update(n for n in re.split(r"\s*,\s*", m.group(1) or m.group(2)) if n and not n.startswith("_"))
    return names, re.search(r"^[(\[][^\n]*\)?\]?\s*=(?!=)", masked_text, re.M) is not None


def _load_strings(args: str) -> list[str]:
    """The strings of a ``load(`` call whose arguments start ``args``: the label, then each symbol (the loaded name
    of ``alias = "name"``)."""
    out = []
    for kind, val, _ in _tokens(args):
        if kind == "op" and val == ")":
            break
        if kind == "str":
            out.append(val)
    return out


def _call_body(code: str, open_paren: int) -> str:
    depth = 0
    for k in range(open_paren, len(code)):
        depth += (code[k] == "(") - (code[k] == ")")
        if depth == 0:
            return code[open_paren + 1:k]
    return code[open_paren + 1:]


# ---------------------------------------------------------------- make
def _make_facts(scan: Scan) -> dict:
    def make() -> dict:
        files = [f for f in scan.files if posixpath.basename(f) in ("Makefile", "makefile", "GNUmakefile")
                 or f.endswith(".mk")]
        scan.preload(files)
        targets: set[str] = set()
        patterns: set[str] = set()
        computed = False
        text = ""
        for f in files:
            body = scan.text(f)
            text += body + "\n"
            for m in _MAKE_RULE.finditer(body):
                for t in m.group(1).split():
                    if "$" in t:
                        computed = True
                    elif "%" in t:
                        patterns.add(t)
                    else:
                        targets.add(t)
        return {"files": files, "targets": targets, "patterns": patterns, "computed": computed, "text": text}
    return scan.cached("make", make)


def _make(scan: Scan, file: str, line: int, toks: list[str], text: str) -> None:
    facts = _make_facts(scan)
    args = iter(toks[1:])
    for t in args:
        if t in _MAKE_OPT_ARG:
            next(args, None)
            continue
        if t in _MAKE_OPT_NUM:
            t = next(args, "")
            if not t or t.isdigit() or t.replace(".", "", 1).isdigit():
                continue
        if t.startswith("-") or (dynamic(t) and "=" not in t) or dynamic(t.split("=", 1)[0]):
            continue  # an expansion (`$(nproc)`, `$TARGET`, `FOO_$X=1`) is not a name to look up
        if "=" in t:
            key = t.split("=", 1)[0].rstrip("+:?")
            ok = re.search(rf"\b{re.escape(key)}\b", facts["text"]) is not None
            scan.add(file, line, "make_var", t, "ok" if ok else "fail",
                     "variable used in the makefiles" if ok else f"{key} appears in no makefile", text)
        elif not facts["files"]:
            scan.add(file, line, "make_target", t, "fail", "the repository has no makefile", text)
        elif t in facts["targets"]:
            scan.add(file, line, "make_target", t, "ok", "rule in the makefiles", text)
        elif any(fnmatch.fnmatchcase(t, p.replace("%", "*")) for p in facts["patterns"]):
            status = "unresolved" if "%" in facts["patterns"] else "ok"
            scan.add(file, line, "make_target", t, status, "matched by a pattern rule", text)
        elif facts["computed"]:
            scan.add(file, line, "make_target", t, "unresolved",
                     "no literal rule; some rule names are built from variables and not expanded here", text)
        else:
            scan.add(file, line, "make_target", t, "fail", "no rule for it in the makefiles", text)


# ---------------------------------------------------------------- cmake
_CMAKE_BUILTIN = re.compile(r"^(CMAKE_|CTEST_|CPACK_|Python3?_|BUILD_SHARED_LIBS$|BUILD_TESTING$)|_(DIR|ROOT)$")


def _cmake_facts(scan: Scan) -> dict:
    def make() -> dict:
        files = [f for f in scan.files if posixpath.basename(f) == "CMakeLists.txt" or f.endswith((".cmake", ".cmake.in"))]
        scan.preload(files)
        presets: set[str] = set()
        for name in ("CMakePresets.json", "CMakeUserPresets.json"):
            try:
                doc = json.loads(scan.text(name) or "{}")
            except json.JSONDecodeError:
                continue
            for key, value in (doc.items() if isinstance(doc, dict) else ()):
                if key.endswith("Presets") and isinstance(value, list):
                    presets |= {p["name"] for p in value if isinstance(p, dict) and isinstance(p.get("name"), str)}
        return {"sources": "\n".join(scan.text(f) for f in files), "presets": presets}
    return scan.cached("cmake", make)


def _cmake(scan: Scan, file: str, line: int, toks: list[str], text: str) -> None:
    facts = _cmake_facts(scan)
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t.startswith("-D"):
            _cmake_var(scan, file, line, (t[2:] or nxt).split("=")[0].split(":")[0], text)
        elif t.startswith("-S"):
            _cmake_source(scan, file, line, t[2:] or nxt, text)
        elif t == "--preset" and nxt or t.startswith("--preset="):
            name = t.split("=", 1)[1] if "=" in t else nxt
            if dynamic(name):
                continue
            ok = name in facts["presets"]
            scan.add(file, line, "cmake_preset", name, "ok" if ok else "fail",
                     "preset in CMakePresets.json" if ok else "no such preset in CMake(User)Presets.json", text)


def _cmake_var(scan: Scan, file: str, line: int, var: str, text: str) -> None:
    if not var or dynamic(var):
        return
    if _CMAKE_BUILTIN.search(var):
        scan.add(file, line, "cmake_var", var, "ok", "CMake built-in or package variable", text)
    else:
        ok = re.search(rf"\b{re.escape(var)}\b", _cmake_facts(scan)["sources"]) is not None
        scan.add(file, line, "cmake_var", var, "ok" if ok else "fail",
                 "used in the CMake sources" if ok else "appears in no CMakeLists.txt or .cmake file", text)


def _raw_source_has_backslash(text: str, src: str) -> bool:
    """True when the ``-S`` word of the command text that gives ``src`` holds a backslash the shell split removed.
    A backslash in another word of the same command does not count."""
    for m in re.finditer(r"(?:^|\s)-S\s*(\S+)", text):
        raw = m.group(1)
        if "\\" in raw:
            try:
                parts = shlex.split(raw)
            except ValueError:
                continue
            if parts == [src]:
                return True
    return False


def _cmake_source(scan: Scan, file: str, line: int, src: str, text: str) -> None:
    """``-S <dir>`` names a directory with a ``CMakeLists.txt``. Where the command runs is known only after a
    literal ``cd``; without one the directory is tried at the guidance file's directory and its ancestors, and a
    miss is ``unresolved``, since the text may say elsewhere where to run it."""
    # the shell split already dropped the backslash of an unquoted `src\app`, so the command text is checked too
    if not src or dynamic(src) or src.startswith(("/", "~", "-")) or any(c in src for c in "*?[{%\\") \
            or _raw_source_has_backslash(text, src):
        return  # a glob or brace expansion, a Windows path or a Windows variable names directories this text does not list
    if re.match(r"^[A-Za-z]:", src):
        return
    cd = scan.cd_dirs
    if cd == []:
        scan.add(file, line, "cmake_src", src, "unresolved", "after a `cd` this tree cannot follow", text)
        return
    dirs = [d for d in dict.fromkeys(posixpath.normpath(posixpath.join(b, src)) for b in (cd or ancestors(file)))
            if not d.startswith("..")]
    hit = next((d for d in dirs if scan.exists(posixpath.join(d, "CMakeLists.txt"))), None)
    if hit is not None:
        scan.add(file, line, "cmake_src", src, "ok", f"{posixpath.normpath(posixpath.join(hit, 'CMakeLists.txt'))} exists", text)
    elif cd is None:
        scan.add(file, line, "cmake_src", src, "unresolved",
                 f"no CMakeLists.txt in {', '.join(dirs) or src}; it depends on where the command runs", text)
    elif sub := scan.submodule_of(dirs):
        scan.add(file, line, "cmake_src", src, "unresolved", f"inside the submodule {sub}, which this snapshot does not hold",
                 text)
    else:
        scan.add(file, line, "cmake_src", src, "fail", f"no CMakeLists.txt in {', '.join(dirs)} after the `cd`", text)


# ---------------------------------------------------------------- pytest
def _pytest(scan: Scan, file: str, line: int, toks: list[str], text: str) -> None:
    args = iter(toks[1:])
    pyargs = "--pyargs" in toks
    for t in args:
        if t in _PYTEST_OPT_ARG:
            next(args, None)
            continue
        if t.startswith("-"):
            continue
        path, _, node = t.partition("::")
        kind, r = "pytest_path", scan.resolve_path(file, path)
        # with --pyargs pytest imports the name first, and reads it as a path only when no module has it
        if pyargs and _MODULE_NAME.match(path) and not (r and r[0].startswith("ok")):
            kind, r = "pytest_module", _pytest_module(scan, path)
        if r is None:
            continue
        scan.add(file, line, kind, path, r[0], r[1], text)
        if node and r[0] == "ok" and r[1] in scan.file_set and not dynamic(node):
            name = node.split("[")[0].split("::")[-1]  # a parameter id may hold `::`
            ok = re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b", scan.text(r[1]), re.M)
            scan.add(file, line, "pytest_node", t, "ok" if ok else "fail",
                     f"{'defined' if ok else 'not defined'} in {r[1]}", text)


def _pytest_module(scan: Scan, name: str) -> tuple[str, str]:
    """A ``--pyargs`` module: ``a.b`` is ``a/b.py`` or ``a/b/__init__.py`` below any directory of the tree.

    A directory with no ``__init__.py`` is no module (pytest skips namespace packages by default). A module
    missing from a package the tree holds is ``fail``; any other missing module may be installed from elsewhere.
    """
    def find(rel: str) -> str | None:
        return next((f for f in scan.by_name.get(posixpath.basename(rel), []) if f == rel or f.endswith("/" + rel)), None)

    rel = name.replace(".", "/")
    hit = find(rel + ".py") or find(rel + "/__init__.py")
    if hit:
        return "ok", hit
    package, _, leaf = name.rpartition(".")
    home = find(package.replace(".", "/") + "/__init__.py") if package else None
    if home:
        return "fail", f"package {package} ({posixpath.dirname(home)}/) has no module {leaf}"
    return "unresolved", f"no module {name} in the tree; it may be installed from elsewhere"


def _python(scan: Scan, file: str, line: int, toks: list[str], text: str) -> None:
    if toks[1:3] == ["-m", "pytest"]:
        _pytest(scan, file, line, ["pytest", *toks[3:]], text)


# ---------------------------------------------------------------- pre-commit
def _pre_commit(scan: Scan, file: str, line: int, toks: list[str], text: str) -> None:
    if len(toks) < 3 or toks[1] != "run" or toks[2].startswith("-"):
        return
    hook = toks[2]
    if dynamic(hook):
        return

    def make() -> set[str] | None:
        if ".pre-commit-config.yaml" not in scan.file_set:
            return None
        try:
            cfg = yaml.safe_load(scan.text(".pre-commit-config.yaml")) or {}
        except yaml.YAMLError:
            return set()
        ids: set[str] = set()
        repos = cfg.get("repos") if isinstance(cfg, dict) else None
        for repo in repos if isinstance(repos, list) else []:
            hooks = repo.get("hooks") if isinstance(repo, dict) else None
            for h in hooks if isinstance(hooks, list) else []:
                if isinstance(h, dict):
                    ids |= {v for v in (h.get("id"), h.get("alias")) if isinstance(v, str)}
        return ids
    ids = scan.cached("pre-commit", make)
    if ids is None:
        scan.add(file, line, "precommit_hook", hook, "fail", "the repository has no .pre-commit-config.yaml", text)
    else:
        scan.add(file, line, "precommit_hook", hook, "ok" if hook in ids else "fail",
                 "hook id in .pre-commit-config.yaml" if hook in ids else "no hook with this id or alias", text)


# first word of a command -> (adapter that must be enabled, its check)
COMMANDS: dict[str, tuple[str, Handler]] = {
    "bazel": ("bazel", _bazel), "bazelisk": ("bazel", _bazel),
    "make": ("make", _make), "gmake": ("make", _make),
    "cmake": ("cmake", _cmake),
    "pytest": ("pytest", _pytest), "py.test": ("pytest", _pytest),
    "python": ("pytest", _python), "python3": ("pytest", _python),
    "pre-commit": ("pre-commit", _pre_commit),
}
