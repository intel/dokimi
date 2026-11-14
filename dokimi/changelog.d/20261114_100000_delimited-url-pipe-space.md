### Fixed

- `catalog check --online` reads a `<...>` link destination up to its `>`: `[a](<https://acme.dev/x y>)` is requested as `https://acme.dev/x%20y` (it was cut at the space, so `/x` was probed), and a `|` inside an autolink or a `<...>` destination stays in the URL (`<https://acme.dev/a|b>` was cut at the pipe). In a table row (a line that starts with `|`) an unescaped pipe still ends the URL. (#459)
