# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Compatibility imports for callers that still use ``dokimi.utils``."""

from dokimi.digest import GitTree, digest_text, file_contents, file_digest, git_tree, key_bytes, link_text, tree_digest
from dokimi.files import InputError, is_symlink_loop, read_json_input, read_json_input_bytes, refuse_device, retry_writable
from dokimi.frontmatter import describe_yaml_error, parse_frontmatter, parse_frontmatter_strict
from dokimi.model_names import (check_bedrock_model, model_identity, same_evaluator, same_model,
                                warn_judge_independence, warn_recorded_self_judge)
from dokimi.records import question_id

__all__ = [
    "GitTree", "InputError", "check_bedrock_model", "describe_yaml_error", "digest_text", "file_contents",
    "file_digest", "git_tree", "is_symlink_loop", "key_bytes", "link_text", "model_identity", "parse_frontmatter",
    "parse_frontmatter_strict", "question_id", "read_json_input", "read_json_input_bytes", "refuse_device",
    "retry_writable", "same_evaluator", "same_model", "tree_digest", "warn_judge_independence",
    "warn_recorded_self_judge",
]
