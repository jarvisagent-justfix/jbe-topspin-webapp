#!/usr/bin/env python3
"""JBE TopSpin — Portfolio Notion Sync
Aggiorna la pagina Paper Portfolio su Notion con dati reali.
Eseguito ogni 3 ore via cron (d2adb2046721).
"""
import sys, os, json, http.client, subprocess
from datetime import date

# All output to stderr - cron no_agent local delivery, keep clean
def log(msg: str = ""):
    print(msg, file=sys.stderr)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from database import TennisDatabase
from config import DB_PATH
from paper_portfolio import get_portfolio_summary, get_portfolio_timeline, settle_bets, init_schema

NOTION_PAGE_ID = "38acf515-23b3-8110-a4d5-e7b44096af96"

def get_api_key():
    result = subprocess.run(["grep", "^NOTION_API_KEY", "/opt/data/.env"], capture_output=True, text=True)
    return result.stdout.strip().split("=", 1)[1]

def notion_req(method, path, body=None):
    key = get_api_key()
    conn = http.client.HTTPSConnection("api.notion.com")
    headers = {"Authorization": f"Bearer {key}", "Notion-Version": "2025-09-03"}
    if body:
        headers["Content-Type"] = "application/json"
        conn.request(method, path, json.dumps(body).encode(), headers=headers)
    else:
        conn.request(method, path, headers=headers)
    resp = conn.getresponse()
    return json.loads(resp.read().decode())

def main():
    db = TennisDatabase(DB_PATH)
    init_schema(db)
    
    # 1. Settle pending bets
    settled, pnl = settle_bets(db)
    
    # 2. Get portfolio stats
    summary = get_portfolio_summary(db)
    timeline = get_portfolio_timeline(db)
    
    log(f"=== JBE TopSpin Portfolio Sync ===")
    log(f"Bets settled: {settled}, P&L: {pnl:+.2f}")
    log(f"Total bets: {summary['total_bets']}")
    log(f"Pending: {summary['pending']}")
    log(f"Bankroll: {summary['current_bankroll']} EUR (ROI: {summary['roi']:.1f}%)")
    log(f"Win rate: {summary['win_rate']:.1f}%")
    
    # 3. Get breakdown per market
    by_market = db.conn.execute("""
        SELECT market,
               COUNT(*) as n,
               SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) as losses,
               COALESCE(SUM(result), 0) as total_pnl
        FROM paper_portfolio
        WHERE status IN ('won','lost')
        GROUP BY market
    """).fetchall()
    
    # 4. Build markdown update for Notion
    today = date.today().isoformat()
    
    stats_rows = "\n".join([
        f"| Bankroll Iniziale | {summary['initial_bankroll']} EUR |",
        f"| Bankroll Corrente | {summary['current_bankroll']} EUR |",
        f"| ROI | {summary['roi']:+.1f}% |",
        f"| Win Rate | {summary['win_rate']:.1f}% |",
        f"| Value Bets Totali | {summary['settled']} (di cui {summary['pending']} pending) |",
        f"| Edge Medio | {summary['avg_edge']:.1f}% |",
    ])
    
    market_rows = ""
    for m in by_market:
        mkt = m["market"].replace("_", " ").title()
        market_rows += f"| {mkt} | {m['n']} | {m['wins']} | {m['losses']} | {m['total_pnl']:+.2f}€ |\n"
    if not market_rows:
        market_rows = "| - | 0 | 0 | 0 | 0 |\n"
    
    # Build P&L curve
    pnl_points = ""
    if timeline:
        for pt in timeline[-10:]:  # Last 10 days
            pnl_points += f"  {pt['date']}: {pt['cumulative_pnl']:.0f}€ ({pt['n_bets']} bets)\n"
    else:
        pnl_points = "  (Ancora nessuna bet settlementata — i dati appariranno dopo Wimbledon)\n"
    
    # Mostra solo le bet del batch piu' recente (ultima sessione di analisi)
    last_batch_date = db.conn.execute("""
        SELECT date(MAX(created_at)) FROM paper_portfolio
    """).fetchone()[0]
    
    markdown = (
        f"# Paper Portfolio — Portafoglio Virtuale\n\n"
        f"<callout icon=\"💰\" color=\"green_bg\">\n"
        f"\t**Ultimo sync: {today}** | "
        f"Bankroll: {summary['current_bankroll']:.0f} EUR | "
        f"ROI: {summary['roi']:+.1f}% | "
        f"Settled: {summary['settled']} bets\n"
        f"</callout>\n\n"
        f"## Stato Attuale\n\n"
        f"| Metrica | Valore |\n"
        f"|---|---|\n"
        f"{stats_rows}\n"
        f"\n## Cronologia Scommesse — Ultimo batch: {last_batch_date or '?'}\n\n"
        f"| # | Data Giocata | Data Incontro | Match | Mercato | Selezione | Quota | Edge | Stake | Esito | P&L |\n"
        f"|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    
    # Add recent bets (solo ultimo batch)
    recent = db.conn.execute("""
        SELECT created_at, match_date, match_datetime, player1, player2, market, selection, odds, edge, stake,
               status, result
        FROM paper_portfolio
        WHERE date(created_at) = (SELECT date(MAX(created_at)) FROM paper_portfolio)
        ORDER BY id DESC LIMIT 30
    """).fetchall()
    
    def format_selection(sel, market):
        """Formatta la selezione in modo leggibile con il tipo di giocata completo."""
        if not sel:
            return "?"
        if not market or market == "match_winner":
            return f"{sel} Vittoria"
        elif market == "game_handicap":
            # sel es: "Moez Echargui +5.5"
            import re
            m = re.search(r'([+-]\d+\.?\d*)$', sel)
            if m:
                player = sel[:m.start()].strip()
                hc = m.group(1)
                return f"{player} Handicap {hc}"
            return f"{sel} Handicap"
        elif market == "over_under":
            import re
            m = re.search(r'([\d.]+)$', sel)
            if m:
                val = m.group(1)
                if sel.lower().startswith('o'):
                    return f"Totale Over {val}"
                else:
                    return f"Totale Under {val}"
            return f"Totale {sel}"
        return sel
    
    for i, b in enumerate(recent):
        # Data Giocata = created_at (data in cui e' stata piazzata la bet)
        bet_date = str(b["created_at"] or "?")[:10]  # YYYY-MM-DD
        
        # Data Incontro = data del match
        match_d = b["match_datetime"] or b["match_date"] or "?"
        
        p1 = (b["player1"] or "?")[:15]
        p2 = (b["player2"] or "?")[:15]
        mkt = (b["market"] or "match_winner").replace("_", " ").title()[:15]
        sel = format_selection(b["selection"], b["market"])[:30]
        odd = f"{b['odds']:.2f}" if b["odds"] else "?"
        edge = f"{b['edge']*100:.1f}%" if b["edge"] else "?"
        stake = f"{b['stake']:.2f}" if b["stake"] else "?"
        status = b["status"] or "pending"
        if b["result"] is not None:
            pnl = f"{b['result']:+.2f}€" if b["status"] == "won" else f"{b['result']:.2f}€"
        else:
            pnl = "-"
        markdown += f"| {i+1} | {bet_date} | {match_d} | {p1} vs {p2} | {mkt} | {sel} | {odd} | {edge} | {stake} | {status} | {pnl} |\n"
    
    if not recent:
        markdown += "| - | - | - | - | - | - | - | - | - | - |\n"
    
    markdown += (
        f"\n## Statistiche\n\n"
        f"| Metrica | Valore |\n"
        f"|---|---|\n"
        f"{stats_rows}\n"
        f"\n## Distribuzione per Mercato\n\n"
        f"| Mercato | Bets | Vinte | Perse | P&L |\n"
        f"|---|---|---|---|---|\n"
        f"{market_rows}\n"
        f"\n## Andamento Bankroll\n\n"
        f"```\n"
        f"Ultimi 10 punti P&L:\n"
        f"{pnl_points}"
        f"```\n\n"
        f"## Note\n"
        f"- Portafoglio **virtuale** — budget iniziale {summary['initial_bankroll']:.0f} EUR\n"
        f"- Staking: Kelly {12.5:.1f}% frazionale, max {5:.0f}% bankroll per bet\n"
        f"- Le scommesse vengono registrate solo per match futuri (Odds API live)\n"
        f"- I risultati vengono aggiornati quando tennis-data.co.uk pubblica i dati (24-48h ritardo)\n"
        f"- Settlement supporta 3 mercati: match winner, game handicap, over/under\n"
    )
    
    # Send to Notion
    payload = {
        "type": "update_content",
        "update_content": {
            "content": markdown
        }
    }
    
    # Use replace_content since the page has no child pages
    payload_replace = {
        "type": "replace_content",
        "replace_content": {
            "old_str": "# Paper Portfolio",
            "new_str": markdown
        }
    }
    
    try:
        result = notion_req("PATCH", f"/v1/pages/{NOTION_PAGE_ID}/markdown", payload_replace)
        if "error" in result:
            # Fallback: use insert_content 
            log(f"[WARN] replace_content failed: {result.get('error', {}).get('message', '?')[:100]}")
            payload_insert = {
                "type": "insert_content",
                "insert_content": {
                    "content": f"\n---\n<callout icon=\"🔄\" color=\"blue_bg\">\n\t**Auto-sync {today}** — Bankroll: {summary['current_bankroll']:.0f} EUR, ROI: {summary['roi']:+.1f}%, Bets: {summary['settled']}\n</callout>\n"
                }
            }
            result2 = notion_req("PATCH", f"/v1/pages/{NOTION_PAGE_ID}/markdown", payload_insert)
            log(f"  insert_content result: {'OK' if 'object' in result2 else 'ERR'}")
        else:
            log(f"[OK] Notion portfolio page updated")
    except Exception as e:
        log(f"[ERRORE] Notion sync: {e}")
    
    db.close()
    log(f"[OK] Portfolio sync completato.")

if __name__ == "__main__":
    main()
