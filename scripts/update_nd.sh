#!/usr/bin/env bash
# Update Notion docs
set -e
NOTION_KEY=$(grep "^NOTION_API_KEY" /opt/data/.env | cut -d= -f2- | tr -d '\n')

# Read page
curl -s "https://api.notion.com/v1/pages/389cf515-23b3-8159-a4dc-cf8c9e3e7a4d/markdown" \
  -H "Authorization: Bearer *** \
  -H "Notion-Version: 2025-09-03" > /tmp/notion_page.json

# Build update payload with Python
python3 << 'PYEOF'
import json, subprocess

with open("/tmp/notion_page.json") as f:
    data = json.load(f)
md = data.get("markdown", "")
print(f"Markdown: {len(md)} chars")

idx = md.rfind("Versione 1.1")
if idx < 0:
    print("Signature not found")
    exit(1)

update = """
## 15. Updates (25/06/2026 — Portfolio + Game Handicap + Retrain)

### ✅ Paper Portfolio Tracker
- **Notion database** \"Paper Portfolio - Registro Scommesse\" creata
- **6 value bet** sincronizzate con odds, stake, status
- **Summary page** con ROI, win rate, bankroll
- **Auto-sync** ogni 6 ore su Notion

### ✅ Game Handicap + O/U Markets
- Odds API requests now include spreads + totals
- Ready for Wimbledon secondary markets

### ✅ XGBoost Retrain (2024-2026)
- **7,474** training samples, **38 features**
- **91.3%** validation accuracy
- Fresh model for grass season

### API Keys
- Key 1: dd20865b... (active)
- Key 2: cee0d680... (fallback)
"""

new_md = md + update

payload = {"type": "replace_content", "replace_content": {"old_str": md, "new_str": new_md}}
with open("/tmp/np_final.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)

print("Payload built")
PYEOF

# Upload
curl -s -X PATCH "https://api.notion.com/v1/pages/389cf515-23b3-8159-a4dc-cf8c9e3e7a4d/markdown" \
  -H "Authorization: Bearer *** \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d @/tmp/np_final.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('object','error'))"

echo "Done."
