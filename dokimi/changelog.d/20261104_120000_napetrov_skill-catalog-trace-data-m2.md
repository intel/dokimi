### Changed

- **Skill, catalog, trace and data commands live under their groups (#94 M2)**: `skill novelty`
  (analyze, atoms, probe, items) and `skill shorten` are under `skill`. `catalog check` replaces
  `catalog scan`, and `catalog intents` replaces `library intents`. The trace commands are under
  `trace` (`inspect`, `consistency`, `prefilter`). `data export` replaces `dataset export`, and
  `data import docling` replaces `ingest docling`. Every old path still answers, but it prints
  `moved: dokimi <new argv>` to stderr and exits 2 without running anything. The arguments are
  unchanged. Move the scripts to the new paths before the next release.

  | Before | After |
  |---|---|
  | `novelty analyze` | `skill novelty analyze` |
  | `novelty atoms` | `skill novelty atoms` |
  | `novelty probe` | `skill novelty probe` |
  | `novelty items` | `skill novelty items` |
  | `novelty shorten` | `skill shorten` |
  | `catalog scan` | `catalog check` |
  | `library intents` | `catalog intents` |
  | `context trace` | `trace inspect` |
  | `context consistency` | `trace consistency` |
  | `context prefilter` | `trace prefilter` |
  | `dataset export` | `data export` |
  | `ingest docling` | `data import docling` |
  | `context`, `dataset`, `ingest` (bare, or their help) | `trace`, `data export`, `data import` |

  `novelty routing` and `novelty view`, `library list|show`, `subjects list|show` and
  `repo tasks replay` stay where they are for now. The `.github/workflows/agent-quality.yml`
  step runs `python cli.py catalog check`; its job name is unchanged.
