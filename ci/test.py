# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Intel CI test for dokimi.

GitHub Actions, through ci/hooks.py, and Intel's internal CI both run this with one
built package installed in a clean environment. It runs dokimi's own test
suite from the checkout's dokimi/ directory against that installed package,
offline as dokimi's pytest settings make it, and writes a JUnit report for
the internal CI.
"""

import os
import pathlib
import subprocess
import sys
import sysconfig
import tempfile

import pytest

PACKAGE = "dokimi"
# These expect the package inside the source checkout, so they cannot pass on an installed one.
SOURCE_ONLY = (
    "tests/test_packaging.py::test_default_data_files_ship_in_the_wheel",
    "tests/test_explore_track.py::test_seed_tasks_load_and_reference_real_files",
)


def running_python() -> str:
    """This interpreter's version as artifact names spell it, such as 3.14t."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    threaded = sysconfig.get_config_var("Py_GIL_DISABLED")
    return version + ("t" if threaded else "")


def installed_package() -> pathlib.Path:
    """Where `import dokimi` resolves from the working directory."""
    shown = subprocess.run(
        [sys.executable, "-c", "import dokimi; print(dokimi.__file__)"],
        check=True,
        capture_output=True,
        text=True,
    )
    return pathlib.Path(shown.stdout.strip()).resolve()


CHECK_DATA = (
    "from dokimi import config, intents, registry; from dokimi.runner import spec;"
    "paths = (registry._DEFAULT_REGISTRY, intents._DEFAULT_INTENTS,"
    " config.DEFAULT_PRODUCTS_PATH, spec.DEFAULT_SCHEMA_PATH);"
    "print(*paths, sep=chr(10));"
    "raise SystemExit(not all(path.is_file() for path in paths))"
)


def main() -> int:
    """Runs the project's test suite against the installed package."""
    expected = os.environ["GH_CI_PYTHON"]
    if running_python() != expected:
        print(f"running Python {running_python()}, the cell is {expected}")
        return 1
    source = pathlib.Path(os.environ["GH_CI_SOURCE"])
    project = source / PACKAGE
    # The suite reads data/ and docs/ relative to the project, so it runs in it.
    # No empty-string sys.path entry then puts the source package first.
    os.chdir(project)
    os.environ["PYTHONSAFEPATH"] = "1"
    where = installed_package()
    print(f"dokimi imported from {where}", flush=True)
    if where.is_relative_to(source.resolve()):
        print("dokimi resolves inside the checkout, not the installed package")
        return 1
    # The deselected packaging test is the only check of these, so repeat it here.
    data = subprocess.run(
        [sys.executable, "-c", CHECK_DATA],
        capture_output=True,
        text=True,
        check=False,
    )
    print("default data files:", *data.stdout.split(), flush=True)
    if data.returncode:
        print(data.stderr or "a default data file is missing from the installed package")
        return 1
    # Every package the internal CI tests reports into the same build, so each
    # names its own suite.
    platform = os.environ.get("GH_CI_PLATFORM", "linux")
    cell = f"{platform}.py{expected}.{os.environ.get('GH_CI_KIND', 'conda')}"
    print("deselected, they need the source checkout: " + ", ".join(SOURCE_ONLY))
    report = pathlib.Path(os.environ["GH_CI_RESULTS"]) / f"{PACKAGE}.xml"
    with tempfile.TemporaryDirectory() as home:
        # Tests that commit to scratch repositories need a git identity.
        gitconfig = pathlib.Path(home) / "gitconfig"
        gitconfig.write_text("[user]\n\tname = dokimi-ci\n\temail = dokimi-ci@example.com\n")
        os.environ["GIT_CONFIG_GLOBAL"] = str(gitconfig)
        return pytest.main(
            [
                "-v",
                "-p",
                "no:cacheprovider",
                # Imported first, so conftest.py putting the project on sys.path
                # cannot shadow the installed package, in the xdist workers too.
                "-p",
                PACKAGE,
                "-n",
                "auto",
                *(f"--deselect={test}" for test in SOURCE_ONLY),
                f"--junitxml={report}",
                "-o",
                f"junit_suite_name={PACKAGE} {cell}",
            ]
        )


if __name__ == "__main__":
    sys.exit(main())
