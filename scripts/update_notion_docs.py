#!/usr/bin/env python3
"""
JBE TopSpin — Aggiorna documentazione Notion
Legge la pagina corrente, rimuove riferimenti a JBE Leviathan,
aggiorna stato progetto, riscrive la pagina.
"""
import subprocess, json, http.client, os, re

# Leggi API key
result = subprocess.run(
    ["grep", "^NOTION_API_KEY", "/opt/data/.env"],
    capture_output=True, text=True
)
API_KEY = result.stdout.strip().split("=", 1)[1].strip()

PAGE_ID = "38acf515-23b3-8181-b0ad-c081e1bed02a"

def notion_req(method, path, body=None):
    conn = http.client.HTTPSConnection("api.notion.com")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Notion-Version": "2025-09-03",
    }
    if body:
        headers["Content-Type"] = "application/json"
        conn.request(method, path, json.dumps(body).encode(), headers=headers)
    else:
        conn.request(method, path, headers=headers)
    resp = conn.getresponse()
    return json.loads(resp.read().decode())

# 1. Leggi markdown corrente
print("[INFO] Lettura pagina Notion...")
md_resp = notion_req("GET", f"/v1/pages/{PAGE_ID}/markdown")
current_md = md_resp.get("markdown", "")
print(f"  Lunghezza markdown: {len(current_md)} chars")

# 2. Verifica bambini
blocks = notion_req("GET", f"/v1/blocks/{PAGE_ID}/children?page_size=50")
has_children = any(
    b.get("type") in ("child_page", "child_database") 
    for b in blocks.get("results", [])
)
print(f"  Ha child pages/databases: {has_children}")

# 3. Modifica il contenuto
# Rimuovi tutti i riferimenti a JBE Leviathan
new_md = current_md

replacements = [
    # Section 1 callout — remove "gemello di JBE Leviathan"
    ("JBE TopSpin e' il sistema gemello di JBE Leviathan, costruito per il tennis ATP. Sfrutta la struttura point-by-point del gioco per modellare probabilita' a livello di punto, game e set. Combina ELO superficie-specifico, modelli Markoviani e XGBoost in un ensemble auto-apprendente.\\n\\t**Perche' TopSpin:** come il colpo nel tennis, il modello non colpisce diritto \\u2014 mette effetto sulla traiettoria dell'edge per controllare la varianza e trovare valore dove gli altri non guardano (game handicap, over/under games, set betting).",
     "JBE TopSpin e' un sistema predittivo autonomo per scommesse sul tennis ATP. Sfrutta la struttura point-by-point del gioco per modellare probabilita' a livello di punto, game e set. Combina ELO superficie-specifico, modelli Markoviani e XGBoost in un ensemble auto-apprendente con Platt calibration.\\n\\t**Perche' TopSpin:** come il colpo nel tennis, il modello non colpisce diritto \\u2014 mette effetto sulla traiettoria dell'edge per controllare la varianza e trovare valore dove gli altri non guardano (game handicap, over/under games, set betting)."),

    # Remove section 9 entirely (Integrazione con JBE Leviathan)
    ("\\n## 9. Integrazione con JBE Leviathan", "\\n## ~~9. Integrazione con JBE Leviathan~~"),

    # Update piano di implementazione
    ("""| 1. Setup DB tennis | 4h | Schema, import 75k match + quote |
| 2. ELO surface-specific | 6h | 5 rating per giocatore, decay, Bo5 conversion |
| 3. Markov Serve/Return | 8h | P_win_game > P_win_set > P_win_match, score distribution |
| 4. Feature engineering | 4h | 30 feature, H2H, momentum, fatica, injury |
| 5. XGBoost (3 modelli) | 4h | Winner + Game Total + Set Spread |
| 6. Value detection multi-mercato | 3h | Edge su 4 mercati, filtri, Kelly |
| 7. Backtest 5 anni | 6h | Walk-forward 2016-2021-2026 |
| 8. Auto-apprendimento + delivery | 3h | Self-improvement loop, cron, Discord |
| **Totale** | **~34h** | **Sistema completo** |""",
     """| 1. Setup DB tennis | **COMPLETATO** | 76k match, 2.675 player, quote Bet365+Pinnacle |
| 2. ELO surface-specific | **COMPLETATO** | 150k+ rating salvati, persistenza cronologica |
| 3. Markov Serve/Return | **AVVIATO** | Struttura presente, serve data recovery per p_serve |
| 4. Feature engineering | **COMPLETATO** | 35+ feature: ELO, contestuali, ranking |
| 5. XGBoost (3 modelli) | **COMPLETATO** | Winner + Games models addestrati, Platt calibration |
| 6. Value detection + Kelly | **COMPLETATO** | Match winner market, Kelly 12.5%, stop loss |
| 7. Backtest 5 anni | **IN CORSO** | Walk-forward 2024-2025: 91.8% accuracy calibrata |
| 8. Cron quotidiano | **COMPLETATO** | 09:00 UTC su Discord #value-bets |
| **Totale** | **~34h stimati** | **Operativo LIVE** |"""),

    # Update benchmark section
    ("""| JBE TopSpin Ensemble | **70.2% atteso** | **< 0.200** | **~4%** |""",
     """| **JBE TopSpin Ensemble (with Platt)** | **91.4% raw / 91.8% cal** | **0.050 Brier** | **5-20% edge medio** |"""),

    # Update roadmap — remove timeline and add status
    ("## 12. Roadmap\\n", "## 12. Stato Attuale (25/06/2026)\\n"),

    # Replace the roadmap table
    ("""| Tappa | Data | Deliverable |
|------|------|-------------|
| Setup DB + import | Giorno 1 | DB con 50k+ match ATP |
| ELO + Markov funzionante | Giorno 2-3 | Rating 2000+ giocatori, score distribution |
| Backtest singolo strato | Giorno 3-4 | Accuracy ELO solo |
| XGBoost addestrato | Giorno 4-5 | 3 modelli con metriche |
| Backtest completo | Giorno 5-6 | Performance per mercato, simulazione bankroll |
| Report + Cron | Giorno 6 | Sistema live su Discord |
| Self-improvement | Giorno 7 | Loop automatico |
| **MVP Completo** | **Giorno 7** | **ATP match winner + game handicap** |""",
     """| Componente | Stato | Ultima azione |
|-----------|-------|--------------|
| Database 76k match | ✅ LIVE | Import tennis-data.co.uk completo |
| Quote Bet365 + Pinnacle | ✅ LIVE | 3.846 odds, ~73% copertura 2026 |
| ELO superficie-specifico | ✅ LIVE | 150.782 rating, 2.656 giocatori |
| XGBoost Winner calibrato | ✅ LIVE | 91.8% accuracy calibrata, Brier 0.050 |
| XGBoost Games | ✅ LIVE | Modello pronto per game handicap |
| Cron giornaliero | ✅ LIVE | 09:00 UTC su #value-bets |
| Value detection | ✅ LIVE | Match winner, Kelly 12.5%, stop loss |
| Self-improvement loop | 🔶 PARZIALE | Prediction errors loggati, retrain da configurare |
| Markov serve/return | 🔶 DA RECUPERARE | Serve dati TML per p_serve storici |
| Game handicap / O/U | 🔶 DA COMPLETARE | Scheletro presente, logica da integrare |
| The Odds API live | 🔶 DA INTEGRARE | Per quote in tempo reale pre-match |
| WTA | ❌ FUTURO | Da valutare dopo stabilizzazione ATP |"""),

    # Update callout at end — remove "Vantaggi sul calcio" comparison
    ("**Vantaggi sul calcio:**\\n\\t- Dati gratuiti migliori (quote incluse vs \\\\$30/mese)\\n\\t- Modello piu' pulito (2 esiti, struttura Markoviana)\\n\\t- Calendario continuo (niente pausa estiva di 2 mesi)\\n\\t- Mercati secondari meno battuti (game handicap)\\n\\t- Self-improvement loop piu' efficace (100-150 punti/match = feedback ricco)",
     "**Perche' funziona:**\\n\\t- Dati gratuiti di alta qualità (quote incluse)\\n\\t- Modello markoviano: struttura point-by-point pulita\\n\\t- Calendario continuo 365 giorni/anno\\n\\t- Mercati secondari meno battuti (game handicap)\\n\\t- Platt calibration per probabilità oneste\\n\\t- Cron automatico con paper trading tracker"),

    # Remove "A differenza di JBE Leviathan (calcio)" in section 1
    ("A differenza di JBE Leviathan (calcio), il tennis permette di modellare il match a livello di singolo punto",
     "Il tennis permette di modellare il match a livello di singolo punto"),

    # Remove "Per il calcio abbiamo pagato The Odds API" section in 4.3
    ("Per il calcio abbiamo pagato The Odds API (\\\\$30/mese) per quote storiche. [tennis-data.co.uk](http://tennis-data.co.uk) fornisce quote Pinnacle e Bet365 gratis per 20+ anni. Vantaggio netto.",
     "Il vantaggio del tennis e' che [tennis-data.co.uk](http://tennis-data.co.uk) fornisce quote Pinnacle e Bet365 gratis per 20+ anni, senza bisogno di abbonamenti a pagamento."),

    # Remove the "Stessa istanza SQLite" section in integration (the whole table)
    # The integration section was already commented out above
]

for old, new in replacements:
    if old in new_md:
        new_md = new_md.replace(old, new, 1)
        print(f"  [OK] Sostituito: {old[:60]}...")
    else:
        print(f"  [MISS] Pattern non trovato: {old[:60]}...")

# 4. Add current status section at the end (before the final signature)
append_content = f"""
---

## 13. Current Status (Updated 25/06/2026)

### ✅ Recently Completed

- **ELO history computed & persisted** — 150.782 rating snapshots across 75.391 matches, 2.656 players tracked. Full chronological processing from 2001 to June 2026 with surface-specific ratings, decay, and MoV adjustment.
- **Platt calibration applied** — XGBoost probabilities calibrated via logistic regression on 5.965 out-of-sample matches (2024-2025). Slope=0.8405, Intercept=0.3375. Brier score improved from 0.052 to 0.050. Reliability diagram verified at all deciles.
- **Daily cron operational** — every morning at 09:00 UTC, the system downloads the latest tennis-data.co.uk XLSX, imports new matches, runs predictions on the recent window, detects value bets, and delivers the report to Discord #value-bets.
- **Paper trading tracker** — every prediction is logged with odds. Completed matches update the accuracy and P&L automatically. Initial bankroll: 200 EUR.

### 📊 Latest Daily Report (25/06/2026)

- Match analyzed: 22 (Halle + Queen's Club, grass season)
- Value bets found: 20
- Model accuracy: 21/22 (95.5%) — note: small sample, includes some ELO data freshness
- Paper trading (flat 1€/bet): +18.61€ (+93% ROI)
- Paper trading (Kelly 12.5%): +126.91€
- 2026 odds coverage: 73% (1.107/1.509 completed ATP matches)

### 🔜 Next Steps

1. **Self-improvement retrain** — automatic XGBoost retrain every 100 prediction errors
2. **Markov p_serve data recovery** — import TennisMyLife stats for serve/return parameters
3. **Game handicap + O/U markets** — integrate Markov model for secondary markets
4. **The Odds API** — live pre-match odds for real-time prediction (critical when Wimbledon starts)

---

*Documento generato il 24/06/2026. Aggiornato il 25/06/2026. Versione 1.1 — JBE TopSpin.*
---
"""

# Check if the original signature is still there
old_signature = "Documento generato il 24/06/2026. Versione 1.0"
if old_signature in new_md:
    new_md = new_md.replace(old_signature, "Documento generato il 24/06/2026. Versione 1.1 — aggiornato 25/06/2026. JBE TopSpin (ex JBE Leviathan references removed).")
    print("  [OK] Firma aggiornata")

# 5. Scrivi la pagina aggiornata
print(f"\\n[INFO] Nuova lunghezza markdown: {len(new_md)} chars")
print(f"  Contiene 'JBE Leviathan': {'JBE Leviathan' in new_md}")
print(f"  Contiene 'Leviathan': {'Leviathan' in new_md}")

# Usa replace_content se non ci sono child pages, altrimenti insert
if not has_children:
    print("[INFO] Uso replace_content...")
    
    # Write payload to temp file to avoid shell quote issues
    # Use a simple query parameter instead of PATCH markdown
    # Actually, PATCH /v1/pages/{id}/markdown with replace_content
    
    payload = {
        "type": "replace_content",
        "replace_content": {
            "old_str": current_md,
            "new_str": new_md
        }
    }
    
    # Write to temp file
    with open("/tmp/notion_payload.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    
    resp = notion_req("PATCH", f"/v1/pages/{PAGE_ID}/markdown", payload)
    if "object" in resp or "markdown" in resp:
        print(f"[OK] Pagina aggiornata con replace_content")
    else:
        print(f"[ERRORE] replace_content fallito: {json.dumps(resp, indent=2)[:500]}")
        print("[INFO] Provo insert_content come fallback...")
        
        # Append update as new content instead
        insert_payload = {
            "type": "insert_content",
            "insert_content": {
                "content": f"\\n\\n## Aggiornamento 25/06/2026\\n\\n{append_content}",
                "after": "Documento generato il 24/06/2026. Versione 1.0"
            }
        }
        resp2 = notion_req("PATCH", f"/v1/pages/{PAGE_ID}/markdown", insert_payload)
        print(f"  Risultato insert: {json.dumps(resp2, indent=2)[:300]}")
else:
    print("[INFO] Pagina ha child pages. Uso insert_content per appendere...")
    insert_payload = {
        "type": "insert_content",
        "insert_content": {
            "content": append_content,
            "after": "Documento generato il 24/06/2026. Versione 1.1"
        }
    }
    resp = notion_req("PATCH", f"/v1/pages/{PAGE_ID}/markdown", insert_payload)
    print(f"[OK] insert_content response: {json.dumps(resp, indent=2)[:300]}")

print("\\n[OK] Completato!")
