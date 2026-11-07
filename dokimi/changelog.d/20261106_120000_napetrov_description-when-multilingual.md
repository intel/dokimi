### Fixed

- **`description-no-when` reads the "when" cue of each language** — a description in Chinese, Japanese,
  Korean, Spanish, French, German, Portuguese or Russian with a native "when" clause (`当…时使用`,
  `…場合に使う`, `…경우 사용`, `…cuando…`) was flagged as having none, since only English cues counted.
  Such skills now pass. A description in a script no cue list covers (Arabic, Thai, Hebrew, …) is not
  checked; a Latin-script language with no list of its own (Polish, Dutch, Turkish, Vietnamese) is still
  checked, against the cues above. Descriptions without a cue still get the `warn`, and bare words such as `使用` (use) are not cues.
  Run `catalog check` again to see the warnings that go away.
