#!/usr/bin/env bash
# JBE TopSpin — Pipeline completa (nessun output su Discord)
# 1. OddsPapi: scarica quote ATP (Bet365 > Pinnacle, rotazione 6 key)
# 2. Importa nuovi match + runna ML (ELO, XGBoost, value detection)
# 3. Genera data.json per la webapp
#
# Eseguito da cron: 0 9 * * * (09:00 UTC = 11:00 Italian)
set -e
BASE="/opt/data/jbe-topspin-webapp"
export PYTHONPATH="$BASE/src"
LOG="/tmp/jbe-pipeline.log"

echo "===== JBE Pipeline: $(date) =====" > "$LOG"
cd "$BASE"

# Step 1: Risolvi value bet pending (OddsPapi settlements)
echo "[1/4] Risoluzione bet pending..." >> "$LOG"
/tmp/jbe-venv2/bin/python3 scripts/odds_api.py --settle >> "$LOG" 2>&1 || echo "  settle: warning" >> "$LOG"

# Step 2: OddsPapi (fetch odds Bet365/Pinnacle)
echo "[2/4] OddsPapi..." >> "$LOG"
/tmp/jbe-venv2/bin/python3 scripts/odds_api.py --report >> "$LOG" 2>&1 || echo "  odds_api: warning (non bloccante)" >> "$LOG"

# Step 3: Daily report (import match + ELO + XGBoost + value detection)
echo "[3/4] Daily report..." >> "$LOG"
/tmp/jbe-venv2/bin/python3 scripts/daily_report.py >> "$LOG" 2>&1 || echo "  daily_report: warning" >> "$LOG"

# Step 4: Genera webapp data
echo "[4/4] Webapp data..." >> "$LOG"
python3 scripts/generate_webapp_data.py >> "$LOG" 2>&1

echo "===== Fatto: $(date) =====" >> "$LOG"
tail -5 "$LOG"
