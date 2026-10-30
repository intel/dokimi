# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The matrix run's setup: every cell is prepared and probed before the first answer is paid for."""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from dokimi.eval.question_text import EmptyQuestionError
from dokimi.exit_codes import EXIT_FAILURE, EXIT_USAGE


def prepare_matrix_cells(args: argparse.Namespace, cells: list, specs: list[str], questions: list[dict], *,
                         apply_cell: Callable, prepare: Callable, probe: Callable) -> list[tuple]:
    """Set up and probe every matrix cell before the first answer, as the base matrix did.

    ``apply_cell(namespace, cell)``, ``prepare(namespace, specs, questions)`` and ``probe(setup)`` are
    the arms module's helpers, passed in so this module does not import it. Returns
    ``(cell, cell_args, setup)`` per cell. A cell that cannot be set up is a usage error (2); a cell
    whose probe call is refused is a model failure (1). Either way no cell has run and no output
    directory has been written yet.
    """
    prepared = []
    for cell in cells:
        cell_args = argparse.Namespace(**vars(args))
        apply_cell(cell_args, cell)
        try:
            setup = prepare(cell_args, specs, questions)
        except EmptyQuestionError:
            raise  # a bad input record, not a cell setup error: cli.main reports it (exit 2)
        except (ValueError, OSError, RecursionError) as exc:
            print(f"Error building matrix cell {cell.name!r}: {exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
        try:
            probe(setup)
        except ValueError as exc:
            # A model call refused: a runtime failure, so the matrix stops with 1 and no answer was paid for.
            print(f"Error probing matrix cell {cell.name!r}: {exc}", file=sys.stderr)
            sys.exit(EXIT_FAILURE)
        prepared.append((cell, cell_args, setup))
    return prepared
