### Fixed

- **A rule that cannot reach p < alpha no longer raises the other rules' Holm multiplier** — in `repo compare --ledger`, a rule whose changed cases are too few for any outcome to reach p < alpha (`alpha` is the policy's, 0.05 by default) stays out of the Holm family. It keeps its raw `p`, has no `p_holm`, and is `insufficient-evidence` with a reason that says to link more cases. The `family` count and the "Holm over N result(s)" header count only the rules adjusted. Before, such a rule could turn another rule's `keep` or `review` into `insufficient-evidence`.
