#!/usr/bin/env python3
"""Update Notion docs with portfolio + handicap + retrain status."""
import subprocess, json

r = subprocess.run(["grep","^NOTION_API_KEY","/opt/data/.env"], capture_output=True, text=True)
KEY = r.stdout.strip().split("=", 1)[1]

import http.client
def notion(m, p, b=None):
    c = http.client.HTTPSConnection("api.notion.com")
    h = {"Authorization": f"Bearer {KEY}", "Notion-Version": "2025-09-03"}
    if b:
        h["Content-Type"] = "application/json"
        c.request(m, p, json.dumps(b, ensure_ascii=False).encode(), headers=h)
    else:
        c.request(m, p, headers=h)
    return json.loads(c.getresponse().read().decode())

PAGE = "389cf515-23b3-8159-a4dc-cf8c9e3e7a4d"
md = notion("GET", f"/v1/pages/{PAGE}/markdown").get("markdown", "")

update = """
## 15. Updates (25/06/2026 — Portfolio + Game Handicap + Retrain)

### ✅ Paper Portfolio Tracker
- Notion database "Paper Portfolio — Registro Scommesse" creata sotto JBE TopSpin
- 6 value bet sincronizzate con odds, edge, stake
- Summary page con ROI, win rate, bankroll corrente (200 EUR iniziali)
- Sync automatico ogni 6 ore
- P&L chart generato dopo le prime settlement

### ✅ Game Handicap + O/U Markets
- Odds API ora richiede anche spreads (handicap) e totals (O/U)
- Pronto per Wimbledon

### ✅ XGBoost Retrain (2024-2026)
- 7.474 match di training, 38 feature
- Validation accuracy: 91.3%
- Modello fresco per stagione su erba

### API Keys
- Key 1: dd20865b... (attiva)
- Key 2: cee0d680... (fallback)
"""

old = "Versione 1.1"
if old in md:
    new_md = md.replace(old, old + update)
    payload = {"type": "replace_content", "replace_content": {"old_str": md, "new_str": new_md}}
    with open("/tmp/n_update.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    
    cmd = ["curl", "-s", "-X", "PATCH", f"https://api.notion.com/v1/pages/{PAGE}/markdown",
           "-H", f"Authorization: Bearer ***           "-H", "Notion-Version: 2025-09-03", "-H", "Content-Type: application/json",
           "-d", "@/tmp/n_update.json"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    print(f"Response: {res.stdout[:100]}")
    
    final = notion("GET", f"/v1/pages/{PAGE}/markdown").get("markdown", "")
    print(f"Update present: {'Portfolio' in final[-500:]}")
