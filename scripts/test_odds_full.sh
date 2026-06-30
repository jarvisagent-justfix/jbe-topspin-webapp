#!/usr/bin/env bash
# Quick test output
cd /opt/data/jbe-tennis && PYTHONPATH=src /tmp/jbe-venv2/bin/python3 -m scripts.odds_api --report 2>/dev/null
