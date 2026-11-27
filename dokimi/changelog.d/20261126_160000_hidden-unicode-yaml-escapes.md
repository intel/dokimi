### Fixed

- **`catalog check` reports hidden characters that a skill's `name` or `description` writes as YAML escapes.** A description such as `"...\\u202E"` is ASCII in `SKILL.md`, but the value an agent reads holds the bidi override, so `hidden-unicode` stayed silent. It is now a `warn` naming the front matter key and the code point. A character written literally keeps its one `line N` finding. Other front matter keys are not read.
