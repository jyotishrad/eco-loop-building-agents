#!/usr/bin/env bash
# One-command demo: runs baseline + AI closed-loop sims, then launches dashboard.
# Usage: ./run_demo.sh models/baseline.idf /path/to/weather.epw
set -e

IDF="${1:-models/baseline.idf}"
EPW="${2:?Usage: ./run_demo.sh <idf_path> <epw_path>}"

echo "== Checking Ollama is running =="
curl -sf http://localhost:11434/api/tags > /dev/null || {
  echo "Ollama not reachable at localhost:11434. Run 'ollama serve' in another terminal first."
  exit 1
}

echo "== Running baseline simulation =="
python src/orchestrator.py --idf "$IDF" --epw "$EPW" --mode baseline

echo "== Running AI closed-loop simulation =="
python src/orchestrator.py --idf "$IDF" --epw "$EPW" --mode ai-closed-loop

echo "== Summary =="
python src/metrics.py --baseline data/baseline_run.csv --ai data/ai_run.csv

echo "== Launching dashboard (Ctrl+C to stop) =="
streamlit run dashboard/app.py
