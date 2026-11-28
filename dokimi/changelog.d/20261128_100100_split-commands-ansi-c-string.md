### Fixed

- **An escaped apostrophe in `$'...'` no longer hides the commands after it** — `split_commands`, the masking of a command's substitutions and its parenthesis count read `$'don\'t'` to its closing apostrophe, a backslash escaping the next character, so `printf $'don\'t'` followed by `rm g` on the next line gives two commands (#1257).
