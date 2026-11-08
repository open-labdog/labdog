#!/bin/bash
# Full baseline, most useful runs first so an interrupted run still informs.
cd "$(dirname "$0")"
# Resume: skip runs that already have results.
run() { [ -f "results/$1-n$2-$3/measure.txt" ] || ./run_one.sh "$@"; }
for cfg in default raised; do
    for sid in S1 S2 S3 S4 S5; do run "$sid" 300 "$cfg"; done
done
for cfg in default raised; do
    for n in 50 150; do run S1 "$n" "$cfg"; done
done
echo MATRIX-COMPLETE
