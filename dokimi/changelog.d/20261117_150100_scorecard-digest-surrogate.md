<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A subject scorecard digest no longer raises for a spec or ref with a JSON high surrogate** — `ref_digest`, `question_set_hash` and `baseline_digest` encoded text with `os.fsencode`, which cannot map `\ud800`, so a descriptor or arm spec read from a file with that escape raised `UnicodeEncodeError`. They now encode with the cache keys' `key_bytes`, on every OS. On Windows, where `os.fsencode` already encoded a lone surrogate, the digest of such a ref differs from before.
