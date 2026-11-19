# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A YAML load that refuses a repeated mapping key."""

from typing import Any


def load_unique_keys(raw: str) -> Any:
    """``yaml.safe_load`` of *raw*, but a key given twice in one mapping raises ``yaml.YAMLError``.

    PyYAML keeps the last value of a repeated key; js-yaml (gray-matter, Claude Code) refuses the file.
    The check reads the composed nodes before any construction, so a ``<<`` merge (which PyYAML
    rewrites while constructing) neither hides a repeat inside its source nor turns an explicit
    override of a merged key into one.
    """
    import yaml

    loader = yaml.SafeLoader(raw)
    try:
        root = loader.get_single_node()
        if root is None:
            return None
        stack = [root]
        seen_nodes: set = set()
        while stack:
            node = stack.pop()
            if id(node) in seen_nodes:
                continue
            seen_nodes.add(id(node))
            if isinstance(node, yaml.SequenceNode):
                stack.extend(node.value)
            elif isinstance(node, yaml.MappingNode):
                keys: set = set()
                for key_node, value_node in node.value:
                    stack.append(value_node)
                    if key_node.tag == "tag:yaml.org,2002:merge" or not isinstance(key_node, yaml.ScalarNode):
                        stack.append(key_node)
                        continue
                    key = loader.construct_object(key_node)
                    if key in keys:
                        raise yaml.constructor.ConstructorError(
                            "while constructing a mapping", node.start_mark,
                            f"found a duplicate key {key!r}", key_node.start_mark,
                        )
                    keys.add(key)
        return loader.construct_document(root)
    finally:
        loader.dispose()
