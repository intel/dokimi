### Fixed

- **A judge rerun no longer pays for an unparseable first reply** — with
  `DOKIMI_RESPONSE_CACHE` on, only a reply that parses is stored, so a rerun of an
  answer whose score came from a retry asked the first attempt live again on every
  run before the stored retry replayed (at pinned sampling the provider repeats the
  unparseable reply, so this never healed). The single judge and each panel judge
  now look up the stored entry of every attempt before any live call: a rerun of
  an already-scored answer makes 0 provider calls. Cache keys are unchanged, so
  existing caches stay valid and get the saving at once.
