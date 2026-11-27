### Fixed

- **`skill novelty` spans and walkthrough match what the commands do** — a Setext heading
  (`Title` over `====` or `----`) is one heading span and a run of four-space or tab indented lines
  after a blank line, outside a list, is one code span, so `analyze` and `--estimate-only` no
  longer count their sentences as extra spans (and span ids shift for artifacts that use these
  forms; an ATX heading indented up to three spaces and a Setext underline with CRLF line endings count too). The walkthrough now says that `analyze --max-usd` stopped in measurement leaves
  `novelty.partial.json`, that sole carriers are in `novelty.json` under `views.sole_carriers`
  (not in `annotated.html`), and that `probe` files carry no `run_config`.
