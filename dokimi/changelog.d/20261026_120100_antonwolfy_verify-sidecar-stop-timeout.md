### Fixed

- **A verifier sidecar whose removal times out no longer replaces the verdict** — on a
  `docker-*` run with an allowlisted verifier, a `docker rm` of the `-verify` sidecar that
  timed out after `test.sh` turned the computed reward into a TIMEOUT cell (exit 124, reward
  0), and the sandbox teardown did not know the sidecar, so it stayed on the run's network.
  The verifier's verdict and `metrics.network.verify_egress` now stand, the timeout goes to
  `run.log`, and the teardown retries the removal before it drops the network.
  The same holds for a `-verify` sidecar whose start failed and whose removal then timed
  out, and the next run with the same id removes a `-verify` sidecar a crashed run left,
  before it creates the network.
