# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Two saved all-vs-all routing runs compared query by query (#710).

Reads what ``catalog routing`` saved: ``result.lexical.per_query`` or, for the model arm,
``result.model.noise.per_query``. Rows are paired by ``(gold, query)``; the queries that are
right in one run only are tested with the exact two-sided sign test. No model is called.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from dokimi.eval.binary_stats import exact_binomial_two_sided

from .routing import RoutingInputError

ARMS = ("lexical", "model")

#: Why a row is left out of every figure, as the report names it.
NO_CONTENT_WORD = "no_content_word"
NO_ANSWER = "no_answered_call"
NO_MODAL_PICK = "no_unique_modal_pick"


def load_run(path: str, arm: str) -> dict:
    """The saved all-vs-all run at ``path``, checked to hold the per-query rows of ``arm``.

    Raises:
        RoutingInputError: The file cannot be read, is not JSON, is not an all-vs-all result, has
            no ``result`` object, or lacks the arm or its ``per_query`` rows (a file saved before
            the lexical arm kept them has none); the message names the file and the cause.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        raise RoutingInputError(f"{path}: cannot read: {exc}") from None
    except json.JSONDecodeError as exc:
        raise RoutingInputError(f"{path}: not JSON: {exc}") from None
    except RecursionError:
        raise RoutingInputError(f"{path}: nested too deeply to read") from None
    mode = data.get("mode") if isinstance(data, dict) else None
    if mode != "all_vs_all" or not isinstance(data.get("result"), dict):
        raise RoutingInputError(f"{path}: not an all-vs-all routing result (mode {mode!r}): "
                                "`catalog routing ROOT --queries-by-skill FILE --out FILE` saves one")
    section = data["result"].get(arm)
    if arm == "model":
        if not isinstance(section, dict):
            raise RoutingInputError(f"{path}: has no model arm (the run was made without --model)")
        section = section.get("noise")
    rows = section.get("per_query") if isinstance(section, dict) else None
    if not isinstance(rows, list):
        where = "model.noise.per_query" if arm == "model" else "lexical.per_query"
        raise RoutingInputError(f"{path}: no `{where}` rows: saved before the arm kept a row per "
                                "query (#711); run `catalog routing` again")
    if not all(isinstance(r, dict) and isinstance(r.get("query"), str)
               and isinstance(r.get("gold"), str) for r in rows):
        raise RoutingInputError(f"{path}: a per_query row has no string `query` and `gold`")
    return {"path": path, "rows": rows, "model": (data["result"].get("model") or {}).get("model")}


def _entry(row: dict, arm: str) -> dict:
    """One row as ``{pick, unscored, acceptable}``; ``unscored`` is the reason, else ``None``."""
    acceptable = row.get("acceptable") if isinstance(row.get("acceptable"), list) else None
    if arm == "lexical":
        if row.get("rank") is None:
            return {"pick": None, "unscored": NO_CONTENT_WORD, "acceptable": acceptable}
        return {"pick": row.get("picked"), "unscored": None, "acceptable": acceptable}
    picks = row.get("picks") or []
    if not picks:
        return {"pick": None, "unscored": NO_ANSWER, "acceptable": acceptable}
    (pick, count), *rest = Counter(picks).most_common()
    if rest and rest[0][1] == count:
        return {"pick": None, "unscored": NO_MODAL_PICK, "acceptable": acceptable}
    return {"pick": pick, "unscored": None, "acceptable": acceptable}


def _keyed(run: dict, arm: str) -> dict[tuple[str, str, int], dict]:
    """Entries by ``(gold, query, k)``: ``k`` numbers a query listed twice for one gold."""
    seen: Counter = Counter()
    keyed: dict[tuple[str, str, int], dict] = {}
    for row in run["rows"]:
        base = (row["gold"], row["query"])
        keyed[(*base, seen[base])] = _entry(row, arm)
        seen[base] += 1
    return keyed


def _alternatives(base: dict, other: dict) -> list[str] | None:
    """The acceptable names both runs give a pair, or ``None`` when the gold alone decides."""
    names_base, names_other = base["acceptable"], other["acceptable"]
    if names_base and names_other and set(names_base) == set(names_other):
        return names_base
    return None


def compare_runs(base: dict, other: dict, arm: str = "lexical") -> dict:
    """Compare two runs from :func:`load_run` on the queries both made.

    A pair is right when the pick is the gold, or, when both runs name the same ``acceptable``
    for it, the gold or one of those names. When only one run names alternatives, or the two
    differ, the pair is scored on the gold alone, so both runs always use one key; the count of
    such pairs is ``acceptable_mismatch``. A pair either run leaves unscored (no content word, no
    answered call, or, with repeats, no unique most common pick) is left out and counted under
    ``unscored``. ``p_value`` is the exact two-sided sign test over the pairs right in one run
    only (1.0 with none).
    """
    keyed_base, keyed_other = _keyed(base, arm), _keyed(other, arm)
    paired = sorted(set(keyed_base) & set(keyed_other))
    only_base = sorted(set(keyed_base) - set(keyed_other))
    only_other = sorted(set(keyed_other) - set(keyed_base))
    unscored: Counter = Counter()
    scored: list[tuple[tuple[str, str, int], dict, dict]] = []
    for key in paired:
        a, b = keyed_base[key], keyed_other[key]
        reason = a["unscored"] or b["unscored"]
        if reason:
            unscored[reason] += 1
        else:
            scored.append((key, a, b))
    right = {"base": 0, "other": 0, "base_gold": 0, "other_gold": 0}
    only = {"base": 0, "other": 0}
    changed: list[dict] = []
    used = mismatch = 0
    for (gold, query, _k), a, b in scored:
        names = _alternatives(a, b)
        used += names is not None
        mismatch += names is None and bool(a["acceptable"] or b["acceptable"])
        ok_a = a["pick"] == gold or (names is not None and a["pick"] in names)
        ok_b = b["pick"] == gold or (names is not None and b["pick"] in names)
        right["base"] += ok_a
        right["other"] += ok_b
        right["base_gold"] += a["pick"] == gold
        right["other_gold"] += b["pick"] == gold
        only["base"] += ok_a and not ok_b
        only["other"] += ok_b and not ok_a
        if a["pick"] != b["pick"]:
            changed.append({"gold": gold, "query": query, "base": a["pick"], "other": b["pick"]})
    n_discordant = only["base"] + only["other"]
    p_value = exact_binomial_two_sided(only["base"], n_discordant) if n_discordant else 1.0
    return {
        "arm": arm,
        "base": {"path": base["path"], "model": base["model"]},
        "other": {"path": other["path"], "model": other["model"]},
        "n_paired": len(scored),
        "scoring": "gold_or_acceptable" if used else "gold",
        "acceptable_pairs": used,
        "acceptable_mismatch": mismatch,
        "n_correct": {"base": right["base"], "other": right["other"]},
        "n_correct_gold": {"base": right["base_gold"], "other": right["other_gold"]},
        "discordant": {"only_base": only["base"], "only_other": only["other"],
                       "n_discordant": n_discordant},
        "p_value": p_value,
        "changed": changed,
        "unscored": dict(sorted(unscored.items())),
        "only_in_base": _listed(only_base),
        "only_in_other": _listed(only_other),
    }


def _listed(keys: list[tuple[str, str, int]]) -> dict:
    """Count and up to three examples of the queries one run has and the other lacks."""
    return {"count": len(keys),
            "examples": [{"gold": gold, "query": query} for gold, query, _k in keys[:3]]}
