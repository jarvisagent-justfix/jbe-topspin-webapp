"""
JBE TopSpin — Cron Setup

Configura il cron job per il report giornaliero.
"""
import sys
import os

# Da eseguire come: python3 scripts/setup_cron.py
# Questo script configura il cron job via Hermes

CRON_PROMPT = """
Sei JBE TopSpin, il sistema predittivo tennis. Genera il report giornaliero.

1. Esegui: cd /opt/data/jbe-tennis && PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/daily_report.py
2. Leggi il file salvato in data/delivery/
3. Invia il report come messaggio Discord formattato
4. Il report deve contenere:
   - Match del giorno con predizioni
   - Value bet trovate con edge, stake, confidenza
   - Stato bankroll
"""

# Per configurare il cron job:
# hermes cron create --schedule "0 10 * * *" --prompt "..." --deliver discord
print("Per configurare il cron job:")
print(f"1. hermes cron create --schedule '0 10 * * *' --prompt '{CRON_PROMPT}' --deliver discord:#tennis-bets")
print("2. Verificare: hermes cron list")
