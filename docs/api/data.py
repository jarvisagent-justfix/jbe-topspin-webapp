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


def load_matches_today(db, days_ahead=7):
    """Carica i match ATP in programma da oggi in avanti (solo non giocati)."""
    today = date.today()
    start_s = today.isoformat()
    end_s = (today + timedelta(days=days_ahead)).isoformat()

    rows = db.conn.execute("""
        SELECT m.id, m.match_date, m.tournament, m.surface, m.round,
               m.best_of, m.tour_level, m.score,
               m.winner_id, m.loser_id,
               w.name as winner_name, l.name as loser_name,
               w.country as winner_country, l.country as loser_country,
               w.hand as winner_hand, l.hand as loser_hand,
               m.winner_rank, m.loser_rank,
               m.w_sets, m.l_sets, m.w_games, m.l_games
        FROM tennis_matches m
        JOIN players w ON w.id = m.winner_id
        JOIN players l ON l.id = m.loser_id
        WHERE m.match_date >= ?
          AND m.score IS NULL
        ORDER BY m.match_date ASC, m.id ASC
    """, (start_s,)).fetchall()

    matches = []
    for r in rows:
        # Verifica se ci sono scommesse su questo match
        has_bet = False
        bet_count = db.conn.execute(
            "SELECT COUNT(*) as n FROM paper_portfolio WHERE match_id = ?",
            (r["id"],)
        ).fetchone()["n"]
        if bet_count > 0:
            has_bet = True
        if not has_bet:
            # Fallback: cerca per nome giocatori e data
            bc = db.conn.execute("""
                SELECT COUNT(*) as n FROM paper_portfolio
                WHERE match_date = ? AND status = 'pending'
                AND (player1 LIKE ? OR player2 LIKE ? OR player1 LIKE ? OR player2 LIKE ?)
            """, (
                r["match_date"],
                f'%{r["winner_name"].split()[-1]}%', f'%{r["winner_name"].split()[-1]}%',
                f'%{r["loser_name"].split()[-1]}%', f'%{r["loser_name"].split()[-1]}%'
            )).fetchone()["n"]
            has_bet = bc > 0

        analysis_time = None

        matches.append({
            "id": r["id"],
            "date": r["match_date"],
            "tournament": r["tournament"] or "",
            "surface": r["surface"] or "",
            "round": r["round"] or "",
            "best_of": r["best_of"] or 3,
            "tour_level": r["tour_level"] or "",
            "status": "upcoming",
            "players": {
                "p1": {"name": r["winner_name"], "country": r["winner_country"], "rank": r["winner_rank"], "hand": r["winner_hand"]},
                "p2": {"name": r["loser_name"], "country": r["loser_country"], "rank": r["loser_rank"], "hand": r["loser_hand"]}
            },
            "result": None,
            "has_bet": has_bet,
            "match_datetime": None,
            "analysis_time": analysis_time,
            "source": "db",
        })
    return matches


def load_portfolio_upcoming(db, days_ahead=7):
    """Carica match in arrivo dal paper_portfolio (Odds API, non ancora importati in tennis_matches)."""
    today = date.today()
    end = (today + timedelta(days=days_ahead)).isoformat()
    start = today.isoformat()

    rows = db.conn.execute("""
        SELECT DISTINCT p.match_date, p.player1, p.player2, p.tournament,
               p.market, p.odds, p.model_prob, p.edge, p.confidence,
               p.created_at, p.match_datetime, p.surface
        FROM paper_portfolio p
        WHERE p.match_id IS NULL
          AND p.match_date >= ? AND p.match_date <= ?
          AND p.status = 'pending'
        ORDER BY p.match_date ASC, p.match_datetime ASC
    """, (start, end)).fetchall()

    matches = []
    seen_keys = set()
    for r in rows:
        key = (r["match_date"], r["player1"], r["player2"])
        if key in seen_keys:
            continue
        seen_keys.add(key)

        # Se match_datetime è in formato italiano DD/MM/YYYY HH:MM, usalo per la data corretta
        match_date_str = r["match_date"]
        if r["match_datetime"]:
            try:
                parts = r["match_datetime"].split("/")
                if len(parts) >= 3:
                    day = parts[0].zfill(2)
                    month = parts[1].zfill(2)
                    year = parts[2].split()[0]  # "2026 11:00" -> "2026"
                    match_date_str = f"{year}-{month}-{day}"
            except:
                pass

        matches.append({
            "id": None,
            "date": match_date_str,
            "tournament": r["tournament"] or "ATP",
            "surface": r["surface"] or None,
            "round": None,
            "best_of": 3,
            "tour_level": None,
            "status": "upcoming",
            "players": {
                "p1": {"name": r["player1"], "country": None, "rank": None, "hand": None},
                "p2": {"name": r["player2"], "country": None, "rank": None, "hand": None},
            },
            "result": None,
            "has_bet": True,
            "match_datetime": r["match_datetime"] or None,
            "analysis_time": r["created_at"] or None,
            "source": "odds_api",
        })
    return matches


def load_value_bets(db):
    """Carica tutti i value bets dal paper portfolio."""
    rows = db.conn.execute("""
        SELECT * FROM paper_portfolio
        ORDER BY match_date ASC, edge DESC
    """).fetchall()

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


def load_value_candidates(db):
    """Carica le value candidate non giocate (Over, Handicap) da value_candidates."""
    rows = db.conn.execute("""
        SELECT * FROM value_candidates
        ORDER BY match_date ASC, edge DESC
    """).fetchall()

    candidates = []
    for r in rows:
        market_label = {
            "match_winner": "Vincitore",
            "game_handicap": "Handicap Game",
            "over_under": "Totale Game",
        }.get(r["market"], r["market"] or "")

        selection = r["selection"] or ""
        if r["market"] == "over_under" and selection:
            selection = selection.replace("Over", "Oltre").replace("Under", "Sotto")

        candidates.append({
            "id": r["id"],
            "match_date": r["match_date"],
            "player1": r["player1"],
            "player2": r["player2"],
            "selection": selection,
            "odds": r["odds"],
            "model_prob": r["model_prob"],
            "edge": r["edge"],
            "edge_pct": round(r["edge"] * 100, 1),
            "stake": r["stake"],
            "market": r["market"],
            "market_label": market_label,
            "tournament": r["tournament"] or "",
            "surface": r["surface"] or "",
            "bookmaker": r["bookmaker"] or "",
            "reason": r["reason"] or "",
        })
    return candidates


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
               COALESCE(SUM(result), 0) as pnl,
               COALESCE(SUM(stake), 0) as total_staked
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


def load_log_entries():
    """Costruisce log strutturati dal pipeline log e dal DB."""
    import calendar as cal
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    entries = []

    def parse_pipeline_ts(header):
        import re
        m = re.search(r'(\w{3})\s+(\w{3})\s+(\d+)\s+(\d{2}):(\d{2}):(\d{2})\s+\w{3}\s+(\d{4})', header)
        if not m: return None, None
        try:
            month_num = list(cal.month_abbr).index(m.group(2).capitalize())
            ts = datetime(int(m.group(7)), month_num, int(m.group(3)),
                          int(m.group(4)), int(m.group(5)), int(m.group(6)))
            return ts.strftime("%Y-%m-%d"), ts.strftime("%H:%M")
        except: return None, None

    # 1. Leggi pipeline log
    pipeline_log = "/tmp/jbe-pipeline.log"
    if os.path.exists(pipeline_log):
        with open(pipeline_log) as f:
            content = f.read()
        runs = content.split("=" * 40)
        for run in runs:
            if not run.strip():
                continue
            lines = run.strip().split("\n")
            header = lines[0] if lines else ""
            date_key, time_key = parse_pipeline_ts(header)
            if not date_key:
                date_key = date.today().isoformat()
                time_key = datetime.now().strftime("%H:%M")

            value_count = len([l for l in lines if "VALUE BET" in l])
            err_count = len([l for l in lines if "ERRORE" in l or "ERROR" in l])
            match_count = None
            for l in lines:
                if "Match trovati:" in l:
                    try: match_count = int(l.split("Match trovati:")[1].split()[0])
                    except: pass
            
            match_analyzed = None
            for l in lines:
                if "Match analizzati:" in l:
                    try: match_analyzed = int(l.split("Match analizzati:")[1].split()[0])
                    except: pass

            # Info multi-linea
            info_lines = []

            # Self-Improvement
            for l in lines:
                ls = l.strip()
                if "errori totali" in ls and "accuracy" in ls:
                    info_lines.append(f"🎯 Self-Improvement: {ls}")
                if "Errori da ultimo retrain" in ls:
                    info_lines.append(f"📊 {ls}")
                if "Retrain non necessario" in ls or "Retrain necessario" in ls:
                    info_lines.append(f"🔄 {ls}")

            # Coverage
            for l in lines:
                ls = l.strip()
                if "Copertura" in ls and "2026" in ls:
                    info_lines.append(f"📈 {ls}")
                if "Odds totali nel DB" in ls:
                    info_lines.append(f"🗄️ {ls}")

            # Webapp + Bankroll
            for l in lines:
                ls = l.strip()
                if "Webapp data generato" in ls:
                    info_lines.append(f"🌐 {ls.replace('✅ ','')}")
                if "Bankroll" in l and "EUR" in l:
                    info_lines.append(f"💰 {ls}")
                if "Match oggi:" in ls and "Match in arrivo:" in ls:
                    info_lines.append(f"📅 {ls}")
                if "Value bets:" in ls and "Bankroll:" in ls:
                    info_lines.append(f"💎 {ls}")

            # Odds API info
            for l in lines:
                ls = l.strip()
                if ls.startswith("[INFO] Recupero"):
                    info_lines.append(f"📡 {ls}")
                if "[CACHE] Salvati" in ls:
                    info_lines.append(f"💾 {ls}")
                if "Odds API report integrato" in ls:
                    info_lines.append(f"✅ {ls}")

            icon = "🔄"
            title = "Pipeline eseguita"
            desc_parts = []
            if match_analyzed is not None:
                desc_parts.append(f"📡 {match_analyzed} match analizzati")
            elif match_count is not None:
                desc_parts.append(f"📡 {match_count} match trovati")
            if value_count > 0:
                desc_parts.append(f"💎 {value_count} value bet")
            if err_count > 0:
                desc_parts.append(f"⚠️ {err_count} errori")

            entries.append({
                "date": date_key,
                "time": time_key,
                "icon": icon,
                "type": "pipeline",
                "title": title,
                "desc": " · ".join(desc_parts) if desc_parts else "Nessuna bet trovata",
                "info": "\n".join(info_lines) if info_lines else None,
            })

    # 2. Value bet events dal DB
    try:
        db_local = TennisDatabase()
        bet_events = db_local.conn.execute("""
            SELECT created_at, player1, player2, selection, market, odds, edge, model_prob, confidence
            FROM paper_portfolio
            WHERE created_at IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 30
        """).fetchall()
        
        market_emoji = {"match_winner": "🏆", "game_handicap": "⚖️", "over_under": "📊"}
        market_labels = {"match_winner": "Vincitore", "game_handicap": "Handicap Game", "over_under": "Totale Game"}
        
        for r in bet_events:
            try:
                ts = datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S")
                date_key = ts.strftime("%Y-%m-%d")
                time_key = ts.strftime("%H:%M")
            except:
                date_key = date.today().isoformat()
                time_key = "??:??"

            me = market_emoji.get(r["market"], "🎯")
            ml = market_labels.get(r["market"], r["market"] or "")
            edge_pct = round((r["edge"] or 0) * 100, 1)
            model_pct = round((r["model_prob"] or 0) * 100)
            
            # Info dettagliata
            info = []
            info.append(f"📊 Mercato: {me} {ml}")
            if model_pct:
                info.append(f"🎯 Modello: {model_pct}%")
            info.append(f"📈 Edge: +{edge_pct}%")
            info.append(f"🎲 Quota: {r['odds']:.2f}")
            info.append(f"🎯 Confidenza: {r['confidence'] or 'N/A'}")

            entries.append({
                "date": date_key,
                "time": time_key,
                "icon": "💎",
                "type": "value_bet",
                "title": f"{r['player1']} vs {r['player2']}",
                "desc": f"{me} {r['selection']} · Edge +{edge_pct}% @{r['odds']:.2f} · {r['confidence'] or '-'}",
                "info": "\n".join(info),
            })
    except Exception as e:
        entries.append({
            "date": date.today().isoformat(),
            "time": datetime.now().strftime("%H:%M"),
            "icon": "⚠️",
            "type": "system",
            "title": "Errore DB",
            "desc": str(e),
            "info": None,
        })

    entries.sort(key=lambda e: (e["date"], e["time"]), reverse=True)
    return entries


def build_data():
    db = TennisDatabase()

    today = date.today().isoformat()

    BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Carica match dal DB storico (solo da oggi in poi)
    matches_db_today = load_matches_today(db, days_ahead=7)
    matches_db_upcoming = load_matches_today(db, days_ahead=7)

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
        },
        "value_bets": load_value_bets(db),
        "value_candidates": load_value_candidates(db),
        "bankroll": load_bankroll_stats(db),
        "bet_history": load_bet_history(db, limit=50),
        "bankroll_history": load_bankroll_history(db),
        "last_report": load_last_report(db),
        "log_entries": load_log_entries(),
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
