### Fixed

- **`description-no-when` no longer takes a capability word for a "when" cue** — a description with no
  "when" clause lost the `warn` when it held Japanese `思いつく`/`追いつく`/`こいつ` (read as `いつ`), Chinese
  `耗时调用`/`超时调用`/`延时执行`/`当事人`, or German `Hilft beim …`/`Unterstützt beim …`. Now `いつ` counts only
  before a use verb, `时` after `耗`/`超`/`延` is no cue, and `beim` counts only with `verwenden`, `nutzen`,
  `benutzen`, `einsetzen` or `aufrufen` in the same sentence. Run `catalog check` again to see the warnings that come back.
