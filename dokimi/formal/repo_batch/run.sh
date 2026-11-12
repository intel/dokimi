#!/usr/bin/env bash
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run TLC on the given cfgs (default every cfg/*.cfg) and check each against its "EXPECT:" line; see README.md.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
jar=${TLA2TOOLS_JAR:?set TLA2TOOLS_JAR to tla2tools.jar (v1.8.0)}
java=${JAVA:-java}
out=${OUT:-$here/out}
workers=${TLC_WORKERS:-4}
mkdir -p "$out"
summary="$out/summary.tsv"
printf 'cfg\texpect\tgot\tok\tgenerated\tdistinct\tdepth\tseconds\n' > "$summary"
fail=0
[ $# -gt 0 ] || set -- "$here"/cfg/*.cfg
for cfg in "$@"; do
    name=$(basename "$cfg" .cfg)
    expect=$(sed -n 's/^\\\* EXPECT: //p' "$cfg")
    log="$out/$name.log"
    start=$(date +%s.%N)
    set +e
    (cd "$here" && "$java" -XX:+UseParallelGC -cp "$jar" tlc2.TLC -workers "$workers" -deadlock -noGenerateSpecTE \
        -metadir "$out/states/$name" -config "$cfg" RepoBatch.tla) > "$log" 2>&1
    rc=$?
    set -e
    secs=$(echo "$(date +%s.%N) - $start" | bc)
    if grep -q '^Model checking completed. No error has been found.' "$log"; then
        got=pass
    elif name_v=$(grep -oP '^Error: Invariant \K\S+(?= is violated)' "$log"); then
        got="violation $name_v"
    elif name_v=$(grep -oP '^Error: Temporal property \K\S+(?= was violated)' "$log"); then
        got="violation $name_v"
    else
        got="error rc=$rc"
    fi
    gen=$(grep -oP '^\d+ states generated, \K\d+(?= distinct)' "$log" | tail -1 || true)
    all=$(grep -oP '^\K\d+(?= states generated)' "$log" | tail -1 || true)
    depth=$(grep -oP 'depth of the complete state graph search is \K\d+' "$log" || true)
    ok=yes; [ "$got" = "$expect" ] || { ok=NO; fail=1; }
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%.1f\n' "$name" "$expect" "$got" "$ok" "${all:-}" "${gen:-}" "${depth:-}" "$secs" >> "$summary"
done
column -t -s $'\t' "$summary"
exit $fail
