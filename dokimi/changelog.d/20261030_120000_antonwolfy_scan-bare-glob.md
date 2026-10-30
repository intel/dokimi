### Fixed

- **`repo scan` resolves a bare glob like a bare file name** — a glob with no `/`, such as
  `type_dispatch*.hpp`, was tried only at the guidance file's directory and its ancestors, so it
  was `unresolved` ("matches nothing in the tree; a build may create it") while the bare name
  `type_utils.hpp` next to it was `ok_basename`. It is now `ok_basename` too when it matches the
  name of some file or directory in the tree, with the count in the reason ("bare glob; 3
  match(es) in 2 directories, e.g. inc/utils/type_dispatch.hpp"). A bare glob that matches no
  name stays `unresolved`, with the same reason and `.gitignore` check as before.
