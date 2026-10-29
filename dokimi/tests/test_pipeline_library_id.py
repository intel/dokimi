# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``EvaluationPipeline._library_id``: which doc-source library a run queries."""

import pytest

from dokimi.orchestrator.pipeline import EvaluationPipeline
from dokimi.registry import ProductRegistry

REGISTRY = """\
products:
  my_lib:
    name: My Lib
    description: The registry key differs from the display name
    context7_id: org/declared
    doc_sources: [context7]
"""


class _RecordingClient:
    """Records what the pipeline asks it to resolve; a bare name resolves to a marker."""

    requires_declared_library_id = True

    def __init__(self):
        self.asked = []

    def resolve_library_id(self, name):
        self.asked.append(name)
        return f"client:{name}"


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "products.yaml"
    path.write_text(REGISTRY, encoding="utf-8")
    return ProductRegistry(path=path)


def _pipeline(tmp_path, registry, **kwargs):
    return EvaluationPipeline(
        product="My Lib", description="d", product_key="my_lib", output_dir=tmp_path / "run", registry=registry, **kwargs
    )


def test_an_explicit_id_wins_over_the_registry_entry(tmp_path, registry):
    client = _RecordingClient()

    library_id = _pipeline(tmp_path, registry, context7_id="org/explicit")._library_id(client)

    assert library_id == "org/explicit"
    assert client.asked == []


def test_the_registry_key_not_the_display_name_finds_the_declared_id(tmp_path, registry):
    client = _RecordingClient()

    library_id = _pipeline(tmp_path, registry)._library_id(client)

    assert library_id == "org/declared"
    assert client.asked == []
