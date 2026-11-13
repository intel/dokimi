### Added

- **The `novelty routing --all-vs-all` result records `shared_queries`** — `{"shared": 9, "total": 9}`: how many of the queries with a content word are listed for more than one skill. The shared-query warning went to stderr only, so a file saved with `--out` from a run on queries drawn from one template read later as a normal own-rate table. Files saved before this change lack the key.
