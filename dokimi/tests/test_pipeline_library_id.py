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


def test_an_mcp_descriptor_id_wins_over_the_registry_entry(tmp_path, registry):
    """``mcp:...;id=X`` names the library for that source; the registry's id does not replace it."""
    from dokimi.mcp.factory import create_doc_source_client

    client = create_doc_source_client("mcp:http=https://example.com/mcp;id=org/custom")

    library_id = _pipeline(tmp_path, registry)._library_id(client)

    assert library_id == "org/custom"


def test_an_explicit_id_wins_over_the_mcp_descriptor_id(tmp_path, registry):
    from dokimi.mcp.factory import create_doc_source_client

    client = create_doc_source_client("mcp:http=https://example.com/mcp;id=org/custom")

    library_id = _pipeline(tmp_path, registry, context7_id="org/explicit")._library_id(client)

    assert library_id == "org/explicit"


def test_the_registry_key_not_the_display_name_finds_the_declared_id(tmp_path, registry):
    client = _RecordingClient()

    library_id = _pipeline(tmp_path, registry)._library_id(client)

    assert library_id == "org/declared"
    assert client.asked == []


def test_an_unregistered_product_asks_the_client_for_its_key(tmp_path, registry):
    """A key that is not in the registry goes to the client, by its key and not its display name."""
    client = _RecordingClient()
    pipeline = EvaluationPipeline(
        product="Other Lib", description="d", product_key="other_lib", output_dir=tmp_path / "run", registry=registry
    )

    library_id = pipeline._library_id(client)

    assert library_id == "client:other_lib"
    assert client.asked == ["other_lib"]


def test_question_generation_extracts_topics_for_the_declared_library(tmp_path, registry, monkeypatch):
    """The question step asks the extractor for the registry's id, not for whatever the
    client resolves the display name to: the arms stack and this step must agree."""
    import dokimi.questions as questions

    client = _RecordingClient()
    seen = {}

    class _StopHere(Exception):
        pass

    class _Extractor:
        def __init__(self, **kwargs):
            pass

        def extract_topics(self, library_id, **kwargs):
            seen["library_id"] = library_id
            raise _StopHere

    monkeypatch.setattr("dokimi.orchestrator.pipeline.create_doc_source_client", lambda source: client)
    monkeypatch.setattr(questions, "RagasSeedExtractor", _Extractor)

    with pytest.raises(_StopHere):
        _pipeline(tmp_path, registry)._generate_questions({})

    assert seen["library_id"] == "org/declared"
