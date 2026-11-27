### Fixed

- **A `<<` in a `${...}` expansion, or after a nested quoted substitution inside `$((...))`, no longer hides the commands after it** — in `echo ${x:-<<EOF}` the `<<` is text of the expansion, not a redirection, and in `echo $(( $(echo "$(echo "))")") << 3 ))` the quotes of the inner `$(...)` are its own, so the `))` in them does not close the arithmetic before the shift (#930).
