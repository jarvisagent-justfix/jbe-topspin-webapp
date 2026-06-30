#!/usr/bin/env python3
"""Second pass: remove remaining 2 Leviathan refs."""
import subprocess, json, http.client

r = subprocess.run(["grep","^NOTION_API_KEY","/opt/data/.env"], capture_output=True, text=True)
KEY = r.stdout.strip().split("=", 1)[1]
PAGE = "389cf515-23b3-8159-a4dc-cf8c9e3e7a4d"

def notion(method, path, body=None):
    c = http.client.HTTPSConnection("api.notion.com")
    h = {"Authorization": f"Bearer {KEY}", "Notion-Version": "2025-09-03"}
    if body:
        h["Content-Type"] = "application/json"
        c.request(method, path, json.dumps(body, ensure_ascii=False).encode(), headers=h)
    else:
        c.request(method, path, headers=h)
    return json.loads(c.getresponse().read().decode())

md = notion("GET", f"/v1/pages/{PAGE}/markdown").get("markdown", "")
new = md

new = new.replace("sistema gemello di JBE Leviathan, costruito", "sistema predittivo autonomo, costruito")
new = new.replace("(ex JBE Leviathan references removed)", "(indipendente dal calcio)")

n = new.count("Leviathan")
print(f"Leviathan remaining: {n}")

if n == 0:
    payload = {"type": "replace_content", "replace_content": {"old_str": md, "new_str": new}}
    with open("/tmp/notion_p3.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    
    import subprocess as sp
    cmd = ["curl", "-s", "-X", "PATCH", f"https://api.notion.com/v1/pages/{PAGE}/markdown",
           "-H", f"Authorization: Bearer {KEY}",
           "-H", "Notion-Version: 2025-09-03", "-H", "Content-Type: application/json",
           "-d", "@/tmp/notion_p3.json"]
    res = sp.run(cmd, capture_output=True, text=True, timeout=30)
    print(f"curl: {res.stdout[:200]}")
    
    final = notion("GET", f"/v1/pages/{PAGE}/markdown").get("markdown", "").count("Leviathan")
    print(f"Final: {final}")
    if final == 0:
        print("OK - Tutti i riferimenti Leviathan rimossi!")
else:
    print("Not yet clean")
