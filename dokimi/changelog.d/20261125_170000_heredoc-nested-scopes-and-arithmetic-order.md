### Fixed

- **A heredoc after a nested `${...}` in double quotes, inside two nested substitutions in arithmetic, or after an arithmetic expansion is still read as one** — in `echo "${x:-${y:-it's}}"` the nested expansion is double-quoted too, so its apostrophe is plain text and the next heredoc body is not read as commands; `$(( $(echo $(cat <<EOF ...) ... ) << 3 ))` reads the heredoc, not the shift, as the opener; and a heredoc body after `echo $((1<<3))` is skipped before its apostrophe is read, so a later `((x=1<<3))` is still arithmetic (#930).
