# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Deterministic text chunking."""

from __future__ import annotations


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 80) -> list[str]:
    """Split normalized text into overlapping fixed-size chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be in [0, chunk_size)")

    text = " ".join(text.split())
    if not text:
        return []

    chunks: list[str] = []
    step = chunk_size - overlap
    i = 0
    while i < len(text):
        chunks.append(text[i : i + chunk_size])
        if i + chunk_size >= len(text):
            break  # the tail is already in this chunk; another would only repeat the overlap
        i += step
    return chunks
