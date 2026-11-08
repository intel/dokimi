### Fixed

- **A guidance file whose path contains a carriage return is now found** — workspace
  preparation read git's `-z` listings as text, which turns each `\r` into a newline, so
  such a path named no file: a `guidance:rev:` arm did not install it or list it in
  `guidance_latest`, `guidance:none` and `guidance:overlay` did not remove it, a `patch:`
  arm recorded a wrong path, and the base check of a skill arm counted it as shipped. The
  listings are now split on NUL before each path is decoded.
