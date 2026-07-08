#!/usr/bin/env python3
"""
JBE TopSpin — Paper Portfolio Schema + Sync
============================================
Crea e gestisce la tabella paper_portfolio per il paper trading tracker.
Ogni value bet viene salvata con stato, risultato e P&L.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from database import TennisDatabase
from config import DB_PATH

PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER REFERENCES tennis_matches(id) ON DELETE SET NULL,
    match_date DATE NOT NULL,
    tournament TEXT,
    surface TEXT,
    player1 TEXT NOT NULL,
    player2 TEXT NOT NULL,
    selection TEXT NOT NULL,        -- Giocatore scommesso
    market TEXT DEFAULT 'match_winner',
    odds REAL NOT NULL,             -- Quota al momento della bet
    model_prob REAL NOT NULL,       -- Probabilita' modello
    edge REAL NOT NULL,             -- Edge calcolato
    stake REAL NOT NULL,            -- Stake Kelly
    bankroll_before REAL,           -- Bankroll prima della bet
    bankroll_after REAL,            -- Bankroll dopo la bet
    status TEXT DEFAULT 'pending',  -- pending, won, lost, void
    result REAL,                    -- Profitto/perdita (positivo=win, negativo=loss)
    bookmaker TEXT,                 -- Fonte quota (Pinnacle, Bet365, OddsAPI)
    source TEXT DEFAULT 'odds_api',  -- odds_api, tennis_data, manual
    confidence TEXT,                -- HIGH, MEDIUM, LOW
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    settled_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_portfolio_date ON paper_portfolio(match_date);
CREATE INDEX IF NOT EXISTS idx_portfolio_status ON paper_portfolio(status);
CREATE INDEX IF NOT EXISTS idx_portfolio_match ON paper_portfolio(match_id);
"""


def init_schema(db=None):
    """Crea la tabella paper_portfolio se non esiste."""
    own_db = db is None
    if own_db:
        db = TennisDatabase(DB_PATH)
    db.conn.executescript(PAPER_SCHEMA)
    db.conn.commit()
    
    count = db.conn.execute("SELECT COUNT(*) FROM paper_portfolio").fetchone()[0]
    print(f"[INFO] paper_portfolio: {count} bets registrate")
    if own_db:
        db.close()
    return count


def add_bet(db, match_id, match_date, tournament, surface,
            player1, player2, selection, odds, model_prob, edge,
            stake, bankroll_before, market="match_winner",
            bookmaker="OddsAPI", confidence="MEDIUM", source="odds_api",
            match_datetime=None, odds_source=None, notes=None):
    """Registra una nuova bet nel portfolio."""
    bankroll_after = bankroll_before - stake  # Sottratto fino al settlement
    
    db.conn.execute("""
        INSERT INTO paper_portfolio
        (match_id, match_date, match_datetime, tournament, surface,
         player1, player2, selection, market, odds, model_prob, edge,
         stake, bankroll_before, bankroll_after, status,
         bookmaker, odds_source, source, confidence, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                ?, ?, ?, ?, ?)
    """, (
        match_id,
        match_date.isoformat() if hasattr(match_date, 'isoformat') else str(match_date),
        match_datetime,
        tournament or "", surface or "",
        player1, player2, selection, market,
        float(odds), float(model_prob), float(edge),
        float(stake), float(bankroll_before), float(bankroll_after),
        bookmaker, odds_source, source, confidence, notes,
    ))
    db.conn.commit()


def settle_bets(db):
    """
    Aggiorna lo stato delle bet pending in base ai risultati match.
    Supporta 3 mercati: match_winner, game_handicap, over_under.
    
    Rounds 1: bets con match_id diretto (daily report / tennis_data)
    Round 2: bets senza match_id (odds_api_live) — match per nome giocatore + data
    
    Returns:
        (n_settled, total_pnl) 
    """
    n_settled = 0
    total_pnl = 0.0
    
    # === Round 1: bets con match_id ===
    pending = db.conn.execute("""
        SELECT pp.id, pp.match_id, pp.selection, pp.stake, pp.odds,
               pp.player1, pp.player2, pp.bankroll_before, pp.market,
               pp.match_date
        FROM paper_portfolio pp
        WHERE pp.status = 'pending' AND pp.match_id IS NOT NULL
    """).fetchall()
    
    for p in pending:
        match = db.conn.execute("""
            SELECT m.winner_id, m.w_games, m.l_games,
                   w.name as wname, l.name as lname
            FROM tennis_matches m
            JOIN players w ON w.id=m.winner_id
            JOIN players l ON l.id=m.loser_id
            WHERE m.id=?
        """, (p["match_id"],)).fetchone()
        
        if not match:
            continue
        
        market = p["market"] or "match_winner"
        won = _settle_market(match, market, p["selection"])
        
        _apply_settlement(db, p, won)
        if won:
            total_pnl += p["stake"] * (p["odds"] - 1)
        else:
            total_pnl -= p["stake"]
        n_settled += 1
    
    # === Round 2: bets senza match_id (odds_api_live) — match per nome ===
    pending_no_id = db.conn.execute("""
        SELECT pp.id, pp.selection, pp.stake, pp.odds,
               pp.player1, pp.player2, pp.match_date, pp.market
        FROM paper_portfolio pp
        WHERE pp.status = 'pending' AND pp.match_id IS NULL
    """).fetchall()
    
    for p in pending_no_id:
        match = _find_match_by_names(db, p["player1"], p["player2"], p["match_date"])
        if not match:
            continue
        
        # Found matching match — update match_id first
        db.conn.execute("UPDATE paper_portfolio SET match_id=? WHERE id=?",
                       (match["id"], p["id"]))
        db.conn.commit()
        
        market = p["market"] or "match_winner"
        won = _settle_market(match, market, p["selection"])
        
        _apply_settlement(db, p, won)
        if won:
            total_pnl += p["stake"] * (p["odds"] - 1)
        else:
            total_pnl -= p["stake"]
        n_settled += 1
    
    if n_settled > 0:
        db.conn.commit()
    
    return n_settled, total_pnl


def _find_match_by_names(db, p1_name, p2_name, match_date_str):
    """Cerca un match nel DB per nomi giocatori + data."""
    if not p1_name or not p2_name:
        return None
    
    # Estrai cognomi per fuzzy match
    p1_parts = p1_name.strip().split()
    p2_parts = p2_name.strip().split()
    p1_surname = p1_parts[-1].lower() if p1_parts else ""
    p2_surname = p2_parts[-1].lower() if p2_parts else ""
    
    if not p1_surname or not p2_surname:
        return None
    
    # Cerca match con data + cognomi
    candidates = db.conn.execute("""
        SELECT m.id, m.winner_id, m.w_games, m.l_games,
               w.name as wname, l.name as lname
        FROM tennis_matches m
        JOIN players w ON w.id=m.winner_id
        JOIN players l ON l.id=m.loser_id
        WHERE m.match_date = ?
    """, (match_date_str,)).fetchall()
    
    for c in candidates:
        w_lower = c["wname"].lower()
        l_lower = c["lname"].lower()
        # Check if both surnames appear in the match (either order)
        if (p1_surname in w_lower and p2_surname in l_lower) or \
           (p1_surname in l_lower and p2_surname in w_lower):
            return dict(c)  # Convert Row to dict for dict access
    
    return None


def _settle_market(match, market, selection):
    """Valuta se una bet e' vinta o persa per un dato mercato."""
    won = False
    
    if market == "match_winner":
        if match["wname"] and selection:
            sel_surname = selection.split()[-1].lower() if " " in selection else selection.lower()
            win_name = match["wname"].lower()
            if sel_surname in win_name:
                won = True
    
    elif market == "game_handicap":
        sel = selection.strip()
        import re as _re
        hc_match = _re.search(r'([+-]\d+\.?\d*)$', sel)
        if hc_match:
            handicap = float(hc_match.group(1))
            player_name = sel[:hc_match.start()].strip()
            
            p_games = None
            opp_games = None
            
            if player_name.lower() in match["wname"].lower():
                p_games = match["w_games"] or 0
                opp_games = match["l_games"] or 0
            elif player_name.lower() in match["lname"].lower():
                p_games = match["l_games"] or 0
                opp_games = match["w_games"] or 0
            
            if p_games is not None and opp_games is not None:
                if p_games + handicap > opp_games:
                    won = True
    
    elif market == "over_under":
        sel = selection.strip()
        import re as _re
        ou_match = _re.search(r'([\d.]+)$', sel)
        if ou_match:
            threshold = float(ou_match.group(1))
            total_games = (match["w_games"] or 0) + (match["l_games"] or 0)
            
            if sel.lower().startswith("o"):
                won = total_games > threshold
            else:
                won = total_games < threshold
    
    return won


def _apply_settlement(db, pending, won):
    """Applica settlement a una bet pending."""
    if won:
        result = pending["stake"] * (pending["odds"] - 1)
        status = "won"
    else:
        result = -pending["stake"]
        status = "lost"
    
    bankroll_after = pending["bankroll_before"] + result
    
    db.conn.execute("""
        UPDATE paper_portfolio
        SET status=?, result=?, bankroll_after=?,
            settled_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (status, result, bankroll_after, pending["id"]))


def get_portfolio_summary(db=None, initial_bankroll=200.0):
    """Calcola statistiche riassuntive del portfolio."""
    if db is None:
        db = TennisDatabase(DB_PATH)
    
    total = db.conn.execute("SELECT COUNT(*) FROM paper_portfolio").fetchone()[0]
    settled = db.conn.execute("SELECT COUNT(*) FROM paper_portfolio WHERE status IN ('won','lost')").fetchone()[0]
    pending = db.conn.execute("SELECT COUNT(*) FROM paper_portfolio WHERE status='pending'").fetchone()[0]
    
    if settled == 0:
        return {
            "total_bets": total, "settled": 0, "pending": pending,
            "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0,
            "current_bankroll": initial_bankroll, "roi": 0,
            "initial_bankroll": initial_bankroll, "avg_edge": 0,
        }
    
    wins = db.conn.execute("SELECT COUNT(*) FROM paper_portfolio WHERE status='won'").fetchone()[0]
    losses = db.conn.execute("SELECT COUNT(*) FROM paper_portfolio WHERE status='lost'").fetchone()[0]
    
    total_staked = db.conn.execute("SELECT COALESCE(SUM(stake), 0) FROM paper_portfolio").fetchone()[0]
    total_pnl = db.conn.execute("SELECT COALESCE(SUM(result), 0) FROM paper_portfolio WHERE status IN ('won','lost')").fetchone()[0]
    
    current_bankroll = initial_bankroll + total_pnl
    roi = (total_pnl / initial_bankroll * 100) if initial_bankroll > 0 else 0
    
    avg_edge_settled = db.conn.execute("""
        SELECT COALESCE(AVG(edge), 0) FROM paper_portfolio WHERE status IN ('won','lost')
    """).fetchone()[0]
    
    return {
        "total_bets": total,
        "settled": settled,
        "pending": pending,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / settled * 100, 1) if settled > 0 else 0,
        "total_staked": round(total_staked, 2),
        "total_pnl": round(total_pnl, 2),
        "current_bankroll": round(current_bankroll, 2),
        "roi": round(roi, 1),
        "initial_bankroll": initial_bankroll,
        "avg_edge": round(avg_edge_settled * 100, 1),
    }


def get_portfolio_timeline(db=None, initial_bankroll=200.0):
    """Ritorna la timeline P&L giornaliera per il grafico."""
    if db is None:
        db = TennisDatabase(DB_PATH)
    
    rows = db.conn.execute("""
        SELECT match_date, 
               COUNT(*) as n_bets,
               SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) as losses,
               COALESCE(SUM(result), 0) as daily_pnl
        FROM paper_portfolio
        WHERE status IN ('won','lost')
        GROUP BY match_date
        ORDER BY match_date
    """).fetchall()
    
    timeline = []
    cumulative = initial_bankroll
    for row in rows:
        cumulative += row["daily_pnl"]
        timeline.append({
            "date": row["match_date"],
            "n_bets": row["n_bets"],
            "wins": row["wins"],
            "losses": row["losses"],
            "daily_pnl": round(row["daily_pnl"], 2),
            "cumulative_pnl": round(cumulative, 2),
        })
    
    return timeline


if __name__ == "__main__":
    db = TennisDatabase(DB_PATH)
    n = init_schema(db)
    
    print("\nSettling pending bets...")
    settled, pnl = settle_bets(db)
    print(f"  Settled: {settled}, P&L: {pnl:+.2f} EUR")
    
    print("\nPortfolio Summary:")
    summary = get_portfolio_summary(db)
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"  {k}: {v}")
        else:
            print(f"  {k}: {v}")
    
    db.close()
