#!/usr/bin/env python3
"""Final Notion doc update: match_datetime column + Italian times."""
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
portfolio_id = '38acf515-23b3-81c2-87b7-e902f47b39fc'

# Update main page
payload = {
    "type": "insert_content",
    "insert_content": {
        "content": (
            "\n---\n"
            "### Match Datetime (Ora Italiana)\n\n"
            "Il portfolio ora registra la **data e ora italiana** (CEST, UTC+2) di ogni match:\n"
            "- **Odds API**: orario `commence_time` convertito in ora italiana (es. UTC 10:05 -> Italia 12:05)\n"
            "- **Daily report**: solo data (tennis-data.co.uk non fornisce orari)\n\n"
            "La colonna `match_datetime` nel paper portfolio mostra il formato `GG/MM/AAAA HH:MM`.\n"
        )
    }
}

result = notion_req('PATCH', f'/v1/pages/{page_id}/markdown', payload)
print(f"Main page: {'OK' if 'error' not in result else 'ERR'}")

# Update portfolio page
time.sleep(1)
md = notion_req('GET', f'/v1/pages/{portfolio_id}/markdown')
content = md.get('markdown', '')

# Update the table header
payload2 = {
    "type": "insert_content",
    "insert_content": {
        "content": (
            "\n<callout icon=\"🕐\" color=\"blue_bg\">\n"
            "\t**Novita**: Le scommesse live (Odds API) ora includono l'orario italiano (CEST).\n"
            "\tLe scommesse del report giornaliero mostrano solo la data.\n"
            "</callout>\n"
        )
    }
}

result2 = notion_req('PATCH', f'/v1/pages/{portfolio_id}/markdown', payload2)
print(f"Portfolio page: {'OK' if 'error' not in result2 else 'ERR'}")
print("Documentation updated.")
PYEOF
