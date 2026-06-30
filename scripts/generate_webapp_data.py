#!/usr/bin/env python3
"""Genera data.json per la webapp JBE TopSpin.
Eseguito dopo il daily_report.py per aggiornare il frontend.

Uso: PYTHONPATH=src python3 scripts/generate_webapp_data.py [--output webapp/api/data.json]
"""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
# Import the webapp data builder
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webapp", "api"))
from data import build_data

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    data = build_data()
    output = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "webapp", "api", "data.json")
    output = output.replace("--output=", "")
    
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"✅ Webapp data generato: {output}")
    print(f"   Match oggi: {len(data['matches']['today'])}")
    print(f"   Match in arrivo: {len(data['matches']['upcoming'])}")
    print(f"   Value bets: {len(data['value_bets'])}")
    print(f"   Bankroll: €{data['bankroll']['current']:.2f}")
    return output

if __name__ == "__main__":
    out = main()
    print(f"\n📲 Apri webapp: file://{os.path.abspath(out).replace('api/data.json', 'index.html')}")
