### Fixed

- **`repo scan` checks `pytest --pyargs` module names as modules** — `pytest --pyargs pkg.tests`
  names a package, not a path, so every such claim was `unresolved` ("tried pkg.tests, ...").
  With `--pyargs`, a dotted name that is no path in the tree is now a `pytest_module` claim:
  `ok` when `pkg/tests.py` or `pkg/tests/__init__.py` is below some directory of the tree,
  `fail` when the tree holds the package `pkg` but not the module, and `unresolved` when it
  holds neither (an installed package). A `::test` node after a module is checked in the
  module's file. Without `--pyargs` a dotted name stays a path.
