# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The API reference's landing page, the ``dokimi`` package docstring, maps every subpackage."""

import importlib.util
import pkgutil
import re

import dokimi

# The CLI, which has its own reference (scripts/docs/gen_reference.py, SKIP_API). A private
# (`_`-prefixed) subpackage gets no reference page either.
NOT_IN_API = {"commands"}


def _links(doc: str) -> set[str]:
    return set(re.findall(r"\]\[(dokimi(?:\.\w+)*)\]", doc))


def test_the_landing_page_links_every_subpackage():
    packages = {m.name for m in pkgutil.iter_modules(dokimi.__path__)
                if m.ispkg and not m.name.startswith("_")} - NOT_IN_API
    missing = sorted(p for p in packages if f"dokimi.{p}" not in _links(dokimi.__doc__ or ""))
    assert not missing, f"link {missing} from the dokimi/__init__.py docstring, in the track it serves"


def _exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:  # a missing parent of a dotted name
        return False


def test_every_landing_page_link_names_a_module():
    stale = sorted(name for name in _links(dokimi.__doc__ or "") if not _exists(name))
    assert not stale, f"{stale} in the dokimi/__init__.py docstring name no module"
