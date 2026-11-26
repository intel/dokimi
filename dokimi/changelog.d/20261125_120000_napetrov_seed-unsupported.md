### Changed

- **a seed the route does not take is dropped, warned about once and recorded** — on an API route that refuses `seed` (Bedrock), `arms run`, `arms matrix-run`, the judge panel and `Judge` no longer refuse the run. The seed is not sent, a single warning says so, and the arm run config records `seed: null`, `seed_requested` and `seed_status: "unsupported"` (the judge output records `judge_seed_requested` and `judge_seed_status`), so the artifact does not read as seeded. A temperature the route refuses still refuses the run. CLI routes keep their refusal of any seed.
