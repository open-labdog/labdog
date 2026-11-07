#!/bin/bash
# Full baseline, most useful runs first so an interrupted run still informs.
cd "$(dirname "$0")"
for cfg in default raised; do
    for sid in S1 S2 S3 S4 S5; do ./run_one.sh "$sid" 300 "$cfg"; done
done
for cfg in default raised; do
    for n in 50 150; do ./run_one.sh S1 "$n" "$cfg"; done
done
echo MATRIX-COMPLETE
