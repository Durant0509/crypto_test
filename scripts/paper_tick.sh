#!/bin/bash
# Hourly paper-trading tick, run locally by launchd (macOS). Runs from a
# non-geo-blocked IP so it can read the live Binance futures long/short ratio,
# then commits docs/live.js + paper_state/ledger.json so the dashboard updates.
set -uo pipefail

REPO="/Users/durant_lin/code/trading/crypto_test"
PY="$REPO/.venv/bin/python"
GIT=/usr/bin/git
LOG="$REPO/data/paper_tick.log"

cd "$REPO" || exit 1
mkdir -p "$REPO/data"

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) tick start ==="
  if "$PY" scripts/paper_tick.py; then
    "$GIT" add docs/live.js paper_state/ledger.json
    if "$GIT" diff --cached --quiet; then
      echo "no changes"
    else
      "$GIT" commit -m "paper tick $(date -u +%Y-%m-%dT%H:%MZ)" >/dev/null 2>&1
      if "$GIT" push origin main 2>&1; then echo "pushed"; else echo "PUSH FAILED"; fi
    fi
  else
    echo "TICK FAILED (see traceback above)"
  fi
  echo "=== tick done ==="
} >>"$LOG" 2>&1
