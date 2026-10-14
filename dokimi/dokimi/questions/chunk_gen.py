# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Chunk-based question generator: fetches doc text and generates grounded questions.

This produces 40% of the total question budget. Questions are anchored to actual
documentation content, making them answerable only if the LLM reads the docs.

Flow:
  1. Fetch doc page via HTTP (URL from library registry)
  2. Split into semantic chunks (~2000 chars each with overlap)
  3. For each chunk: LLM generates N questions whose answers exist in the chunk
  4. Store chunk as ground_truth alongside the question
"""

from __future__ import annotations

import logging
import math
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List
from urllib.parse import urlparse
from urllib.request import urlopen, Request

from dokimi.llm import llm_call, extract_json_array
from dokimi.mcp.utils import html_to_text

logger = logging.getLogger(__name__)

# ── Prompt ───────────────────────────────────────────────────────────────────

_CHUNK_QUESTION_PROMPT = """You are generating evaluation questions for a documentation quality benchmark.

You are given a REAL excerpt from __LIBRARY__ documentation.
Generate __COUNT__ questions whose answers are EXPLICITLY present in this excerpt.

**Documentation excerpt:**
```
__CHUNK__
```

**Requirements:**
- Each question must be directly answerable from the excerpt (no external knowledge)
- Questions must mention specific APIs, parameters, behaviors, or concepts from the text
- Mix types: factual ("what does X return?"), definitional ("difference between A and B?"), procedural ("how to do specific step from excerpt?")
- Include installation/setup questions if the excerpt covers them
- Include performance/speedup questions if the excerpt mentions benchmarks or numbers

**PREFERRED — write questions as a real user would ask them:**
- "What PCA implementations does __LIBRARY__ support?" (not "What PCA implementations are mentioned?")
- "How do I configure [specific parameter] in __LIBRARY__?"
- "What does [specific API] return when [condition]?"
- "What is the difference between [A] and [B] in __LIBRARY__?"
- "Which [modes / algorithms] in __LIBRARY__ support [feature]?"
- "What speedup should I expect from __LIBRARY__ on [task]?"

**FORBIDDEN:**
- "What are the best practices..." (open-ended, opinion-based)
- Questions answerable without reading the excerpt
- Duplicate or near-duplicate questions
- Questions that reference the documentation itself: "What does the documentation say about X?", "What is mentioned in the excerpt?", "What types of X are mentioned in the documentation?" — questions must be framed from a user's perspective, NOT as document analysis tasks
- Any question containing: "mentioned in", "according to the documentation", "described in the excerpt", "stated in the docs", "in the documentation excerpt"

**Output ONLY a JSON array of strings:**
["Question 1?", "Question 2?", ...]

Generate __COUNT__ questions now:"""


# ── Chunking ─────────────────────────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 20) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme '{parsed.scheme}': only http/https allowed")
    req = Request(url, headers={"User-Agent": "dokimi/1.0"})
    # The initial scheme is restricted to http/https above. A redirect can still
    # reach ftp:, but urllib's redirect handler refuses file:, which is B310's risk.
    with urlopen(req, timeout=timeout) as resp:  # nosec B310
        raw = resp.read()
        charset = resp.headers.get_content_charset("utf-8")
    return raw.decode(charset, errors="replace")


def _strip_html(html: str) -> str:
    text = html_to_text(html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_long_para(para: str, max_chars: int) -> List[str]:
    """Split a single oversized paragraph at word/line boundaries."""
    parts = []
    lines = para.split("\n")
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_chars:
            if current:
                parts.append(current.strip())
            current = line
        else:
            current = (current + "\n" + line).strip() if current else line
    if current.strip():
        parts.append(current.strip())
    return parts or [para]


def _chunk_text(text: str, max_chars: int = 2000, overlap: int = 200) -> List[str]:
    paragraphs = re.split(r"\n{2,}", text)
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para or len(para) < 30:
            continue
        # Split oversized paragraphs (e.g. long code blocks without blank lines)
        sub_paras = _split_long_para(para, max_chars) if len(para) > max_chars else [para]
        for sp in sub_paras:
            if len(current) + len(sp) > max_chars:
                if current:
                    chunks.append(current.strip())
                # Overlap: find last word boundary
                tail = current[-overlap:] if current else ""
                boundary = tail.rfind("\n")
                tail = tail[boundary + 1:] if boundary >= 0 else tail
                current = (tail + "\n\n" + sp).strip() if tail else sp
            else:
                current = (current + "\n\n" + sp).strip() if current else sp
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ChunkQuestion:
    question: str
    chunk_text: str
    chunk_index: int
    source_url: str
    question_source: str = "chunk"


@dataclass
class ChunkQuestionResult:
    questions: List[ChunkQuestion] = field(default_factory=list)
    total_chunks: int = 0
    chunks_used: int = 0
    source_url: str = ""


# ── Generator ────────────────────────────────────────────────────────────────

class ChunkBasedQuestionGenerator:
    """Generate questions grounded in actual documentation text chunks."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        provider: str = "openai",
        questions_per_chunk: int = 2,
        max_chunks: int = 25,
        min_chunk_chars: int = 300,
    ):
        self.model = model
        self.provider = provider
        self.questions_per_chunk = questions_per_chunk
        self.max_chunks = max_chunks
        self.min_chunk_chars = min_chunk_chars

    def generate(
        self,
        library_name: str,
        doc_url: str,
        total_questions: int,
    ) -> ChunkQuestionResult:
        logger.info(f"Fetching docs for chunk-based generation: {doc_url}")
        try:
            raw = _fetch_url(doc_url)
        except Exception as exc:
            logger.error(f"Failed to fetch {doc_url}: {exc}")
            return ChunkQuestionResult(source_url=doc_url)

        if "<html" in raw[:500].lower() or "<!doctype" in raw[:200].lower():
            text = _strip_html(raw)
        else:
            text = raw

        chunks = _chunk_text(text)
        total_chunks = len(chunks)
        logger.info(f"Got {total_chunks} chunks from {doc_url}")

        needed = min(
            self.max_chunks,
            math.ceil(total_questions / self.questions_per_chunk),
            total_chunks,
        )
        if needed == 0:
            return ChunkQuestionResult(total_chunks=total_chunks, source_url=doc_url)
        # Float step ensures even coverage across the whole document
        step = total_chunks / needed
        selected = sorted(set(int(i * step) for i in range(needed)))[:needed]

        all_questions: List[ChunkQuestion] = []
        remaining = total_questions

        for idx in selected:
            if remaining <= 0:
                break
            chunk = chunks[idx]
            if len(chunk) < self.min_chunk_chars:
                continue
            count = min(self.questions_per_chunk, remaining)
            try:
                qs = self._gen_from_chunk(library_name, chunk, doc_url, idx, count)
                all_questions.extend(qs)
                remaining -= len(qs)
            except Exception as exc:
                logger.warning(f"Chunk {idx} failed: {exc}")

        return ChunkQuestionResult(
            questions=all_questions[:total_questions],
            total_chunks=total_chunks,
            chunks_used=len(selected),
            source_url=doc_url,
        )

    def _gen_from_chunk(
        self, library_name: str, chunk: str, url: str, idx: int, count: int
    ) -> List[ChunkQuestion]:
        prompt = (
            _CHUNK_QUESTION_PROMPT
            .replace("__LIBRARY__", library_name)
            .replace("__CHUNK__", textwrap.shorten(chunk, width=1800, placeholder="…"))
            .replace("__COUNT__", str(count))
        )
        raw = llm_call(prompt, model=self.model, provider=self.provider)
        questions = extract_json_array(raw)
        return [
            ChunkQuestion(question=q.strip(), chunk_text=chunk,
                         chunk_index=idx, source_url=url)
            for q in questions if isinstance(q, str) and q.strip()
        ]


def to_question_dicts(result: ChunkQuestionResult) -> List[Dict[str, Any]]:
    """Convert to standard question dict format with ground_truth_chunk."""
    return [
        {
            "question": cq.question,
            "question_source": cq.question_source,
            "chunk_index": cq.chunk_index,
            "source_url": cq.source_url,
            "ground_truth_chunk": cq.chunk_text,
        }
        for cq in result.questions
    ]
