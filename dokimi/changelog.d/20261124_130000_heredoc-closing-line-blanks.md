### Fixed

- **A heredoc whose quoted delimiter ends in a space is closed by a CRLF closing line, and the trace reader no longer raises on a no-break-space line** — `cat <<'a '` closed by `a \r` (or `a  `) was read as unclosed, so the commands after it were dropped; and an unclosed `cat <<EOF` whose body held a no-break-space `EOF` line raised an `AssertionError` in the trace reader. A closing line is now matched to a delimiter by the same blanks each reader's pattern allows around it (#930).
