### Fixed

- **The Context7 client uses `context7.timeout` from the config.** `create_doc_source_client("context7")` built the client with 30 seconds whatever `config/products.yaml` said, so raising `context7.timeout` as the quickstart advises had no effect. A `timeout` that is not a whole number of at least 1, including an empty one, is now an input error naming the file (#1291).
