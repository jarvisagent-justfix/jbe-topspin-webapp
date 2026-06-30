#!/usr/bin/env bash
# JBE TopSpin — Cron delivery script
# Esegue il report giornaliero, salva il file, stampa per delivery
set -e
cd /opt/data/jbe-tennis
PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/daily_report.py 2>/tmp/jbe-tennis-cron.log
echo ""
echo "--- LOG ---"
cat /tmp/jbe-tennis-cron.log
