<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **litellm's banners no longer reach stdout** — a failed LLM call printed litellm's "Give
  Feedback / Get Help" block, and a provider litellm does not know its "Provider List" line, to
  stdout, once per attempt. When a judge call of `repo hits judge --json` failed and the run went
  on, they came before the JSON summary, which then did not parse. dokimi now turns them off
  (`litellm.suppress_debug_info`) wherever it loads litellm; the error itself is reported as
  before.
