#!/bin/bash
# 24 h retrain: label in four ~6 h groups (one process each keeps memory near the 5 h run's),
# concatenate, evaluate on a 60/40 time split, save the screener model. Logs to logs/full_day.log.
set -euo pipefail
cd "$(dirname "$0")/../.."
SW=data/interim/swaps_fleet_20261014
for g in 0 1 2 3; do
  d=data/interim/fd_g$g; mkdir -p $d
  ls $SW | sort -n | awk -v g=$g 'NR > g*270 && NR <= (g+1)*270' | while read f; do ln -sf "$(realpath $SW/$f)" $d/; done
  [ -f data/interim/labels_fd_g$g.parquet ] || .venv/bin/python -m research.sandwich.label --swaps $d --out data/interim/labels_fd_g$g.parquet --workers 5
done
.venv/bin/python -c "
import duckdb; duckdb.sql(\"copy (select * from read_parquet('data/interim/labels_fd_g*.parquet', union_by_name=true)) to 'data/interim/labels_fullday.parquet' (format parquet)\")"
.venv/bin/python -m research.sandwich.evaluate --labels data/interim/labels_fullday.parquet --save data/models/screener_v3.txt
