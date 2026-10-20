# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Static documentation metrics, one small module per score, plus run telemetry.

See `docs/contributing-metric.md` to add a metric.
"""

from dokimi.metrics.usage import UsageRecord, to_record

__all__ = ["coverage", "freshness_lite", "readability", "UsageRecord", "to_record"]
