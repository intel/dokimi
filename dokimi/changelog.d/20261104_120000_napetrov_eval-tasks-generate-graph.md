### Added

- **`dokimi eval tasks generate --from graph`** — writes enumeration tasks
  from a SCIP code index of a pinned revision, with no LLM or docker: which
  files (or functions) reference a function (C++ overloads as one), and which classes derive directly
  from a class. About half the tasks name a symbol whose name others share.
  The tasks are graded by the new built-in grader `dokimi:graph_enum`
  (score-only F1 against the index's gold). Contract §4.7.
