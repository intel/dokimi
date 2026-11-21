### Documentation

- **Where a `file:<path>` key is read** — the `dokimi.llm` module docstring said any key may be `file:<path>`. It is read for an explicit `api_key` and for the variable of a provider in the key table; a provider outside the table gets its vendor variable through litellm unread, as `resolve_api_key` does.
