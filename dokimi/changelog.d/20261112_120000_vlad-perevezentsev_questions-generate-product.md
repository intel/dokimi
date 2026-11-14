### Fixed

- **`eval questions generate` records the product in the set it writes** — the questions.v2 file
  now has `product` set to `--product`, as the `evaluate` pipeline's `questions/<product>.json`
  does. Before, the set named no product, so `runs list` showed `-` as its subject and
  `runs list --subject <product>` did not find it. An empty `--product ""` is recorded as `""` too.
