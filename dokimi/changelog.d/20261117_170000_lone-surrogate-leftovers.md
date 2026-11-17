### Fixed

- **A lone surrogate in an argv entry** — `process.run(['echo', 'n\ud800'])` raised `UnicodeEncodeError` from `subprocess` before the child started, so a prompt passed as an argument (`claude -p <prompt>`) with a JSON or YAML `\ud800` escape stopped the run. The entry now reaches the child with each character `os.fsencode` cannot encode as `?`, and a `😀` escape pair as its character, as on stdin; `U+DC80..U+DCFF` on POSIX stay, since `os.fsdecode` makes them of a non-UTF-8 file name. `Completed.argv` holds the text sent; an entry that needed no change is unchanged.
