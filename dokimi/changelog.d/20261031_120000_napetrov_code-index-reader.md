### Added

- **SCIP code index reader (internal)** — `dokimi.repos.code_index.load()` reads a SCIP `index.scip` written by another tool and answers definitions, references, enclosing scopes, base classes and per-file occurrences. It is a library API with no CLI flag yet. The protobuf wire format is decoded with the standard library only, so no dependency is added. A malformed or truncated file raises `CodeIndexError`. Part of #41.
