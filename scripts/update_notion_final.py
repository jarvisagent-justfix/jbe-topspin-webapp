#!/usr/bin/env python3
"""Update Notion documentation with stake humanization and bug fixes."""
import json, http.client, subprocess, time, sys

result = subprocess.run(['grep', '^NOTION_API_KEY', '/opt/data/.env'], capture_output=True, text=True)
api_key = result.stdout.strip().split('=', 1)[1]

def notion_req(method, path, body=None):
    conn = http.client.HTTPSConnection('api.notion.com')
    headers = {'Authorization': f'Bearer {api_key}', 'Notion-Version': '2025-09-03'}
    if body:
        headers['Content-Type'] = 'application/json'
        conn.request(method, path, json.dumps(body).encode(), headers=headers)
    else:
        conn.request(method, path, headers=headers)
    resp = conn.getresponse()
    return json.loads(resp.read().decode())

page_id = '389cf515-23b3-8159-a4dc-cf8c9e3e7a4d'

payload = {
    "type": "insert_content",
    "insert_content": {
        "content": (
            "\n---\n"
            "<callout icon=\"🔄\" color=\"green_bg\">\n"
            "\t**Aggiornamento 25/06/2026 — Stake Umanizzati + Bug Fix Finali**\n"
            "</callout>\n\n"
            "### Stake Umanizzati\n\n"
            "Gli importi suggeriti non sono piu' valori Kelly precisi (es. 4.23 EUR) ma **valori arrotondati** (4.00 EUR, 5.50 EUR, 8.00 EUR). Questo per:\n"
            "- **Non sembrare un bot** agli occhi del bookmaker (Bet365 vede 4.23 EUR e sa che e' un algoritmo)\n"
            "- **Sembrare un cliente normale** che punta cifre tonde\n"
            "- **Ridurre il rischio di limitazione del conto**\n\n"
            "Regola: arrotondamento al 0.50 EUR piu' vicino, preferisce numeri tondi quando vicino (4.99 EUR -> 5.00 EUR, 3.51 EUR -> 3.50 EUR)\n\n"
            "### Bug Fix Completati (25/06/2026)\n\n"
            "| Bug | Fix |\n"
            "|---|---|\n"
            "| Self-improvement non funzionante (WHERE ? = ?) | Whitelist colonne + f-string |\n"
            "| ELO mai persistito dopo prediction | save_ratings() + commit() in record_match_result() |\n"
            "| 123 stale bets in sospeso | Cancellate (fonte odds_api) |\n"
            "| Importo NULL games crash training | (m['w_games'] or 0) |\n"
            "| rank_pts_diff=0 sempre in prediction | Parametro aggiunto a predict() |\n"
            "| Hardcoded retrain date | Sostituito con date.today().isoformat() |\n"
            "| DB connection timeout 0 | timeout=10 |\n\n"
            "### Cronologia\n"
            "- **25/06/2026 — Stake Umanizzati**: Arrotondamento Kelly a valori tondi. Bug fix finali (12 fix).\n"
        )
    }
}

result = notion_req('PATCH', f'/v1/pages/{page_id}/markdown', payload)
if 'error' in result:
    print(f"ERROR: {json.dumps(result, indent=2)[:300]}")
    sys.exit(1)
else:
    print("OK: Notion page updated")

time.sleep(2)
md = notion_req('GET', f'/v1/pages/{page_id}/markdown')
content = md.get('markdown', '')
checks = ['Stake Umanizzati', 'Bug Fix Completati', 'Cronologia']
for c in checks:
    ok = c.lower() in content.lower()
    status = "OK" if ok else "MISSING"
    print(f"  [{status}] Section: {c}")
