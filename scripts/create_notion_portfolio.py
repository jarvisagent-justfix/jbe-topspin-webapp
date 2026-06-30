#!/usr/bin/env python3
"""Crea il database Notion per il Paper Portfolio."""
import subprocess, json, http.client

r = subprocess.run(["grep","^NOTION_API_KEY","/opt/data/.env"], capture_output=True, text=True)
KEY = r.stdout.strip().split("=", 1)[1]

def notion(m, p, b=None):
    c = http.client.HTTPSConnection("api.notion.com")
    h = {"Authorization": f"Bearer {KEY}", "Notion-Version": "2025-09-03"}
    if b:
        h["Content-Type"] = "application/json"
        c.request(m, p, json.dumps(b, ensure_ascii=False).encode(), headers=h)
    else:
        c.request(m, p, headers=h)
    return json.loads(c.getresponse().read().decode())

# Find parent page
print("[INFO] Searching for JBE TopSpin page...")
search = notion("POST", "/v1/search", {"query": "JBE TopSpin", "page_size": 5})
parent = None
for r in search.get("results", []):
    if r.get("object") == "page":
        parent = r["id"]
        title_props = r.get("properties", {}).get("title", {}).get("title", [])
        if title_props:
            print(f"  Parent: {title_props[0]['text']['content'][:60]}")
        break

if not parent:
    print("[ERRORE] Pagina JBE TopSpin non trovata")
    exit(1)

# Create database
print("[INFO] Creating Portfolio database...")
body = {
    "parent": {"type": "page_id", "page_id": parent},
    "icon": {"type": "emoji", "emoji": "💰"},
    "title": [{"type": "text", "text": {"content": "Paper Portfolio — Registro Scommesse"}}],
    "properties": {
        "Match": {"title": {}},
        "Date": {"date": {}},
        "Selection": {"rich_text": {}},
        "Market": {"select": {"options": [
            {"name": "match_winner", "color": "blue"},
            {"name": "game_handicap", "color": "green"},
        ]}},
        "Odds": {"number": {"format": "number"}},
        "Model Prob": {"number": {"format": "percent"}},
        "Edge": {"number": {"format": "percent"}},
        "Stake": {"number": {"format": "number"}},
        "Status": {"select": {"options": [
            {"name": "pending", "color": "yellow"},
            {"name": "won", "color": "green"},
            {"name": "lost", "color": "red"},
        ]}},
        "P&L": {"number": {"format": "number"}},
        "Confidence": {"select": {"options": [
            {"name": "HIGH", "color": "green"},
            {"name": "MEDIUM", "color": "yellow"},
            {"name": "LOW", "color": "red"},
        ]}},
        "Bookmaker": {"rich_text": {}},
        "Surface": {"select": {"options": [
            {"name": "Hard", "color": "blue"},
            {"name": "Clay", "color": "orange"},
            {"name": "Grass", "color": "green"},
        ]}},
    },
}

resp = notion("POST", "/v1/databases", body)
if "id" in resp:
    db_id = resp["id"]
    print(f"\n[OK] Database creato!")
    print(f"  ID: {db_id}")
    print(f"  URL: https://notion.so/{db_id.replace('-', '')}")
    if "data_sources" in resp and resp["data_sources"]:
        ds_id = resp["data_sources"][0]["id"]
        print(f"  Data Source ID: {ds_id}")
        
        # Save to a config file for the sync script
        config = {"database_id": db_id, "data_source_id": ds_id}
        with open("/opt/data/jbe-tennis/data/notion_portfolio.json", "w") as f:
            json.dump(config, f, indent=2)
        print(f"  Config saved: data/notion_portfolio.json")
else:
    print(f"[ERRORE] {json.dumps(resp, indent=2)[:500]}")
