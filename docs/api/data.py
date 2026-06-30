#!/usr/bin/env python3
"""JBE TopSpin Webapp — Genera JSON per il frontend.
Legge il DB SQLite e produce un data.json consumabile dalla PWA.

Uso: PYTHONPATH=src python3 webapp/api/data.py [--output webapp/data.json]
"""
import sys, os, json, math
from datetime import date, datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase


def load_matches_today(db, days_lookback=1, days_ahead=3):
    """Carica i match recenti con odds e predizioni."""
    today = date.today()
    start = today - timedelta(days=days_lookback)
    end = today + timedelta(days=days_ahead)
    start_s = start.isoformat()
    end_s = end.isoformat()

    rows = db.conn.execute("""
        SELECT m.id, m.match_date, m.tournament, m.surface, m.round,
               m.best_of, m.tour_level, m.score,
               m.winner_id, m.loser_id,
               w.name as winner_name, l.name as loser_name,
               w.country as winner_country, l.country as loser_country,
               m.winner_rank, m.loser_rank,
               m.w_sets, m.l_sets, m.w_games, m.l_games
        FROM tennis_matches m
        JOIN players w ON w.id = m.winner_id
        JOIN players l ON l.id = m.loser_id
        WHERE m.match_date >= ? AND m.match_date <= ?
        ORDER BY m.match_date ASC, m.id ASC
    """, (start_s, end_s)).fetchall()

    matches = []
    for r in rows:
        # Carica odds per questo match
        odds_rows = db.conn.execute("""
            SELECT bookmaker, odds_winner, odds_loser, odds_2_0_fav, odds_2_1_fav,
                   handicap_line, handicap_odds_fav, total_line, over_odds, under_odds
            FROM tennis_odds WHERE match_id = ?
        """, (r["id"],)).fetchall()

        odds_list = []
        for o in odds_rows:
            odds_list.append({
                "bookmaker": o["bookmaker"],
                "winner": o["odds_winner"],
                "loser": o["odds_loser"],
                "set_2_0_fav": o["odds_2_0_fav"],
                "set_2_1_fav": o["odds_2_1_fav"],
                "handicap": {
                    "line": o["handicap_line"],
                    "odds_fav": o["handicap_odds_fav"]
                },
                "total": {
                    "line": o["total_line"],
                    "over": o["over_odds"],
                    "under": o["under_odds"]
                }
            })

        best_odds = None
        for o in odds_list:
            if o["winner"] and o["loser"]:
                if best_odds is None or o["winner"] < best_odds["winner"]:
                    best_odds = {"winner": o["winner"], "loser": o["loser"], "bookmaker": o["bookmaker"]}

        status = "completed" if r["score"] else ("live" if False else "upcoming")
        is_completed = bool(r["score"])

        matches.append({
            "id": r["id"],
            "date": r["match_date"],
            "tournament": r["tournament"],
            "surface": r["surface"],
            "round": r["round"],
            "best_of": r["best_of"],
            "tour_level": r["tour_level"],
            "status": status,
            "players": {
                "p1": {"name": r["winner_name"], "country": r["winner_country"], "rank": r["winner_rank"]},
                "p2": {"name": r["loser_name"], "country": r["loser_country"], "rank": r["loser_rank"]}
            },
            "result": {
                "score": r["score"],
                "w_sets": r["w_sets"],
                "l_sets": r["l_sets"],
                "w_games": r["w_games"],
                "l_games": r["l_games"]
            } if is_completed else None,
            "odds": {
                "best": best_odds,
                "all": odds_list[:3]  # max 3 bookmaker
            }
        })
    return matches


def load_portfolio_upcoming(db, days_ahead=7):
    """Carica match in arrivo dal paper_portfolio (Odds API, non ancora importati in tennis_matches)."""
    today = date.today()
    end = (today + timedelta(days=days_ahead)).isoformat()
    start = (today - timedelta(days=1)).isoformat()

    rows = db.conn.execute("""
        SELECT DISTINCT p.match_date, p.player1, p.player2, p.tournament,
               p.market, p.odds, p.model_prob, p.edge, p.confidence
        FROM paper_portfolio p
        WHERE p.match_id IS NULL
          AND p.match_date >= ? AND p.match_date <= ?
          AND p.status = 'pending'
        ORDER BY p.match_date ASC
    """, (start, end)).fetchall()

    matches = []
    seen_keys = set()
    for r in rows:
        key = (r["match_date"], r["player1"], r["player2"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        matches.append({
            "id": None,
            "date": r["match_date"],
            "tournament": r["tournament"] or "ATP",
            "surface": None,
            "round": None,
            "best_of": 3,
            "tour_level": None,
            "status": "upcoming",
            "players": {
                "p1": {"name": r["player1"], "country": None, "rank": None},
                "p2": {"name": r["player2"], "country": None, "rank": None},
            },
            "result": None,
            "odds": {
                "best": {"winner": r["odds"], "loser": None, "bookmaker": "Odds API"},
                "all": []
            },
            "model_prob": r["model_prob"],
            "edge": r["edge"],
            "confidence": r["confidence"],
            "source": "odds_api",
        })
    return matches


def load_value_bets(db, days=2):
    """Carica value bets recenti dal paper portfolio."""
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()

    rows = db.conn.execute("""
        SELECT * FROM paper_portfolio
        WHERE match_date >= ?
        ORDER BY match_date ASC, edge DESC
    """, (start,)).fetchall()

    bets = []
    for r in rows:
        # Arricchisci con tournament/surface dal DB se match_id presente
        tournament = r["tournament"] or ""
        surface = r["surface"] or ""
        if r["match_id"] and not tournament:
            m = db.conn.execute("SELECT tournament, surface, round, tour_level FROM tennis_matches WHERE id=?", (r["match_id"],)).fetchone()
            if m:
                tournament = m["tournament"] or ""
                surface = m["surface"] or ""

        # Formatta market in italiano
        market_label = {
            "match_winner": "Vincitore",
            "game_handicap": "Handicap Game",
            "over_under": "Totale Game",
        }.get(r["market"], r["market"] or "")

        # Formatta selezione
        selection = r["selection"] or ""
        if r["market"] == "match_winner" and selection:
            selection = f"{selection} vince"
        elif r["market"] == "over_under" and selection:
            selection = selection.replace("O/U", "Totale").replace("Over", "Oltre").replace("Under", "Sotto")
        elif r["market"] == "game_handicap" and selection:
            selection = f"{selection}"

        resolved = r["result"] is not None
        bets.append({
            "id": r["id"],
            "match_id": r["match_id"],
            "match_date": r["match_date"],
            "match_datetime": r["match_datetime"] or "",
            "player1": r["player1"],
            "player2": r["player2"],
            "selection": selection,
            "selection_raw": r["selection"] or "",
            "odds": r["odds"],
            "model_prob": r["model_prob"],
            "edge": r["edge"],
            "edge_pct": round(r["edge"] * 100, 1),
            "stake": r["stake"],
            "confidence": r["confidence"] or "",
            "market": r["market"],
            "market_label": market_label,
            "tournament": tournament,
            "surface": surface,
            "bookmaker": r["bookmaker"] or "",
            "status": r["status"],
            "result": r["result"],
            "resolved": resolved,
            "profit": r["result"],
            "reason": r["notes"] or ""
        })
    return bets


def load_bankroll_stats(db):
    """Statistiche bankroll calcolate dal paper portfolio."""
    today = date.today()

    # Bankroll calcolato dal portfolio
    initial = 200.0
    row = db.conn.execute("""
        SELECT COALESCE(SUM(stake), 0) as total_staked,
               COALESCE(SUM(result), 0) as total_pnl
        FROM paper_portfolio WHERE result IS NOT NULL AND status IN ('won','lost')
    """).fetchone()
    current_bankroll = initial + (row["total_pnl"] if row else 0)
    peak_bankroll = initial

    # Peak calcolato
    peak_row = db.conn.execute("""
        SELECT MIN(bankroll_before) as min_bb FROM paper_portfolio WHERE bankroll_before IS NOT NULL
    """).fetchone()
    if peak_row and peak_row["min_bb"] is not None:
        peak_bankroll = max(initial, peak_row["min_bb"] + abs(row["total_pnl"]) if row else initial)

    # Consecutive losses
    losses_row = db.conn.execute("""
        SELECT result FROM paper_portfolio
        WHERE status IN ('won','lost') AND result != 0
        ORDER BY id DESC LIMIT 10
    """).fetchall()
    consecutive = 0
    for r in losses_row:
        if r["result"] < 0:
            consecutive += 1
        else:
            break

    # Performance ultimi 30 giorni
    monthly = db.conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN result > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result < 0 THEN 1 ELSE 0 END) as losses,
               SUM(result) as pnl,
               SUM(stake) as total_staked
        FROM paper_portfolio
        WHERE result IS NOT NULL AND status IN ('won','lost') AND match_date >= ?
    """, ((today - timedelta(days=30)).isoformat(),)).fetchone()

    drawdown = (peak_bankroll - current_bankroll) / peak_bankroll * 100 if peak_bankroll > 0 else 0
    total_bets = monthly["total"] if monthly else 0
    wins = monthly["wins"] if monthly else 0
    losses = monthly["losses"] if monthly else 0
    pnl = monthly["pnl"] if monthly else 0
    total_staked = monthly["total_staked"] if monthly else 0

    return {
        "current": round(current_bankroll, 2),
        "peak": round(peak_bankroll, 2),
        "drawdown": round(drawdown, 1),
        "consecutive_losses": consecutive,
        "total_bets": total_bets,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total_bets * 100, 1) if total_bets > 0 else 0,
        "pnl_30d": round(pnl, 2),
        "roi_30d": round(pnl / total_staked * 100, 1) if total_staked > 0 else 0,
    }


def load_elo_rankings(db, limit=20):
    """Top ELO rankings."""
    rows = db.conn.execute("""
        SELECT e.*, p.name, p.country FROM elo_ratings e
        JOIN players p ON p.id = e.player_id
        WHERE e.id IN (SELECT MAX(id) FROM elo_ratings GROUP BY player_id)
        ORDER BY e.rating_overall DESC
        LIMIT ?
    """, (limit,)).fetchall()

    rankings = []
    for i, r in enumerate(rows, 1):
        rankings.append({
            "rank": i,
            "name": r["name"],
            "country": r["country"],
            "elo_overall": round(r["rating_overall"], 1),
            "elo_hard": round(r["rating_hard"], 1),
            "elo_clay": round(r["rating_clay"], 1),
            "elo_grass": round(r["rating_grass"], 1),
            "matches": r["matches_played"]
        })
    return rankings


def load_last_report(db):
    """Carica l'ultimo report generato."""
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    report_dir = os.path.join(base, "data", "delivery")
    files = sorted([f for f in os.listdir(report_dir) if f.endswith(".txt")], reverse=True)
    if files:
        path = os.path.join(report_dir, files[0])
        with open(path) as f:
            return f.read()
    return None


def load_bet_history(db, limit=50):
    """Carica storico scommesse risolte."""
    rows = db.conn.execute("""
        SELECT id, match_date, tournament, player1, player2, selection, market,
               odds, model_prob, edge, stake, result, status, confidence
        FROM paper_portfolio
        WHERE status IN ('won','lost','push') AND result IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()

    history = []
    for r in rows:
        profit = r["result"] if r["result"] is not None else 0
        history.append({
            "id": r["id"],
            "date": r["match_date"],
            "player1": r["player1"],
            "player2": r["player2"],
            "selection": r["selection"],
            "market": r["market"],
            "odds": r["odds"],
            "model_prob": r["model_prob"],
            "edge": r["edge"],
            "stake": r["stake"],
            "profit": round(profit, 2),
            "status": r["status"],
            "confidence": r["confidence"],
        })
    return history


def load_bankroll_history(db):
    """Bankroll progression giorno per giorno."""
    rows = db.conn.execute("""
        SELECT match_date,
               SUM(result) as day_pnl,
               SUM(stake) as day_staked,
               COUNT(*) as total,
               SUM(CASE WHEN result > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result < 0 THEN 1 ELSE 0 END) as losses
        FROM paper_portfolio
        WHERE status IN ('won','lost','push') AND result IS NOT NULL
        GROUP BY match_date
        ORDER BY match_date ASC
    """).fetchall()

    initial = 200.0
    cumulative = initial
    history = []
    for r in rows:
        cumulative += r["day_pnl"]
        history.append({
            "date": r["match_date"],
            "bankroll": round(cumulative, 2),
            "pnl": round(r["day_pnl"], 2),
            "staked": round(r["day_staked"], 2),
            "bets": r["total"],
            "wins": r["wins"],
            "losses": r["losses"],
        })
    return history


def build_data():
    db = TennisDatabase()

    today = date.today().isoformat()

    BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Carica match dal DB storico
    matches_db_today = load_matches_today(db, days_lookback=0, days_ahead=0)
    matches_db_upcoming = load_matches_today(db, days_lookback=0, days_ahead=3)
    matches_db_recent = load_matches_today(db, days_lookback=2, days_ahead=0)

    # Stato API keys
    api_status = {"ok": True, "exhausted": False}
    exhausted_path = os.path.join(BASE, "data", "cache", "api_exhausted.json")
    if os.path.exists(exhausted_path):
        try:
            with open(exhausted_path) as f:
                status = json.load(f)
                api_status = {
                    "ok": False,
                    "exhausted": True,
                    "since": status.get("since", "?"),
                    "keys_tried": status.get("keys_tried", 0),
                }
        except Exception:
            pass

    # Aggiunge match in arrivo dal paper_portfolio (Odds API, non ancora importati)
    portfolio_upcoming = load_portfolio_upcoming(db)
    merged_upcoming = matches_db_upcoming + portfolio_upcoming
    # Evita duplicati per match con stessi giocatori e data
    seen = set()
    deduped = []
    for m in merged_upcoming:
        key = (m["date"], m["players"]["p1"]["name"], m["players"]["p2"]["name"])
        if key not in seen:
            seen.add(key)
            deduped.append(m)
    merged_upcoming = sorted(deduped, key=lambda x: x["date"])

    data = {
        "generated_at": datetime.now().isoformat(),
        "date": today,
        "matches": {
            "today": matches_db_today,
            "upcoming": merged_upcoming,
            "recent": matches_db_recent,
        },
        "value_bets": load_value_bets(db),
        "bankroll": load_bankroll_stats(db),
        "bet_history": load_bet_history(db, limit=50),
        "bankroll_history": load_bankroll_history(db),
        "last_report": load_last_report(db),
        "api_status": api_status,
    }
    return data


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    data = build_data()
    output = args.output
    if not output:
        output = os.path.join(os.path.dirname(__file__), "data.json")

    with open(output, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    print(f"✅ data.json generato: {output}")
    print(f"   Match oggi: {len(data['matches']['today'])}")
    print(f"   Match in arrivo: {len(data['matches']['upcoming'])}")
    print(f"   Value bets: {len(data['value_bets'])}")
    print(f"   Bankroll: €{data['bankroll']['current']:.2f}")
