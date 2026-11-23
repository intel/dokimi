### Fixed

- **`description-no-when` no longer takes a capability word for a "when" cue** — a description with no
  "when" clause lost the `warn` when it held Japanese `思いつく`/`追いつく`/`こいつ` (read as `いつ`), Chinese
  `耗时调用`/`超时调用`/`延时执行`/`当事人`, or German `Hilft beim …`/`Unterstützt beim …`. Now `いつ` counts only
  before a use verb later in the same sentence, at any distance, `耗时`/`超时`/`延时` directly before `调用` or `执行` is no cue (a comma or `请` between them restores it), and any other use verb after it keeps its cue, and `beim` counts only with `verwenden`, `nutzen`, `benutzen`, `einsetzen`, `aufrufen` (also their conjugations such as
  `nutzt`, `verwendest`, and `ruf … auf`, `setz … ein`) in the same sentence, a capitalised noun like `Ruf` or `Nutzen` is no verb, in either order. A German abbreviation such as `Nr.` or `Abs.` does not end the sentence before `beim`, `Nutzen Sie`, `Rufen Sie` and `Setzen Sie` after an abbreviation start a sentence, and `超时处理，请使用…` counts as a cue. Run `catalog check` again to see the warnings that come back.
