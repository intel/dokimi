# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Read a SCIP code index that someone else built. dokimi never runs an indexer.

``load(path, root_prefix=...)`` gives a :class:`CodeIndex`; see :mod:`.index` for the queries and
:mod:`.scip` for the decoded records. The protobuf wire format is read here with the standard library only.
"""

from dokimi.repos.code_index.index import CodeIndex, load
from dokimi.repos.code_index.scip import Occurrence

__all__ = ["CodeIndex", "Occurrence", "load"]
