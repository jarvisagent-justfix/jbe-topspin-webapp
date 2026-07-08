#!/usr/bin/env python3
"""Genera data.json per la webapp JBE TopSpin.
Eseguito dopo il daily_report.py per aggiornare il frontend.

Perché sanitizzazione del path:
  Il path di output viene da sys.argv[1] — se qualcuno passasse
  ../../../etc/output, potrebbe scrivere fuori dal progetto.
  Resolve assoluto + verifica che sia dentro BASE garantisce sicurezza.

Uso: PYTHONPATH=src python3 scripts/build_webapp_data.py [--output docs/api/data.json]
"""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "docs", "api"))
from data import build_data

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    data = build_data()
    DEFAULT_OUTPUT = os.path.join(BASE, "docs", "api", "data.json")
    
    # Parsing argomento CLI (sicuro: restando dentro BASE)
    raw_path = DEFAULT_OUTPUT
    for arg in sys.argv[1:]:
        if arg == "--output" and len(sys.argv) > sys.argv.index(arg) + 1:
            raw_path = sys.argv[sys.argv.index(arg) + 1]
        elif arg.startswith("--output="):
            raw_path = arg.split("=", 1)[1]
    
    output = os.path.abspath(os.path.join(BASE, raw_path))
    
    # Sanity check: output deve essere dentro BASE
    if not output.startswith(os.path.abspath(BASE)):
        print(f"[ERRORE] Path '{output}' fuori dalla directory del progetto.")
        sys.exit(1)
    
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
