#!/usr/bin/env bash
# Linux/macOS equivalent of setup_and_run.bat   (usage: ./setup_and_run.sh [ui|api|both|eval|test])
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
[ -f .venv/.deps_ok ] || { .venv/bin/pip install -q --upgrade pip; .venv/bin/pip install -r requirements.txt; touch .venv/.deps_ok; }
[ -f .env ] || cp .env.example .env
if ! grep -q '^GROQ_API_KEY=gsk_' .env; then
  read -r -p "Paste your free Groq API key (https://console.groq.com/keys) or Enter to skip: " K
  [ -n "$K" ] && sed -i.bak "s|^GROQ_API_KEY=.*|GROQ_API_KEY=$K|" .env && rm -f .env.bak
fi
.venv/bin/python scripts/check_setup.py || true
case "${1:-ui}" in
  api)  .venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000 ;;
  both) .venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000 & .venv/bin/streamlit run app.py ;;
  eval) .venv/bin/python scripts/run_eval.py --limit 10 ;;
  test) .venv/bin/python -m pytest -q tests ;;
  *)    .venv/bin/streamlit run app.py ;;
esac
