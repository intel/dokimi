<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Evaluate a shipped product

A release engineer can test an RC using just the installer, binary or wheel, a pinned runtime image, and a consumer project. A separate AX operator can run the same consumer and verifier with external documentation. Both commands take JSON files from a content checkout. The content owner controls executable install scripts, contract checks and verifiers. Import third-party material as text.

The offline report renders Markdown. Redirect it to a file to attach it to an RC or AX review.

The real-container fixture is a manual test. Set `DOKIMI_DELIVERY_IMAGE` to the immutable ID of a local Linux/amd64 image such as the Ubuntu image below, then run `pytest tests/test_delivery.py -k real_delivery`. Other delivery tests use stand-ins for Docker.

## Try the consumer fixture

From the `dokimi/` project directory, with Docker available, create the external pack once. The image ID includes the operating system and every preinstalled dependency. On CI, preload that exact image. The demo expects Linux x86-64.

```bash
docker pull --platform linux/amd64 ubuntu:24.04
pack=/tmp/shipcalc
image=$(docker image inspect ubuntu:24.04 --format '{{.Id}}')
python tests/fixtures/delivery/build.py "$pack" "$image"
python cli.py delivery release --manifest "$pack/good.json" --policy "$pack/policy.json" --tasks "$pack/tasks.json" --out "$pack/release.json" --json
python cli.py delivery scan --manifest "$pack/good.json" --corpus "$pack/corpus.json" --tasks "$pack/tasks.json" --fixture --out "$pack/scan.json" --json
python cli.py delivery report "$pack/scan.json"
```

`good.json`, `broken.json` and `bad-example.json` install a real shell CLI into a fresh container. The second manifest's binary breaks `double` but supports `twice`. The third supplies a working binary and an incorrect example. A saved article selects the fallback backend. The verifier grades the backend label and numeric output on hidden input. Fixture scripts represent recorded responses. Their success rates describe only those scripts.

For a live one-turn Claude Code answer, install and authenticate the supported `claude-cli` provider, then run the following with a model ID available to your account. Record the CLI package version in the deployment lock. The CLI receives the instruction, starter project and source text with tools disabled. Dokimi executes its proposed shell consumer separately. OpenAI, Anthropic and Amazon Bedrock API providers also support this protocol. Each applicable scenario plans two model calls per repeat, one per arm. Live scans record reported usage after each call and have no CLI dollar cap.

```bash
python cli.py delivery scan --manifest "$pack/good.json" --corpus "$pack/corpus.json" --tasks "$pack/tasks.json" --provider claude-cli --model "$MODEL_ID" --out "$pack/live.json" --json
```

## Authoring the contract

The generated pack is a complete input example. File references carry `path` (relative to their own input document) and `sha256` (hex SHA-256 of the exact bytes). A manifest identifies a version, profile, platform, local image ID, artifact, install command and documentation snapshot. `gpu: true` requests GPU access for the consumer. Bundle additional offline dependencies in the pinned image or distribution. The install command receives `/delivery`. For a wheel, copy it to its original filename before invoking the runtime's pip with `--no-index`. The writable consumer workspace starts with `consumer.sh`. The install command must return nonzero on failure.

The catalog maps task IDs to a profile, group, instruction, initial file, hidden input, verifier, owner, per-container timeout in seconds and contract script. `example: true` runs the shipped documentation verbatim as a shell example. With `example: false`, `contract` supplies the script and the documentation can be prose. A real agent returns a JSON object with `script` and optional boolean `claimed_success`. The `litellm-shell-v2` harness also accepts a JSON object wrapped in Markdown. A reply without a usable object or string script counts as a failed attempt. The offline report shows the protocol error. Historical v1 scores keep their original interpretation. The one-turn agent never sees the hidden input or verifier. Its shell receives hidden stdin. A second fresh container receives stdout on stdin and `consumer_exit` as a shell variable. A verifier must check both. It exits 0 to accept, 10 for a confirmed violation and any other code for a measurement failure. Verifier crashes must propagate their status. Both containers have networking disabled and only use preloaded images.

The release policy declares `version` and a `profiles` map with `required`, `product_version`, `platform`, `image` and `tasks_sha256`. List applicable checks before launch. A profile can omit a scenario it does not promise. The demo has two required checks, one for the binary operation and one for the published example. Its binary control declares `scan: false` in advance. An unavailable mandatory GPU runner or missing check leaves that check inconclusive. Any confirmed mandatory violation fails the release, even alongside missing measurements. Pass requires evidence for every mandatory check. The evaluator matches the full delivery, policy and catalog identity and accepts one contract observation per required check. Keep the policy and release artifacts in the release operator's trusted checkout and artifact store.

A scan corpus lists `sources` with a local snapshot, origin URL, capture time, source version if known, finding ID, recommended owner and repair. For an unreachable official page import the URL, capture time, owner and `unavailable` reason. The B arm becomes partial. The operator can then fix the site and capture a new snapshot. The current runner replays injected text. It cannot establish natural discovery in search or prove which passage the model read. `observed_reads: null` records that telemetry gap. `conflict` is a corpus author's assessment. Compare A and B with their attempt counts to examine a change in outcome. A bundle of sources measures the bundle together.

To promote a finding, reproduce it against the intended RC, identify the promised operation, add a deterministic task with `finding_id`, and change the required IDs and catalog pin in a reviewed policy revision. An obsolete article alone adds no obligation to the release profile.

The fixture `zero-rc.json` passes policy 1 but drops the result for input zero. Reproduce the imported `missing-zero-row` finding with the same RC and hidden zero input. Both replay arms fail. Policy 2 adopts `double-zero` because the CLI promises a result for every input. Its release observation carries the original finding ID.

```bash
python cli.py delivery scan --manifest "$pack/zero-rc.json" --tasks "$pack/regression-tasks.json" --corpus "$pack/zero-corpus.json" --fixture --out "$pack/zero-scan.json"
python cli.py delivery release --manifest "$pack/zero-rc.json" --tasks "$pack/regression-tasks.json" --policy "$pack/regression-policy.json" --out "$pack/zero-check.json"
```

## Reports and CI

`--out` stores a `delivery.v1` artifact. `--json` prints its payload under `data` in the standard `cli_output.v1` envelope. `delivery report` validates and rereads the saved artifact offline and preserves its exit status. Example summary fields for the good release and fixture scan are shown below (scan excerpt).

```json
{"verdict":"PASS","checks":{"double":"PASS","binary-double":"PASS"}}
```

```json
{"A":{"score":10.0,"coverage":[2,2],"false_success":[0,2]},"B":{"score":0.0,"coverage":[2,2],"false_success":[2,2]},"partial":false}
```

Each arm first averages measured repeats per scenario, then scenarios per group. Its score is ten times the sum of group means weighted by normalized predeclared group weights. A working runner with a wrong agent answer scores zero on that attempt. Missing evidence leaves coverage below plan and the arm score null. Coverage counts measured/planned. False success counts rejected claims/all claims. `unverified_claims` counts claims whose verifier result is missing. Without any claims the counts are `[0,0]`. No scan score gates a release.

Use independent workflows in the product repository. These job excerpts assume a prepared runner with Dokimi installed, the pinned image preloaded and the external content checkout under `$PACK`. In branch protection require only `delivery-release`. A release verdict maps PASS to 0, FAIL to 1 and INCONCLUSIVE to 3. A partial scan exits 3 in its own workflow. Archive JSON even after a nonzero exit.

```yaml
# Release workflow, triggered by the product's RC pipeline
jobs:
  delivery-release:
    runs-on: [self-hosted, product-eval]
    steps:
      - run: dokimi delivery release --manifest "$PACK/rc.json" --tasks "$PACK/tasks.json" --policy "$PACK/policy.json" --out release.json --json
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: delivery-release, path: release.json}
```

```yaml
# Independent AX workflow
on: {workflow_dispatch: {}, schedule: [{cron: '0 6 * * 1'}]}
jobs:
  ax-scan:
    runs-on: [self-hosted, product-eval]
    steps:
      - run: dokimi delivery scan --manifest "$PACK/rc.json" --tasks "$PACK/tasks.json" --corpus "$PACK/corpus.json" --provider claude-cli --model "$MODEL_ID" --out scan.json --json
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: ax-scan, path: scan.json}
```
