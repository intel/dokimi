### Fixed

- **A heredoc after `"${x:-it's}"`, inside `$(( $(cat <<EOF ...) ))`, or after a delimiter such as `<<"${"` is still cut from the commands** — the apostrophe in a double-quoted `${...}` is plain text, a `<<` inside a `$(...)` that sits in `$((...))` is a heredoc whose body is skipped before the arithmetic is balanced, and a delimiter word is literal text, not the start of an expansion (#930).
