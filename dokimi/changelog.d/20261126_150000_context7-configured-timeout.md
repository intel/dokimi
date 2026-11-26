### Fixed

- **The Context7 client uses `context7.timeout` and `context7.max_retries` from the config.** `create_doc_source_client("context7")` built the client with 30 seconds and 3 retries whatever `config/products.yaml` said, so raising `context7.timeout` as the quickstart advises had no effect. A value that is not a whole number (timeout at least 1, retries at least 0) is now an input error naming the file (#1291).
