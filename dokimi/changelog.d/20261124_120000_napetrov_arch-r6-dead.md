### Removed

- **Two unused public names are gone** — `dokimi.runner.run.DocMetrics` (a dataclass that nothing built) and `dokimi.novelty.report.colour_of` (a one-line wrapper over `COLOURS`; import `COLOURS` from `dokimi.novelty.attribute` if you need the table). Nothing in dokimi or its tests referenced them. Library code that imported them must drop the import. Runtime behaviour of `dokimi` is unchanged.
- **`dokimi.eval.paired_cluster` is removed** — its `paired_cluster_difference` (cluster-bootstrap difference with a non-inferiority margin) had no caller in dokimi, and its only user was its own test. Code that imported it must carry its own copy of the function. Runtime behaviour of the CLI is unchanged.
