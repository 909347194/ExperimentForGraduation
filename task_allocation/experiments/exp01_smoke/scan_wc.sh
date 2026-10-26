#!/bin/bash
PY="D:/SchoolAffairs/groupmeeting/thesis/dissertation/ExperimentForGraduation/.venv/Scripts/python.exe"
cd "D:/SchoolAffairs/groupmeeting/thesis/dissertation/ExperimentForGraduation/task_allocation/experiments/exp01_smoke"
for pair in "1.5 3.0" "2.0 4.0" "2.5 5.0" "4.5 9.0"; do
  set -- $pair
  MIN=$1; MAX=$2
  TAG="wc${MIN}_${MAX}"
  echo "=== [$TAG] start $(date +%H:%M:%S) ==="
  "$PY" -u run.py --seeds 42,43,44 --no-plots \
    --set tasks.deadline_cycles_min=$MIN \
    --set tasks.deadline_cycles_max=$MAX \
    --set experiment.output_dir=results_scan_wc/$TAG \
    > "scan_${TAG}.log" 2>&1
  echo "=== [$TAG] exit=$? end $(date +%H:%M:%S) ==="
done
echo "ALL DONE $(date +%H:%M:%S)"
