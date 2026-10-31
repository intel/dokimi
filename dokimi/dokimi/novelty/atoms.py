# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Extract knowledge atoms — checkable statements with span provenance.

An atom is the unit of *measurement*: one statement the artifact asserts, phrased as a
question with an expected answer, so it can be asked of a model with and without the
artifact in context. Everything downstream (the paired 2×2, the five categories, the
retention oracle, the shortener) is defined per atom.

Two properties are enforced here rather than hoped for:

* **Provenance.** Every atom names the span ids that assert it. An atom with no span
  is dropped — it cannot be attributed, coloured, or protected, so it would only
  inflate counts.
* **Faithfulness, separately from recall.** :func:`verify_atoms` re-reads each atom
  against its own spans and labels it ``supported`` / ``distorted`` / ``unsupported``.
  That check says nothing about what the extractor *missed*; extractor recall is
  measured across extractors in skill-novelty-evidence.md §10.7, not by this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .client import LLMClient, accepting
from .spans import Span
from dokimi.exit_codes import usage_exit

ATOM_KINDS = ("proposition", "edge", "motif", "contract", "example")
ATOM_TAGS = (
    "local", "public", "temporal", "version_bound", "negative_constraint",
    "rare_critical", "procedural", "routing", "evidence",
)

_EXTRACT_SYSTEM = (
    "You extract checkable knowledge from technical documents. You never invent facts "
    "that the text does not assert, and you never soften a claim the text makes."
)

_EXTRACT_PROMPT = """\
Below are numbered spans from one document. Extract the checkable knowledge each span
asserts, as JSON.

Rules:
- One atom = one statement that could be right or wrong. Split compound claims.
- Keep exact values, versions, flags, thresholds and negations verbatim. They are the
  point; a paraphrase that drops "not" or rounds a number is a wrong atom.
- `question` must be answerable without seeing this document, and must not mention it.
- `answer` is the shortest complete correct answer the span supports.
- `span_ids` lists every span that asserts the atom (usually one).
- Skip pure navigation, formatting and restated headings. Those are not knowledge.
- kind: one of {kinds}
- tags: any of {tags}

Return ONLY a JSON array:
[{{"statement": "...", "question": "...", "answer": "...", "kind": "proposition",
   "tags": ["version_bound"], "span_ids": ["s0007"]}}]

SPANS:
{spans}
"""

_VERIFY_PROMPT = """\
Does the source text below support the statement, exactly as stated?

STATEMENT: {statement}

SOURCE:
{source}

Answer with JSON only:
{{"verdict": "supported" | "distorted" | "unsupported", "why": "<one clause>"}}

- "supported": the source asserts it, with the same qualifiers, numbers and polarity.
- "distorted": the source says something close but the statement changes a qualifier,
  a number, a version, or the polarity.
- "unsupported": the source does not assert it at all.
"""


@dataclass
class Atom:
    """One checkable statement drawn from the artifact.

    Attributes:
        id: Stable ``a<hash>`` id derived from the statement, so re-running the
            extractor on unchanged text keeps atom ids comparable.
        statement: The claim, as the artifact makes it.
        question: A probe question answerable without the artifact.
        answer: The expected answer the artifact supports.
        kind: One of :data:`ATOM_KINDS`.
        tags: Any of :data:`ATOM_TAGS`.
        span_ids: Spans that assert this atom (never empty).
        faithfulness: ``supported`` / ``distorted`` / ``unsupported`` / ``unchecked``.
    """

    statement: str
    question: str
    answer: str
    kind: str = "proposition"
    tags: tuple[str, ...] = ()
    span_ids: tuple[str, ...] = ()
    faithfulness: str = "unchecked"
    id: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            digest = hashlib.sha256(self.statement.strip().encode("utf-8")).hexdigest()[:10]
            self.id = f"a{digest}"

    @property
    def protected(self) -> bool:
        """True for atoms whose value is conditional on a failure."""
        return bool({"negative_constraint", "version_bound", "rare_critical"} & set(self.tags))

    def as_dict(self) -> dict:
        """Serializable form."""
        return {
            "id": self.id,
            "statement": self.statement,
            "question": self.question,
            "answer": self.answer,
            "kind": self.kind,
            "tags": list(self.tags),
            "span_ids": list(self.span_ids),
            "faithfulness": self.faithfulness,
            **({"meta": self.meta} if self.meta else {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Atom":
        """Rebuild an atom from :meth:`as_dict` output."""
        return cls(
            statement=data["statement"],
            question=data.get("question", ""),
            answer=data.get("answer", ""),
            kind=data.get("kind", "proposition"),
            tags=tuple(data.get("tags", ())),
            span_ids=tuple(data.get("span_ids", ())),
            faithfulness=data.get("faithfulness", "unchecked"),
            id=data.get("id", ""),
            meta=data.get("meta", {}),
        )


class ExtractionError(RuntimeError):
    """Every extraction batch failed, so there is nothing to measure."""


def render_spans(spans: list[Span]) -> str:
    """Render spans for the extractor prompt, ids included."""
    return "\n".join(f"[{span.id}] ({span.kind}) {span.text}" for span in spans)


_EMPTY_REPLY = "the extractor returned an empty reply"
# Verdicts verify_atoms can record; anything else is `unchecked`.
_FAITHFULNESS = ("supported", "distorted", "unsupported")


class _NoUsableRows(ValueError):
    """The reply parsed and has rows, but none is an atom of this document."""


@dataclass
class ExtractionReport:
    """What extraction could not read, so a partial atom set is never quoted as whole.

    Attributes:
        unread_span_ids: Spans no extraction call ever read, in document order. No atom
            can come from them, so they are *unread*, not "carrying nothing": every
            consumer (the span summary, dead weight, span labels, the shortener, the gate)
            leaves them out of what it measures. The spans themselves are marked
            ``unread`` in place.
        unread_tokens: Token cost of those spans.
        unparseable_span_ids: The subset whose reply arrived but did not parse even one
            span per call, which is what a too-low output ceiling looks like.
        empty_span_ids: The subset whose call returned no content. A reasoning model that
            spends its output ceiling before answering does that (see
            :class:`~dokimi.novelty.client.LLMClient`), so these also need a higher
            ``--max-tokens``. Not split: half a batch comes back empty too. Kept for the
            warning; the artifact records only the totals.
        unusable_span_ids: The subset whose reply parsed and had rows, none of them an
            atom (wrong keys, strings, span ids that are not this document's). Not "this
            text asserts nothing": an empty array is that, and is a result. Kept for the
            warning and, unlike the empty replies, recorded in the artifact.
        retried_batches: Batches whose reply did not parse, or had no usable atom, and
            were split and re-asked.
        unusable_retries: How many of those splits were for unusable rows. Only the
            cut-off ones are cured by ``--max-tokens``. Kept for the warning.
        calls: Extraction calls made, splits included, so the cost of re-asking is visible.
        batches: Calls the same run would make with no splitting: a batch counts once it
            is asked, and its halves count at its level, so a run the cap stopped early
            counts only the batches it reached.
        parse_error: The last parse failure on a span that stayed unread.
        call_error: The last failed call on a span that stayed unread. Errors that
            splitting recovered from are not recorded: they did not lose anything, and
            quoting one would blame the wrong cause.
        reached: How far through the input a capped atom set reached (see
            :func:`_cap_reach`); ``None`` when the cap did not bind.
    """

    unread_span_ids: list[str] = field(default_factory=list)
    unread_tokens: int = 0
    unparseable_span_ids: list[str] = field(default_factory=list)
    empty_span_ids: list[str] = field(default_factory=list)
    unusable_span_ids: list[str] = field(default_factory=list)
    retried_batches: int = 0
    unusable_retries: int = 0
    calls: int = 0
    batches: int = 0
    parse_error: str = ""
    call_error: str = ""
    unusable_error: str = ""
    reached: dict | None = None

    @property
    def last_error(self) -> str:
        """The error behind the unread spans: a failed call first, since it is not a ceiling."""
        if self.call_error or self.parse_error or self.unusable_error:
            return self.call_error or self.parse_error or self.unusable_error
        return _EMPTY_REPLY if self.empty_span_ids else ""

    def warnings(self, n_spans: int) -> list[str]:
        """Artifact warnings: spans left unread, and the calls re-asking cost."""
        out: list[str] = []
        if self.unread_span_ids:
            unparseable = len(self.unparseable_span_ids)
            empty = len(self.empty_span_ids)
            unusable = len(self.unusable_span_ids)
            failed = len(self.unread_span_ids) - unparseable - empty - unusable
            causes = []
            if unparseable:
                causes.append(
                    f"{unparseable} replied but did not parse even one span per call; a "
                    f"reply cut off mid-JSON means the output ceiling is too low for one "
                    f"span, so raise --max-tokens (last parse error: {self.parse_error})"
                )
            if empty:
                causes.append(
                    f"{empty} got an empty reply, which a reasoning model returns when it "
                    f"spends the output ceiling before answering, so raise --max-tokens "
                    f"(empty replies are not split: half a batch comes back empty too)"
                )
            if unusable:
                causes.append(
                    f"{unusable} replied with rows but none usable (no statement, question "
                    f"and span id of this document), which is not an empty result "
                    f"(last error: {self.unusable_error})"
                )
            if failed:
                causes.append(
                    f"{failed} were lost to calls that failed, which are not split and "
                    f"re-asked (last error: {self.call_error})"
                )
            out.append(
                f"atom extraction could not read {len(self.unread_span_ids)} of {n_spans} "
                f"spans ({self.unread_tokens} tokens): {'; '.join(causes)}. Unread spans "
                f"are left out of `frac_carrying_nothing` and dead weight, labelled "
                f"`unread`, kept by every shortening candidate, and listed in "
                f"`extraction.unread_span_ids`; whatever they assert is missing from "
                f"every rate"
            )
        if self.retried_batches:
            cut = self.retried_batches - self.unusable_retries
            if not cut:
                label = "unusable"
            elif self.unusable_retries:
                label = "unparseable or unusable"
            else:
                label = "unparseable"
            advice = "; a higher --max-tokens avoids the extra calls" if cut else ""
            out.append(
                f"atom extraction re-asked {self.retried_batches} {label} batches as "
                f"halves: {self.calls} extraction calls where {self.batches} would do "
                f"without splitting{advice}"
            )
        return out

    def as_dict(self) -> dict:
        """Serializable form: the ``extraction`` block of ``atoms.json`` and ``novelty.v1``."""
        return {
            "unread_span_ids": list(self.unread_span_ids),
            "unread_tokens": self.unread_tokens,
            "unparseable_span_ids": list(self.unparseable_span_ids),
            "unusable_span_ids": list(self.unusable_span_ids),
            "retried_batches": self.retried_batches,
            "calls": self.calls,
            "batches": self.batches,
            "last_error": self.last_error,
            "reached": self.reached,
        }


def extract_atoms(
    spans: list[Span],
    *,
    client: LLMClient,
    batch_spans: int = 25,
    cap: int | None = None,
    report: ExtractionReport | None = None,
) -> list[Atom]:
    """Extract atoms from spans, batched, keeping only atoms with valid provenance.

    A batch whose reply does not parse, or parses to rows none of which is a usable atom
    (see :func:`_atom_from_row`), is split in half and re-asked, down to one span
    per call; the halves go back to the front of the queue, so atoms keep document order
    (which is what ``cap`` cuts on). The common cause is an output ceiling: a provider
    that defaults to 4096 output tokens truncates a 25-span reply mid-array, and a
    truncated array does not parse. Splitting recovers without knowing the ceiling, at a
    cost of up to 2n-1 calls for an n-span batch that never parses; ``report.calls``
    counts them, against ``report.batches`` for the batches the run reached. A call that
    raises (transport, auth) or returns nothing is not split, because re-asking smaller
    would only multiply it.

    Args:
        spans: Spans from :func:`~dokimi.novelty.spans.segment`.
        client: Extractor client (must not be the judge — see :mod:`.measure`).
        batch_spans: Spans per extraction call. Batching is what keeps extraction
            affordable; too large and the model starts skipping spans.
        cap: Stop after this many atoms. Recorded in the artifact as the sampling
            rule, so a capped run is never quoted as a full sweep.
        report: Filled in place with what could not be read and what re-asking cost,
            and how far a binding cap reached (``reached``). Spans no call could read
            are also marked ``unread`` in place.

    Returns:
        Atoms in extraction order, de-duplicated by id.
    """
    from dokimi.llm import extract_json_array

    report = report if report is not None else ExtractionReport()
    valid_ids = {span.id for span in spans}

    def parse(reply: str) -> list:
        rows = extract_json_array(reply)
        if rows and not any(_atom_from_row(row, valid_ids) for row in rows):
            # `[]` says the spans assert nothing; rows that are not atoms say nothing.
            raise _NoUsableRows(f"{len(rows)} rows, none an atom of this document")
        return rows

    usable = accepting(parse)  # so the response cache never keeps a bad reply
    for span in spans:
        span.unread = False
    seen: set[str] = set()
    sent: set[str] = set()  # span ids some call was shown, which bound the reach
    atoms: list[Atom] = []
    lost: Exception | None = None
    # (spans, is a half): a half was counted in ``batches`` with the batch it came from.
    pending = [
        (spans[start:start + batch_spans], False)
        for start in range(0, len(spans), batch_spans)
    ]
    while pending:
        if cap is not None and len(atoms) >= cap:
            break
        batch, is_half = pending.pop(0)
        sent.update(span.id for span in batch)
        if not is_half:
            report.batches += 1
        prompt = _EXTRACT_PROMPT.format(
            kinds=", ".join(ATOM_KINDS), tags=", ".join(ATOM_TAGS), spans=render_spans(batch)
        )
        unparseable = False
        try:
            report.calls += 1
            reply = client.text(prompt, system=_EXTRACT_SYSTEM, accept=usable)
            if not reply.strip():
                # Its own cause, not a failed call: the fix is a higher output ceiling.
                lost = ValueError(
                    f"{_EMPTY_REPLY}; a reasoning model returns one when it spends the "
                    "output ceiling before answering, so raise --max-tokens"
                )
                report.empty_span_ids.extend(span.id for span in batch)
                _mark_unread(batch, report)
                continue
            unparseable = True
            rows = parse(reply)
        except Exception as exc:
            if unparseable and len(batch) > 1:
                # Unparseable, most often truncated: re-ask the two halves, in place,
                # so atoms keep document order. Not recorded as an error: if the halves
                # parse, nothing was lost.
                half = len(batch) // 2
                pending[:0] = [(batch[:half], True), (batch[half:], True)]
                report.retried_batches += 1
                report.unusable_retries += isinstance(exc, _NoUsableRows)
                continue
            # One lost batch must not lose the others; the report names it.
            lost = exc
            error = f"{type(exc).__name__}: {str(exc)[:120]}"
            if isinstance(exc, _NoUsableRows):
                report.unusable_error = str(exc)[:120]  # no private class name
                report.unusable_span_ids.extend(span.id for span in batch)
            elif unparseable:
                report.parse_error = error
                report.unparseable_span_ids.extend(span.id for span in batch)
            else:
                report.call_error = error
            _mark_unread(batch, report)
            continue
        for row in rows:
            atom = _atom_from_row(row, valid_ids)
            if atom is None or atom.id in seen:
                continue
            seen.add(atom.id)
            atoms.append(atom)
            if cap is not None and len(atoms) >= cap:
                break
    if not atoms and lost is not None and all(span.unread for span in spans):
        # Only when nothing was read: spans that were read and asserted nothing are a
        # result, and a recovered parse error is not a failure.
        raise ExtractionError(f"no atoms: every extraction call failed ({lost})") from lost
    report.reached = _cap_reach(spans, atoms, cap, sent)
    return atoms


def _cap_reach(spans: list[Span], atoms: list[Atom], cap: int | None,
               sent: set[str]) -> dict | None:
    """How far through the input (``spans``) a capped atom set reached; ``None`` under the cap.

    Extraction walks the spans in document order and stops at the cap, so a capped set
    covers the input up to the span it stopped at, not a sample of it. The input is the text segmented,
    not the artifact: a bundled file it left out counts in no span. The reach is the last span any
    kept atom cites, not the last batch sent: a batch the cap cut mid-reply was sent whole,
    but its later spans gave no atom. Only the ``sent`` spans count: an atom may cite a span
    of the document its call was never shown, and that made-up id must not move the reach
    (an atom set citing nothing sent reaches the last span sent). ``spans`` counts the spans
    up to and including it, ``of`` all of them, and ``tokens_share`` is their share of the
    span tokens.
    """
    if cap is None or not atoms or len(atoms) < cap:
        return None
    order = {span.id: index for index, span in enumerate(spans) if span.id in sent}
    last = max((order[span_id] for atom in atoms for span_id in atom.span_ids if span_id in order),
               default=max(order.values()))
    reached = sum(span.tokens for span in spans[:last + 1])
    total = sum(span.tokens for span in spans)
    # Floored in integers, as floored_percent prints it: a cap one span short of the end must not round to 1.0
    share = reached * 10000 // total
    return {
        "last_span": spans[last].id,
        "spans": last + 1,
        "of": len(spans),
        "tokens_share": (max(share, 1) if reached else 0) / 10000,
    }


def _mark_unread(batch: list[Span], report: ExtractionReport) -> None:
    for span in batch:
        span.unread = True
    report.unread_span_ids.extend(span.id for span in batch)
    report.unread_tokens += sum(span.tokens for span in batch)


def _atom_from_row(row: object, valid_ids: set[str]) -> Atom | None:
    if not isinstance(row, dict):
        return None
    statement = str(row.get("statement") or "").strip()
    question = str(row.get("question") or "").strip()
    if not statement or not question:
        return None
    span_ids = tuple(str(s) for s in row.get("span_ids", ()) if str(s) in valid_ids)
    if not span_ids:
        # No provenance means the atom cannot be attributed to text, protected, or
        # coloured. Counting it would inflate every per-artifact number.
        return None
    kind = str(row.get("kind") or "proposition")
    tags = tuple(str(t) for t in row.get("tags", ()) if str(t) in ATOM_TAGS)
    return Atom(
        statement=statement,
        question=question,
        answer=str(row.get("answer") or "").strip(),
        kind=kind if kind in ATOM_KINDS else "proposition",
        tags=tags,
        span_ids=span_ids,
    )


def verify_atoms(
    atoms: list[Atom],
    spans: list[Span],
    *,
    client: LLMClient,
    max_atoms: int | None = None,
) -> dict[str, int]:
    """Label each atom's faithfulness against its own spans (in place).

    Returns a tally of the verdicts. This bounds *distortion*, not *omission*: an atom
    set can be fully ``supported`` and still miss half the document.
    """
    by_id = {span.id: span for span in spans}
    tally = {"supported": 0, "distorted": 0, "unsupported": 0, "unchecked": 0}
    for index, atom in enumerate(atoms):
        if max_atoms is not None and index >= max_atoms:
            tally["unchecked"] += 1
            continue
        source = "\n".join(by_id[s].text for s in atom.span_ids if s in by_id)
        try:
            verdict = str(client.json_object(
                _VERIFY_PROMPT.format(statement=atom.statement, source=source),
                usable=_names_a_faithfulness_verdict,
            ).get("verdict", "")).strip()
        except Exception:
            verdict = ""
        atom.faithfulness = verdict if verdict in tally and verdict != "unchecked" else "unchecked"
        tally[atom.faithfulness] += 1
    return tally


def _names_a_faithfulness_verdict(reply: dict) -> bool:
    """Cache only a reply that names one; ``{"verdict": null}`` replayed reads ``unchecked``."""
    return reply.get("verdict") in _FAITHFULNESS


def save_atoms(
    atoms: list[Atom], spans: list[Span], path, *,
    extractor: str | None = None, cap: int | None = None, bundle: dict | None = None,
    extraction: ExtractionReport | None = None,
) -> None:
    """Write atoms plus their spans as one JSON file (input to ``novelty probe``).

    ``bundle`` records *which files of the artifact* these atoms came from. Without it a
    later ``novelty probe`` cannot know whether to inject the entry point or the whole
    bundle, and would measure atoms extracted from a reference file against a context
    that never contained it -- every such atom scoring `still_missing` for the wrong
    reason.

    ``extraction`` records the spans no extraction call could read. It is written even
    without a report, from the spans' own ``unread`` marks, because every later stage
    (``probe``, ``shorten``, ``view``) reads only this file: without the record, a
    ``shorten --out`` would cut those spans as text that carries nothing.
    """
    from pathlib import Path

    record = extraction.as_dict() if extraction is not None else {}
    record["unread_span_ids"] = [span.id for span in spans if span.unread]
    record["unread_tokens"] = sum(span.tokens for span in spans if span.unread)
    payload = {
        "extractor": extractor,
        "cap": cap,
        "bundle": bundle or {},
        "extraction": record,
        "atoms": [atom.as_dict() for atom in atoms],
        "spans": [span.as_dict() for span in spans],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_atoms(path) -> tuple[list[Atom], list[Span]]:
    """Read back what :func:`save_atoms` wrote, with unread spans marked ``unread``.

    An ``atoms.json`` written before the ``extraction`` record existed marks nothing; it
    cannot say whether a span went unread.
    """
    from pathlib import Path

    return _atoms_from_record(json.loads(Path(path).read_text(encoding="utf-8")))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


_STR = (lambda v: isinstance(v, str), "a string")
_INT = (_is_int, "an integer")
_STR_LIST = (_is_str_list, "a list of strings")
_OBJ = (lambda v: isinstance(v, dict), "an object")
_STR_OR_NULL = (lambda v: v is None or isinstance(v, str), "a string or null")

# Every field a reader takes from a row: (type check, what it should be), and the fields
# a row must have.
_SPAN_FIELDS = {"id": _STR, "kind": _STR, "text": _STR, "start": _INT, "end": _INT,
                "heading_path": _STR_LIST, "protection": _STR_LIST, "atom_ids": _STR_LIST}
_SPAN_REQUIRED = ("id", "kind", "text", "start", "end")
_ATOM_FIELDS = {"id": _STR, "statement": _STR, "question": _STR, "answer": _STR,
                "kind": _STR, "faithfulness": _STR, "tags": _STR_LIST,
                "span_ids": _STR_LIST, "meta": _OBJ}
_ATOM_REQUIRED = ("statement",)
_FILE_FIELDS = {"path": _STR, "role": _STR, "start": _INT, "end": _INT, "words": _INT,
                "sha256": _STR_OR_NULL}
_FILE_REQUIRED = ("path",)
_EXCLUDED_FIELDS = {"path": _STR, "role": _STR, "reason": _STR, "words": _INT}


def _check_rows(where: str, rows, fields: dict, required: tuple = ()) -> None:
    """Raise ValueError unless ``rows`` is a list of objects whose fields have their types."""
    if not (isinstance(rows, list) and all(isinstance(row, dict) for row in rows)):
        raise ValueError(f"'{where}' is not a list of objects")
    for row in rows:
        for key in required:
            if key not in row:
                raise ValueError(f"'{where}' has a row without '{key}'")
        for key, (ok, expected) in fields.items():
            if key in row and not ok(row[key]):
                raise ValueError(f"'{where}' has a row whose '{key}' is not {expected}")


def _check_record_fields(data: dict) -> None:
    """Raise ValueError unless the spans, atoms and extraction record have their types."""
    _check_rows("spans", data.get("spans", []), _SPAN_FIELDS, _SPAN_REQUIRED)
    _check_rows("atoms", data.get("atoms", []), _ATOM_FIELDS, _ATOM_REQUIRED)
    extraction = data.get("extraction")
    if extraction is not None and not isinstance(extraction, dict):
        raise ValueError("'extraction' is not an object")
    unread = (extraction or {}).get("unread_span_ids")
    if unread is not None and not _is_str_list(unread):
        raise ValueError("'extraction.unread_span_ids' is not a list of strings")


def _atoms_from_record(data) -> tuple[list[Atom], list[Span]]:
    """The atoms and spans of a parsed atoms record; ValueError when it is not one."""
    if not isinstance(data, dict):
        raise ValueError(f"the file holds {type(data).__name__}, not an object")
    _check_record_fields(data)
    atoms = [Atom.from_dict(row) for row in data.get("atoms", [])]
    unread = set((data.get("extraction") or {}).get("unread_span_ids") or ())
    spans = [
        Span(
            id=row["id"], kind=row["kind"], text=row["text"], start=row["start"], end=row["end"],
            heading_path=tuple(row.get("heading_path", ())),
            protection=tuple(row.get("protection", ())),
            atom_ids=list(row.get("atom_ids", [])),
            unread=row["id"] in unread,
        )
        for row in data.get("spans", [])
    ]
    return atoms, spans


def _check_bundle_shape(bundle) -> None:
    """Raise ValueError unless a ``bundle`` record has the types its readers rely on."""
    if bundle is None:
        return
    if not isinstance(bundle, dict):
        raise ValueError("'bundle' is not an object")
    for key in ("max_file_words", "max_total_words", "n_files", "resource_words_measured"):
        if bundle.get(key) is not None and not _is_int(bundle[key]):
            raise ValueError(f"'bundle.{key}' is not an integer")
    if bundle.get("artifact_name") is not None and not isinstance(bundle["artifact_name"], str):
        raise ValueError("'bundle.artifact_name' is not a string")
    if bundle.get("resource_roles") is not None and not _is_str_list(bundle["resource_roles"]):
        raise ValueError("'bundle.resource_roles' is not a list of strings")
    if bundle.get("files") is not None:
        _check_rows("bundle.files", bundle["files"], _FILE_FIELDS, _FILE_REQUIRED)
    if bundle.get("excluded") is not None:
        _check_rows("bundle.excluded", bundle["excluded"], _EXCLUDED_FIELDS)


def load_atoms_or_exit(path) -> tuple[list[Atom], list[Span], dict]:
    """:func:`load_atoms` plus the parsed record, for a command.

    Exits with the file named when it cannot be read or is not an atom set, including a
    ``bundle`` whose fields do not have the types its readers use.
    """
    from pathlib import Path

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        atoms, spans = _atoms_from_record(data)
        _check_bundle_shape(data.get("bundle"))
    except (OSError, ValueError, RecursionError) as exc:
        usage_exit(f"cannot read atoms from {path}: {exc}")
    return atoms, spans, data
