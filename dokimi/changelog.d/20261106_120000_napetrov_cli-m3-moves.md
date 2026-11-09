### Changed

- **Catalog listings, the replay task builder and one-skill checks move under their groups (#94 M3)**:
  `catalog list|show --kind product` replaces `library list|show` (and the `product` alias), and
  `catalog list|show --kind subject` replaces `subjects list|show`. `eval tasks generate --from replay`
  replaces `repo tasks replay` (the same flags, `--threads` included). `skill check SKILL --root ROOT`
  is new: the `catalog check` findings for one skill. Every old path still answers, but it prints
  `moved: dokimi <new argv>` to stderr and exits 2 without running anything. Move the scripts to the
  new paths before the next release.

  | Before | After |
  |---|---|
  | `library list` | `catalog list --kind product` |
  | `library show NAME` | `catalog show --kind product NAME` |
  | `subjects list` | `catalog list --kind subject` |
  | `subjects show PATH` | `catalog show --kind subject PATH` |
  | `repo tasks replay` | `eval tasks generate --from replay` |
  | `product list`, `product show`, bare `library` | `catalog list --kind product`, `catalog show --kind product`, `catalog` |

  `novelty routing` and the `docs` group (`run`, `compare`) are not moved yet.
