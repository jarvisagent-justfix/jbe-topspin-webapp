#!/usr/bin/env python3
"""
JBE TopSpin — Sync Paper Portfolio to Notion
=============================================
Legge il paper_portfolio da SQLite e lo sincronizza con il database Notion.
Genera anche un grafico P&L e lo carica.

Uso: PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/sync_portfolio_notion.py
"""
import sys, os, json, subprocess, http.client
from datetime import date, datetime

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import TennisDatabase
from config import DB_PATH
from paper_portfolio import init_schema, settle_bets, get_portfolio_summary, get_portfolio_timeline

# Notion config
CONFIG_FILE = "/opt/data/jbe-tennis/data/notion_portfolio.json"

# Read API key
r = subprocess.run(["grep", "^NOTION_API_KEY", "/opt/data/.env"], capture_output=True, text=True)
NOTION_KEY = r.stdout.strip().split("=", 1)[1] if r.stdout else ""

def notion_req(method, path, body=None):
    conn = http.client.HTTPSConnection("api.notion.com")
    headers = {
        "Authorization": f"Bearer {NOTION_KEY}",
        "Notion-Version": "2025-09-03",
    }
    if body:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, ensure_ascii=False)
        conn.request(method, path, payload.encode(), headers=headers)
    else:
        conn.request(method, path, headers=headers)
    resp = conn.getresponse()
    return json.loads(resp.read().decode())


def load_config():
    """Carica la configurazione del database Notion."""
    if not os.path.exists(CONFIG_FILE):
        print("[ERRORE] Config non trovata: run create_notion_portfolio.py prima")
        return None
    with open(CONFIG_FILE) as f:
        return json.load(f)


def notion_page_id_for_match(db_id, date_str, p1, p2):
    """Cerca se esiste gia' una pagina Notion per questo match."""
    query = {
        "filter": {
            "and": [
                {"property": "Match", "title": {"contains": f"{p1} vs {p2}"}},
            ]
        }
    }
    resp = notion_req("POST", f"/v1/data_sources/{db_id}/query", query)
    for r in resp.get("results", []):
        # Check date matches approximately
        return r["id"]
    return None


def sync_bets_to_notion():
    """Sincronizza tutte le bet da SQLite a Notion."""
    config = load_config()
    if not config:
        return
    
    db_id = config.get("data_source_id")
    if not db_id:
        print("[ERRORE] data_source_id non trovato nel config")
        return
    
    db = TennisDatabase(DB_PATH)
    init_schema(db)
    
    # Settle bets first
    settled, pnl = settle_bets(db)
    if settled:
        print(f"  Settled {settled} bets, P&L {pnl:+.2f} EUR")
    
    # Read all bets from SQLite
    bets = db.conn.execute("""
        SELECT * FROM paper_portfolio ORDER BY match_date DESC
    """).fetchall()
    
    if not bets:
        print("  Nessuna bet da sincronizzare")
        db.close()
        return
    
    print(f"  Sincronizzazione {len(bets)} bet...")
    synced = 0
    for b in bets:
        # Check if already in Notion
        existing = notion_page_id_for_match(
            db_id, str(b["match_date"]), b["player1"], b["player2"]
        )
        
        title = f"{b['player1']} vs {b['player2']}"
        date_str = b["match_date"] if isinstance(b["match_date"], str) else b["match_date"].isoformat()
        pnl_val = float(b["result"]) if b["result"] is not None else 0
        
        properties = {
            "Match": {"title": [{"text": {"content": title}}]},
            "Date": {"date": {"start": date_str}},
            "Selection": {"rich_text": [{"text": {"content": b["selection"]}}]},
            "Market": {"select": {"name": b["market"] or "match_winner"}},
            "Odds": {"number": float(b["odds"])},
            "Model Prob": {"number": float(b["model_prob"])},
            "Edge": {"number": float(b["edge"])},
            "Stake": {"number": float(b["stake"])},
            "Status": {"select": {"name": b["status"]}},
            "P&L": {"number": float(pnl_val)},
            "Confidence": {"select": {"name": b["confidence"] or "MEDIUM"}},
            "Bookmaker": {"rich_text": [{"text": {"content": b["bookmaker"] or ""}}]},
            "Surface": {"select": {"name": b["surface"]}} if b["surface"] else None,
        }
        
        # Remove None properties
        properties = {k: v for k, v in properties.items() if v is not None}
        
        if existing:
            # Update existing
            notion_req("PATCH", f"/v1/pages/{existing}",
                       {"properties": properties})
        else:
            # Create new
            notion_req("POST", "/v1/pages", {
                "parent": {"database_id": db_id},
                "properties": properties,
            })
        synced += 1
    
    print(f"  {synced} bet sincronizzate su Notion")
    
    # Update summary page
    update_summary_page(db, db_id)
    
    db.close()
    return synced


def update_summary_page(db, db_id):
    """Cerca o crea una pagina di riepilogo sotto il database."""
    summary = get_portfolio_summary(db, initial_bankroll=200.0)
    timeline = get_portfolio_timeline(db)
    
    # Trova il database page ID (non data_source_id)
    config = load_config()
    db_page_id = config.get("database_id")
    
    if not db_page_id:
        return
    
    # Check if summary page exists
    search = notion_req("POST", "/v1/search", {"query": "Portfolio Summary"})
    summary_page = None
    for r in search.get("results", []):
        if r.get("object") == "page":
            props = r.get("properties", {})
            title_prop = props.get("title", props.get("Name", {}))
            title_text = ""
            for t in title_prop.get("title", []):
                title_text += t.get("text", {}).get("content", "")
            if "Portfolio Summary" in title_text:
                summary_page = r["id"]
                break
    
    # Build summary markdown
    md = f"""# 📊 Portfolio Summary — JBE TopSpin

**Aggiornato:** {datetime.now().strftime('%d/%m/%Y %H:%M')}

## Overview
| Metrica | Valore |
|---------|--------|
| Bankroll Iniziale | {summary['initial_bankroll']:.0f} EUR |
| Bankroll Corrente | {summary['current_bankroll']:.2f} EUR |
| ROI | {summary['roi']:+.1f}% |
| P&L Totale | {summary['total_pnl']:+.2f} EUR |
| Win Rate | {summary['win_rate']:.1f}% ({summary['wins']}V/{summary['losses']}P) |
| Edge Medio | {summary['avg_edge']:.1f}% |
| N. Scommesse | {summary['total_bets']} ({summary['settled']} chiuse, {summary['pending']} in corso) |

## P&L Timeline
| Data | Scommesse | Vinte | Perse | P&L Giornaliero | Bankroll |
|------|-----------|-------|-------|-----------------|----------|
"""
    for t in timeline[-20:]:  # Last 20 days
        md += f"| {t['date']} | {t['n_bets']} | {t['wins']} | {t['losses']} | {t['daily_pnl']:+.2f} EUR | {t['cumulative_pnl']:.2f} EUR |\n"
    
    md += f"""
## Recent Bets
"""
    for b in db.conn.execute("""
        SELECT match_date, player1, player2, selection, odds, edge, status, result
        FROM paper_portfolio ORDER BY id DESC LIMIT 10
    """).fetchall():
        status_icon = "✅" if b["status"] == "won" else ("❌" if b["status"] == "lost" else "⏳")
        result_str = f"{b['result']:+.2f} EUR" if b["result"] is not None else ""
        md += f"- {status_icon} {b['match_date']} | {b['player1']} vs {b['player2']} | **{b['selection']}** @{b['odds']:.2f} (edge: {b['edge']:.1%}) | {result_str}\n"
    
    if summary_page:
        # Update existing via markdown
        payload = {
            "type": "insert_content",
            "insert_content": {
                "content": f"\n\n## Aggiornamento {datetime.now().strftime('%H:%M')}\nROI: {summary['roi']:+.1f}% | Bankroll: {summary['current_bankroll']:.2f} EUR | Bets: {summary['settled']} chiuse, {summary['pending']} pending"
            }
        }
        resp = notion_req("PATCH", f"/v1/pages/{summary_page}/markdown", payload)
        print(f"  Summary page updated")
    else:
        # Create new summary page inside the portfolio database
        payload = {
            "parent": {"database_id": db_page_id},
            "icon": {"type": "emoji", "emoji": "📊"},
            "properties": {"title": [{"text": {"content": "Portfolio Summary"}}]},
            "markdown": md
        }
        resp = notion_req("POST", "/v1/pages", payload)
        if "id" in resp:
            print(f"  Summary page created")
        else:
            print(f"  [WARN] Summary page creation: {json.dumps(resp, indent=2)[:200]}")
    
    # Generate chart
    generate_pnl_chart(db, summary, timeline)


def generate_pnl_chart(db, summary, timeline):
    """Genera un grafico P&L usando matplotlib."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import numpy as np
    except ImportError:
        print("  [WARN] matplotlib non disponibile, skip chart")
        return
    
    if len(timeline) < 2:
        print("  [WARN] Troppi pochi dati per il grafico")
        return
    
    dates = [t["date"] for t in timeline]
    bankrolls = [t["cumulative_pnl"] for t in timeline]
    daily_pnl = [t["daily_pnl"] for t in timeline]
    
    try:
        date_objs = [datetime.strptime(d, "%Y-%m-%d") if isinstance(d, str) else d for d in dates]
    except (ValueError, TypeError):
        date_objs = list(range(len(dates)))
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={'height_ratios': [3, 1]})
    
    # P&L curve
    ax1.plot(date_objs, bankrolls, color='#2196F3', linewidth=2, marker='o')
    ax1.axhline(y=summary['initial_bankroll'], color='gray', linestyle='--', alpha=0.5, label=f"Start ({summary['initial_bankroll']} EUR)")
    ax1.fill_between(date_objs, summary['initial_bankroll'], bankrolls,
                     where=[b >= summary['initial_bankroll'] for b in bankrolls],
                     color='green', alpha=0.1)
    ax1.fill_between(date_objs, summary['initial_bankroll'], bankrolls,
                     where=[b < summary['initial_bankroll'] for b in bankrolls],
                     color='red', alpha=0.1)
    ax1.set_ylabel('Bankroll (EUR)', fontsize=11)
    ax1.set_title(f"JBE TopSpin — Paper Trading P&L", fontsize=14, fontweight='bold')
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    
    # Daily P&L bars
    colors = ['#4CAF50' if p >= 0 else '#F44336' for p in daily_pnl]
    ax2.bar(date_objs, daily_pnl, color=colors, alpha=0.7)
    ax2.axhline(y=0, color='gray', linestyle='-', alpha=0.3)
    ax2.set_ylabel('P&L Giornaliero (EUR)', fontsize=11)
    ax2.set_xlabel('Data', fontsize=11)
    ax2.grid(True, alpha=0.3)
    
    if isinstance(date_objs[0], datetime):
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        fig.autofmt_xdate()
    
    plt.tight_layout()
    
    # Save
    chart_path = "/opt/data/jbe-tennis/data/delivery/pnl_chart.png"
    plt.savefig(chart_path, dpi=120, bbox_inches='tight')
    plt.close()
    
    print(f"  Chart salvato: {chart_path}")
    
    # Upload to Notion
    if NOTION_KEY:
        upload_chart_to_notion(chart_path)


def upload_chart_to_notion(chart_path):
    """Carica il grafico P&L su Notion."""
    import urllib.request
    
    config = load_config()
    if not config:
        return
    
    db_page_id = config.get("database_id")
    if not db_page_id:
        return
    
    # Step 1: Create upload
    with open(chart_path, 'rb') as f:
        file_data = f.read()
    
    # Use the file_upload API
    create_body = {
        "filename": "pnl_chart.png",
        "content_type": "image/png",
        "size": len(file_data),
    }
    upload = notion_req("POST", "/v1/file_uploads", create_body)
    
    if "upload_url" not in upload:
        print(f"  [WARN] File upload creation failed: {json.dumps(upload)[:200]}")
        return
    
    # Step 2: PUT bytes
    req = urllib.request.Request(
        upload["upload_url"],
        data=file_data,
        headers={"Content-Type": "image/png"},
        method="PUT"
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"  [WARN] File upload PUT failed: {e}")
        return
    
    file_id = upload.get("file_upload_id")
    if not file_id:
        print("  [WARN] No file_upload_id")
        return
    
    # Step 3: Append image block to summary page
    search = notion_req("POST", "/v1/search", {"query": "Portfolio Summary"})
    summary_id = None
    for r in search.get("results", []):
        if r.get("object") == "page":
            summary_id = r["id"]
            break
    
    if not summary_id:
        return
    
    append_result = notion_req("PATCH", f"/v1/blocks/{summary_id}/children", {
        "children": [{
            "object": "block",
            "type": "image",
            "image": {
                "type": "file",
                "file": {"file_id": file_id}
            }
        }]
    })
    
    if "results" in append_result:
        print(f"  Chart uploaded to Notion!")
    else:
        print(f"  [WARN] Chart append: {json.dumps(append_result)[:200]}")


if __name__ == "__main__":
    print("=== Sync Portfolio to Notion ===\n")
    synced = sync_bets_to_notion()
    print(f"\n[OK] Sincronizzazione completata: {synced or 0} bet")
