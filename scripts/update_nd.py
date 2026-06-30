#!/usr/bin/env python3
"""Read Notion page, append update, write back."""
import urllib.request, json, os, subprocess

key = subprocess.run(["grep","^NOTION_API_KEY","/opt/data/.env"],
    capture_output=True, text=True, shell=False).stdout.strip().split("=",1)[1].strip()

PAGE = "389cf515-23b3-8159-a4dc-cf8c9e3e7a4d"

def notion(method, path, body=None):
    url = f"https://api.notion.com/v1{path}"
    headers = {"Authorization": f"Bearer {key}", "Notion-Version": "2025-09-03"}
    data = json.dumps(body, ensure_ascii=False).encode() if body else None
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

# Read
md = notion("GET", f"/pages/{PAGE}/markdown").get("markdown", "")
print(f"Read: {len(md)} chars")

# Append update
update = """
## 15. Updates (25/06/2026)

### ✅ Paper Portfolio Tracker
- **Notion database** "Paper Portfolio - Registro Scommesse" creata sotto JBE TopSpin
- **6 value bet** sincronizzate (odds, stake, status, edge)
- **Summary page** con ROI, win rate, bankroll (200 EUR iniziali)
- **Auto-sync** ogni 6 ore via cron

### ✅ Game Handicap + O/U Markets
- Odds API richiede spreads + totals oltre a h2h
- Pronto per mercati secondari Wimbledon

### ✅ XGBoost Retrain
- **7.474** training samples su 2024-2026, **38 features**
- **91.3%** validation accuracy
- Modello fresco per erba

### API Keys
- Key 1: dd20865b...
- Key 2: cee0d680...

### Cron Jobs
| Nome | Orario | Funzione |
|------|--------|----------|
| JBE TopSpin Daily | 09:00 UTC | Report retrospettivo |
| Odds API Live | 00/06/12/18 UTC | Value bet pre-match |
| Portfolio Sync | +30min Odds | Sincronizza Notion |
"""

new_md = md + update

resp = notion("PATCH", f"/pages/{PAGE}/markdown", {
    "type": "replace_content",
    "replace_content": {"old_str": md, "new_str": new_md}
})

print(f"Update: {resp.get('object', 'error')}")
if "markdown" in resp:
    print("OK - Document updated")
else:
    print(json.dumps(resp, indent=2)[:200])
