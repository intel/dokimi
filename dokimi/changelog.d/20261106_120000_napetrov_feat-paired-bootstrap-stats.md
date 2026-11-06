### Added

- **Paired, cluster-bootstrap difference between two arms** — the library function
  `dokimi.eval.paired_cluster.paired_cluster_difference` takes paired outcomes with a cluster id
  (a prompt or task id) and returns the mean difference over clusters, a seeded cluster-bootstrap
  interval (`seed`, `confidence`) and, with a `margin`, a non-inferiority verdict. Repeats of one
  prompt count as one cluster (each arm averaged per cluster), so the interval is not too narrow as a
  cell-level one would be. At least 10 clusters are needed; a zero-width interval reads
  `inconclusive`, and near the floor the interval is still anti-conservative (documented). No
  command reads it yet; it is for trigger-probe and routing results. Tracks intel/dokimi#424.
