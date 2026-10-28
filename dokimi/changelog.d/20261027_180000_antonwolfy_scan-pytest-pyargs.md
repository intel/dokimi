### Fixed

- **`repo scan` checks `pytest --pyargs` module names as modules** — `pytest --pyargs pkg.tests`
  names a package, not a path, so every such claim was `unresolved` ("tried pkg.tests, ...").
  With `--pyargs`, a dotted name that is no path in the tree is now a `pytest_module` claim:
  `ok` when `pkg/tests.py` or `pkg/tests/__init__.py` is below some directory of the tree
  (present in the tree, not checked to import), `fail` when the tree holds a package the
  name lies in but not the module (`pkg.tests.test_a` with `pkg/tests/` gone is a miss in
  `pkg`) and the package sits at the root or in `src/`, of the tree or of a directory above
  the guidance file, and `unresolved` when it holds no such package (an installed one) or
  holds it only deeper (`docs/examples/pkg/`). A `::test` node after a module is checked in
  the module's file. Without `--pyargs` a dotted name stays a path.
