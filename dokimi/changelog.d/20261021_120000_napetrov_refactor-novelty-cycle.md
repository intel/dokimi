### Changed

- **Novelty coverage record moved below the gate** — `Coverage`, the exclusion classes and the coverage bases now live in `dokimi.metrics.bundle_coverage`, and `load_artifact_text` in `dokimi.novelty.artifact`. The novelty gate no longer imports the novelty track, and `novelty.bundle`, `novelty.analyze`, `novelty.report` and `gate.novelty_gate` no longer form an import cycle. Every old import path (`dokimi.novelty.bundle.Coverage`, `dokimi.novelty.analyze.load_artifact_text`, ...) still works; behaviour is unchanged.
