# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Builds this repository's packages for one Python version.

For the gh-ci GitHub Actions workflow, and for Coverity in Jenkins.

Copy this file and ci/matrix.py into any repository gh-ci tests. They work out
the build system from the files present, and the Python versions from that
build system's own declaration, so most repositories need no changes to them:

  rattler-build    .conda  recipe.yaml or recipe/recipe.yaml, versions in
                           variants.yaml
  conda-build      .conda  conda-recipe/meta.yaml or meta.yaml, versions in
                           conda_build_config.yaml
  cibuildwheel     .whl    pyproject.toml, at the root or the only one a
                           directory down, versions from its classifiers

Usage:
  python ci/build.py --python 3.14 --output OUT [--system rattler-build]
                     [--channel conda-forge]

--python must be one of the declared versions; `python ci/matrix.py` lists them.

Packages land directly in OUT, ready for actions/upload-artifact, which like
ci/hooks.py takes every package there: give each build an empty OUT. A conda
recipe's own tests run during the build; Jenkins later runs ci/test.py against
the uploaded artifacts, and builds with this script once more under Coverity for
intel-innersource tags. Needs only the Python standard library. rattler-build is
used from PATH, or downloaded at a pinned version for Linux or Windows and
checked against its published sha256; conda-build, or cibuildwheel for wheels,
must already be installed for this interpreter, so run it with the conda base
install's Python to build with conda-build.

Wheels build with cibuildwheel, in a manylinux container on Linux, as
pyproject.toml's [tool.cibuildwheel] says; a pyproject.toml without it builds a
pure Python wheel with `pip wheel`. Run-time
requirements that pyproject.toml cannot declare, because the conda package gets
them from its recipe instead, such as MKL's libraries, go in [tool.gh-ci]
wheel-requires: build.py adds them to each wheel's metadata.
"""

import argparse
import base64
import hashlib
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import matrix

ROOT = matrix.ROOT
PACKAGE_SUFFIXES = (".conda", ".tar.bz2", ".whl")
# cibuildwheel's platform part of a build identifier, per system.
WHEEL_PLATFORMS = {
    "Linux": "manylinux_x86_64",
    "Windows": "win_amd64",
    "Darwin": "macosx_x86_64",
}

# The rattler-build release fetched when none is on PATH, per system, with the
# file name it goes under and the sha256 its release publishes.
RATTLER_BUILD_VERSION = "v0.77.0"
RATTLER_BUILD_RELEASES = {
    "Linux": (
        "rattler-build-x86_64-unknown-linux-musl",
        "rattler-build",
        "5a5e8150c703b21e88a01f9130ea49a7a680198e9ac9414170c0a293fed7e5af",
    ),
    "Windows": (
        "rattler-build-x86_64-pc-windows-msvc.exe",
        "rattler-build.exe",
        "e144334f782cee9aa23e26eb8d095c143e1696fbbec7ba7534a156663d4abb2c",
    ),
}


class BuildError(Exception):
    """Raised when the build cannot start or produced nothing."""


def ensure_rattler_build(tools: Path) -> None:
    """
    Puts rattler-build on PATH, downloading the pinned release when none is
    there.
    """
    if shutil.which("rattler-build"):
        return
    release = RATTLER_BUILD_RELEASES.get(platform.system())
    if release is None or platform.machine().lower() not in ("x86_64", "amd64"):
        raise BuildError(
            "no rattler-build on PATH, and only the Linux and Windows x86_64 "
            "releases are pinned here"
        )
    asset, name, sha256 = release
    print(f"build: fetching rattler-build {RATTLER_BUILD_VERSION}", flush=True)
    url = (
        "https://github.com/prefix-dev/rattler-build/releases/download/"
        f"{RATTLER_BUILD_VERSION}/{asset}"
    )
    with urllib.request.urlopen(url, timeout=300) as reply:  # nosec B310 - a fixed https URL
        binary = reply.read()
    if hashlib.sha256(binary).hexdigest() != sha256:
        raise BuildError(
            f"rattler-build {RATTLER_BUILD_VERSION} does not match its pinned "
            "sha256"
        )
    tools.mkdir(parents=True, exist_ok=True)
    target = tools / name
    target.write_bytes(binary)
    target.chmod(0o755)
    os.environ["PATH"] = f'{tools}{os.pathsep}{os.environ.get("PATH", "")}'


def cibuildwheel_identifier(python: matrix.Python) -> str:
    """The one build cibuildwheel runs, such as cp314t-manylinux_x86_64."""
    tag = WHEEL_PLATFORMS.get(platform.system())
    if tag is None:
        raise BuildError(
            f"wheels build on Linux, Windows or macOS, not {platform.system()}"
        )
    minor, threaded = matrix.version_key(python.version)
    return f'cp3{minor}{"t" if threaded else ""}-{tag}'


def pyproject_tools(pyproject: Path) -> dict:
    """The [tool] table of a pyproject.toml."""
    if matrix.TOMLLIB is None:
        raise BuildError("reading pyproject.toml needs Python 3.11 or newer")
    text = pyproject.read_text(encoding="utf-8")
    return matrix.TOMLLIB.loads(text).get("tool", {})


def wheel_requires(pyproject: Path) -> list[str]:
    """[tool.gh-ci] wheel-requires from pyproject.toml."""
    tool = pyproject_tools(pyproject).get("gh-ci", {})
    requires = tool.get("wheel-requires", [])
    if not isinstance(requires, list) or not all(
        isinstance(r, str) for r in requires
    ):
        raise BuildError("[tool.gh-ci] wheel-requires must be a list of specs")
    return requires


def record_hash(data: bytes) -> str:
    """A file's hash as a wheel's RECORD spells it."""
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return "sha256=" + digest.rstrip(b"=").decode("ascii")


def with_requirements(metadata: str, requirements: list[str]) -> str:
    """METADATA with Requires-Dist lines added to its headers, LF or CRLF."""
    newline = "\r\n" if "\r\n" in metadata else "\n"
    head, *body = re.split(r"\r?\n\r?\n", metadata, maxsplit=1)
    head = head.rstrip("\r\n") + "".join(
        f"{newline}Requires-Dist: {r}" for r in requirements
    )
    return head + newline + (newline + body[0] if body else "")


def add_requirements(wheel: Path, requirements: list[str]) -> None:
    """Adds Requires-Dist lines to a built wheel, and updates its RECORD."""
    with zipfile.ZipFile(wheel) as source:
        members = [(info, source.read(info)) for info in source.infolist()]
    names = [info.filename for info, _ in members]
    metadata = next(n for n in names if n.endswith(".dist-info/METADATA"))
    record = metadata.removesuffix("METADATA") + "RECORD"
    for index, (info, data) in enumerate(members):
        if info.filename == metadata:
            new_metadata = with_requirements(
                data.decode("utf-8"), requirements
            ).encode("utf-8")
            members[index] = (info, new_metadata)
    for index, (info, data) in enumerate(members):
        if info.filename == record:
            lines = [
                (
                    f"{metadata},{record_hash(new_metadata)},"
                    f"{len(new_metadata)}"
                    if line.split(",")[0] == metadata
                    else line
                )
                for line in data.decode("utf-8").splitlines()
            ]
            members[index] = (info, ("\n".join(lines) + "\n").encode())
    rewritten = wheel.with_suffix(".tmp")
    with zipfile.ZipFile(rewritten, "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in members:
            target.writestr(info, data)
    rewritten.replace(wheel)


def commands(
    system: str,
    selector: Path,
    python: matrix.Python,
    channels: list[str],
    scratch: Path,
) -> list[list[str]]:
    """
    The commands that build one Python version's packages into scratch.

    Each build system gets the version back exactly as its own file declares it,
    and keeps every other variant setting from that file.
    """
    channel_args = [arg for channel in channels for arg in ("-c", channel)]
    if system == "rattler-build":
        return [
            [
                "rattler-build",
                "build",
                "-r",
                str(selector),
                "--variant",
                f"python={python.spec}",
                "--output-dir",
                str(scratch / "conda"),
                *channel_args,
            ]
        ]
    if system == "conda-build":
        # conda runs as a module of this interpreter, so Windows needs no
        # conda.bat on PATH.
        return [
            [
                sys.executable,
                "-m",
                "conda",
                "build",
                str(selector.parent),
                "--python",
                python.spec,
                "--no-anaconda-upload",
                "--output-folder",
                str(scratch / "conda"),
                "--override-channels",
                *channel_args,
            ]
        ]
    if "cibuildwheel" not in pyproject_tools(selector):
        # A pure Python project has nothing for cibuildwheel to repair.
        return [
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                str(scratch / "wheel"),
                str(selector.parent),
            ]
        ]
    return [
        [
            sys.executable,
            "-m",
            "cibuildwheel",
            "--only",
            cibuildwheel_identifier(python),
            "--output-dir",
            str(scratch / "wheel"),
            str(selector.parent),
        ]
    ]


def collect(scratch: Path, output: Path) -> list[Path]:
    """
    Moves built packages into output, flat, and lists them, not those an
    earlier build left there.
    """
    output.mkdir(parents=True, exist_ok=True)
    built: list[Path] = []
    for package in sorted(scratch.rglob("*")):
        if (
            package.is_file()
            and package.name.endswith(PACKAGE_SUFFIXES)
            and "broken" not in package.parts
        ):
            shutil.move(str(package), output / package.name)
            built.append(output / package.name)
    built.sort()
    if not built:
        raise BuildError("the build produced no package")
    return built


def main(argv: list[str] | None = None) -> int:
    """Builds one Python version."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--python", required=True, help="a declared version, such as 3.14t"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="where the packages go"
    )
    parser.add_argument(
        "--system",
        default="",
        choices=("", *matrix.SYSTEMS),
        help="build system to use instead of the detected one",
    )
    parser.add_argument(
        "--channel",
        action="append",
        default=None,
        help="conda channel to build against, repeatable; default conda-forge",
    )
    default_tools = (
        Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
        / "gh-ci-tools"
    )
    parser.add_argument(
        "--tools",
        type=Path,
        default=default_tools,
        help="where a downloaded rattler-build goes",
    )
    args = parser.parse_args(argv)

    system, selector = matrix.detect(ROOT, args.system)
    declared = {
        python.version: python for python in matrix.declared(system, selector)
    }
    if args.python not in declared:
        raise BuildError(
            f"Python {args.python} is not declared for {system}; "
            f'declared: {", ".join(declared)}'
        )
    python = declared[args.python]
    print(
        f"build: {system} from {selector.relative_to(ROOT)} "
        f"for Python {python.version} ({python.spec})",
        flush=True,
    )
    if system == "rattler-build":
        ensure_rattler_build(args.tools)
    output = args.output.resolve()
    with tempfile.TemporaryDirectory() as scratch:
        for command in commands(
            system,
            selector,
            python,
            args.channel or ["conda-forge"],
            Path(scratch),
        ):
            print(f"+ {shlex.join(command)}", flush=True)
            subprocess.run(command, check=True)
        built = collect(Path(scratch), output)
    if system == "wheel" and (requires := wheel_requires(selector)):
        for wheel in built:
            add_requirements(wheel, requires)
        print("build: added Requires-Dist " + ", ".join(requires))
    print("build: " + ", ".join(path.name for path in built))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        BuildError,
        matrix.DeclarationError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"build: {error}", file=sys.stderr)
        sys.exit(1)
