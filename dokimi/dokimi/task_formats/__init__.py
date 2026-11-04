# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Eval-task format detection: which format is a path, and what does it hold?

Detection and a structure report only: nothing here runs or converts a task.
``dokimi eval inspect <path>`` is the command line; :func:`inspect_path` the API.
"""

from dokimi.task_formats.detect import DIR_PROBES, FILE_PROBES, PROBES, detect, inspect_path, unknown
from dokimi.task_formats.model import Fingerprint, Probe, Report

__all__ = [
    "DIR_PROBES", "FILE_PROBES", "PROBES", "Fingerprint", "Probe", "Report", "detect",
    "inspect_path", "unknown",
]
