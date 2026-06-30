#!/usr/bin/env bash
# JBE TopSpin — Webapp data generation (no Discord output)
cd /opt/data/jbe-topspin-webapp
export PYTHONPATH=src
exec python3 scripts/generate_webapp_data.py 2>/tmp/jbe-webapp-cron.log
