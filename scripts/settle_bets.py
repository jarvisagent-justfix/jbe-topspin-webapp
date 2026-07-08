#!/usr/bin/env python3
"""
JBE TopSpin — Settlement delle bet pending
===========================================
Unico punto di ingresso per risolvere le scommesse in sospeso.
Supporta due fonti di risultati:

  --source db        : Cerca i risultati in tennis_matches (DB locale)
  --source wimbledon : Usa i risultati hardcoded di Wimbledon 2026
                       (per match non ancora nel DB storico)

Perché due fonti:
  - resolve_pending.py e resolve_wimbledon.py facevano la stessa cosa
    (settlement) con logiche duplicate al 70%.
  - Unificarli evita la duplicazione e permette di aggiungere nuove fonti
    (es. US Open 2026) senza creare un nuovo script.

Uso:
  PYTHONPATH=src python3 scripts/settle_bets.py --source db
  PYTHONPATH=src python3 scripts/settle_bets.py --source wimbledon
"""
import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from database import TennisDatabase
from config import DB_PATH

db = TennisDatabase(DB_PATH)
log = print


# ============================================================
# UTILITY: nome matching
# ============================================================
def normalize_name(name):
    """Normalizza nome giocatore per matching fuzzy."""
    if not name:
        return ""
    name = name.strip().lower()
    name = name.replace("\u00e9", "e").replace("\u00e8", "e").replace("\u00ea", "e")
    name = name.replace("\u00e1", "a").replace("\u00e0", "a").replace("\u00e2", "a")
    name = name.replace("\u00ed", "i").replace("\u00ec", "i").replace("\u00ee", "i")
    name = name.replace("\u00f3", "o").replace("\u00f2", "o").replace("\u00f4", "o")
    name = name.replace("\u00fa", "u").replace("\u00f9", "u").replace("\u00fb", "u")
    return name


def get_surname(name):
    """Estrae il cognome (ultima parola del nome)."""
    parts = normalize_name(name).split()
    return parts[-1] if parts else ""


def names_match(name1, name2):
    """
    Matching fuzzy tra due nomi.
    Usa cognome + prima lettera del nome per evitare falsi positivi.
    Esempio: "Jannik Sinner" matcha con "J. Sinner" e "Sinner J."
    """
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)
    if not n1 or not n2:
        return False
    p1 = n1.split()
    p2 = n2.split()
    # Se il cognome matcha, probabilmente è lo stesso giocatore
    if p1[-1] == p2[-1]:
        return True
    # Controllo parziale (es. "de Minaur" vs "Minaur")
    if len(p1[-1]) > 3 and (p1[-1] in p2[-1] or p2[-1] in p1[-1]):
        return True
    return False


def players_match(p1_bet, p2_bet, p1_res, p2_res):
    """
    Verifica che due coppie di nomi corrispondano (indipendentemente dall'ordine).
    """
    return (names_match(p1_bet, p1_res) and names_match(p2_bet, p2_res)) or \
           (names_match(p1_bet, p2_res) and names_match(p2_bet, p1_res))


def parse_selection(selection, market):
    """
    Parsing della selezione di una bet.
    Returns: (tipo, [parametri])
      match_winner: ("player", nome_giocatore)
      over_under: ("over"/"under", linea)
      game_handicap: ("favored"/"underdog"/"player", nome, handicap)
    """
    sel = selection.lower()
    if market == "match_winner":
        return ("player", selection)

    elif market == "over_under":
        m = re.search(r'([\d.]+)', sel)
        line = float(m.group(1)) if m else 0
        if "over" in sel or sel.startswith("o"):
            return ("over", line)
        else:
            return ("under", line)

    elif market == "game_handicap":
        m = re.search(r'([+-]?[\d.]+)', sel)
        point = float(m.group(1)) if m else 0
        if "favorito" in sel:
            return ("favored", point)
        elif "sfavorito" in sel:
            return ("underdog", point)
        else:
            # Cerca nome con handicap
            parts = selection.rsplit(" ", 1)
            if len(parts) == 2:
                return ("player", parts[0], point)
            return ("player", selection, point)

    return ("unknown",)


# ============================================================
# SETTLEMENT VIA DB LOCALE (tennis_matches)
# ============================================================
def settle_from_db():
    """
    Risolve bet pending usando i match completati in tennis_matches.
    Per ogni bet, cerca un match con gli stessi giocatori e data,
    poi determina se la selezione è vincente.
    """
    log("=" * 60)
    log("Settlement via DB locale (tennis_matches)")
    log("=" * 60)

    pending = db.conn.execute("""
        SELECT pp.id, pp.player1, pp.player2, pp.selection, pp.market,
               pp.stake, pp.odds, pp.match_date, pp.bankroll_before, pp.match_id,
               m.id as m_id, m.winner_id, m.loser_id, m.w_games, m.l_games,
               m.score, w.name as wname, l.name as lname
        FROM paper_portfolio pp
        LEFT JOIN tennis_matches m ON (
            strftime('%Y-%m-%d', m.match_date) = pp.match_date
        )
        LEFT JOIN players w ON w.id = m.winner_id
        LEFT JOIN players l ON l.id = m.loser_id
        WHERE pp.status = 'pending'
        ORDER BY pp.match_date DESC
    """).fetchall()

    log(f"Bet pending trovate: {len(pending)}")
    if not pending:
        return 0

    resolved = 0
    skipped = 0
    errors = 0

    for p in pending:
        bid = p["id"]
        p1, p2 = p["player1"], p["player2"]
        selection = p["selection"]
        market = p["market"]

        if not p["m_id"]:
            skipped += 1
            continue

        winner_name = p["wname"]
        loser_name = p["lname"]
        w_games = p["w_games"] or 0
        l_games = p["l_games"] or 0
        score_str = p["score"] or f"{w_games}-{l_games}"

        # Verifica che i giocatori corrispondano
        try:
            if not players_match(p1, p2, winner_name, loser_name):
                skipped += 1
                continue

            # Determina chi è il vincitore
            if names_match(p1, winner_name):
                winner_in_bet = p1
                loser_in_bet = p2
            elif names_match(p2, winner_name):
                winner_in_bet = p2
                loser_in_bet = p1
            else:
                skipped += 1
                continue

            is_won = False
            is_push = False

            if market == "match_winner":
                is_won = names_match(selection, winner_in_bet)

            elif market == "over_under":
                parsed = parse_selection(selection, market)
                total = w_games + l_games
                if parsed[0] == "over":
                    is_won = total > parsed[1]
                else:
                    is_won = total <= parsed[1]
                is_push = total == parsed[1]

            elif market == "game_handicap":
                parsed = parse_selection(selection, market)
                if parsed[0] == "favored":
                    fave_games = w_games if names_match(winner_in_bet, p1) else l_games
                    opp_games = l_games if fave_games == w_games else w_games
                    is_won = (fave_games + parsed[1]) > opp_games
                elif parsed[0] == "underdog":
                    under_games = l_games if names_match(winner_in_bet, p1) else w_games
                    opp_games = w_games if under_games == l_games else l_games
                    is_won = (under_games + abs(parsed[1])) > opp_games
                else:
                    player_name = parsed[1]
                    hc = parsed[2]
                    sel_games = w_games if names_match(player_name, winner_name) else l_games
                    opp_games = l_games if sel_games == w_games else w_games
                    is_won = (sel_games + hc) > opp_games

            # Aggiorna stato
            if is_push:
                profit = 0
                new_status = "push"
            elif is_won:
                profit = round(p["stake"] * (p["odds"] - 1), 2)
                new_status = "won"
            else:
                profit = -round(p["stake"], 2)
                new_status = "lost"

            db.conn.execute("""
                UPDATE paper_portfolio
                SET status = ?, result = ?,
                    settled_at = datetime('now'),
                    notes = ?
                WHERE id = ?
            """, (new_status, profit,
                  f"Auto-risolta via tennis_matches: {winner_name} batte {loser_name} {score_str}",
                  bid))

            icon = "\u2705" if is_won else ("\U0001f504" if is_push else "\u274c")
            log(f"  {icon} #{bid:3d} | {p1:25s} vs {p2:25s} | {selection:30s} | {new_status:>5s} ({profit:+.2f}\u20ac) | {score_str}")
            resolved += 1

        except Exception as e:
            log(f"  \u26a0\ufe0f  #{bid}: {e}")
            errors += 1

    db.conn.commit()
    _print_report(resolved, skipped, errors)
    return resolved


# ============================================================
# SETTLEMENT VIA RISULTATI NOTI (Wimbledon 2026)
# ============================================================
def _load_wimbledon_results():
    """Carica i risultati Wimbledon 2026 dal file JSON."""
    json_path = os.path.join(os.path.dirname(__file__), "..", "data", "wimbledon_2026_results.json")
    try:
        with open(json_path) as f:
            data = json.load(f)
        return data["results"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        log(f"[ERRORE] Caricamento risultati Wimbledon: {e}")
        return []


def _find_winner_in_results(p1, p2, results):
    """
    Cerca un match nella lista risultati.
    Returns: (vincitore, perdente, punteggio, data) o None
    """
    for date_str, winner, loser, score in results:
        if names_match(p1, winner) and names_match(p2, loser):
            return (winner, loser, score, date_str)
        if names_match(p1, loser) and names_match(p2, winner):
            return (winner, loser, score, date_str)
    return None


def settle_from_wimbledon():
    """
    Risolve bet pending usando i risultati noti di Wimbledon 2026.
    Utile per match che non sono ancora nel DB storico.
    I risultati sono in data/wimbledon_2026_results.json.
    """
    log("=" * 60)
    log("Settlement via risultati Wimbledon 2026 (JSON)")
    log("=" * 60)

    results = _load_wimbledon_results()
    if not results:
        log("[ERRORE] Nessun risultato Wimbledon caricato.")
        return 0

    pending = db.conn.execute("""
        SELECT id, player1, player2, selection, market, odds, stake, match_date
        FROM paper_portfolio
        WHERE status = 'pending'
        ORDER BY match_date DESC
    """).fetchall()

    log(f"Bet pending trovate: {len(pending)}")
    if not pending:
        return 0

    resolved = 0
    skipped = 0
    errors = 0

    for p in pending:
        bid = p["id"]
        p1, p2 = p["player1"], p["player2"]
        selection = p["selection"]
        market = p["market"]
        odds = p["odds"]
        stake = p["stake"]

        try:
            match_result = _find_winner_in_results(p1, p2, results)
            if not match_result:
                skipped += 1
                continue

            winner, loser, score_str, result_date = match_result

            is_won = False
            is_push = False
            sel_lower = selection.lower()
            winner_surname = get_surname(winner)
            loser_surname = get_surname(loser)

            if market == "match_winner":
                is_won = winner_surname in sel_lower or names_match(selection, winner)

            elif market == "over_under":
                games = re.findall(r'(\d+)-(\d+)', score_str)
                if games:
                    total_games = sum(int(a) + int(b) for a, b in games)
                    line_match = re.search(r'([\d.]+)', selection)
                    if line_match:
                        line = float(line_match.group(1))
                        if "OVER" in sel_lower or "O/" in sel_lower:
                            is_won = total_games > line
                            is_push = total_games == line
                        else:
                            is_won = total_games <= line
                            is_push = total_games == line

            elif market == "game_handicap":
                parsed = parse_selection(selection, market)
                hc_match = re.search(r'([+-]?[\d.]+)', selection)
                if hc_match:
                    hc = float(hc_match.group(1))
                    games_inside = re.findall(r'(\d+)-(\d+)', score_str)
                    if games_inside:
                        g1 = sum(int(a) for a, _ in games_inside)
                        g2 = sum(int(b) for _, b in games_inside)
                        if "FAVORITO" in selection.upper():
                            is_won = (g1 + hc) > g2
                        elif "SFAVORITO" in selection.upper():
                            is_won = (g2 + abs(hc)) > g1

            if is_push:
                profit = 0
                new_status = "push"
            elif is_won:
                profit = round(stake * (odds - 1), 2)
                new_status = "won"
            else:
                profit = -round(stake, 2)
                new_status = "lost"

            db.conn.execute("""
                UPDATE paper_portfolio
                SET status = ?, result = ?, settled_at = datetime('now'),
                    notes = ?
                WHERE id = ?
            """, (new_status, profit,
                  f"Wimbledon 2026: {winner} batte {loser} {score_str}",
                  bid))

            icon = "\u2705" if is_won else ("\U0001f504" if is_push else "\u274c")
            log(f"  {icon} #{bid:3d} | {p1:25s} vs {p2:25s} | {selection:30s} | {new_status:>5s} ({profit:+.2f}\u20ac) | {score_str}")
            resolved += 1

        except Exception as e:
            log(f"  \u26a0\ufe0f  #{bid}: {e}")
            errors += 1

    db.conn.commit()
    _print_report(resolved, skipped, errors)
    return resolved


# ============================================================
# REPORT
# ============================================================
def _print_report(resolved, skipped, errors):
    log(f"\n{'=' * 60}")
    log(f"Risolte: {resolved} | Saltate: {skipped} | Errori: {errors}")
    log(f"{'=' * 60}")
    cur = db.conn.execute("SELECT status, COUNT(*), ROUND(SUM(COALESCE(result,0)),2) FROM paper_portfolio GROUP BY status")
    log("\nPortfolio aggiornato:")
    total_pnl = 0
    for r in cur.fetchall():
        log(f"  {r[0]:>10}: {r[1]:3d} | P&L: {r[2]:+.2f}\u20ac")
        if r[0] in ("won", "lost"):
            total_pnl += r[2]
    log(f"\n  P&L Totale: {total_pnl:+.2f}\u20ac")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    source = "db"
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--source" and i + 1 < len(sys.argv[1:]):
            source = sys.argv[i + 2]
        elif arg.startswith("--source="):
            source = arg.split("=", 1)[1]

    if source == "wimbledon":
        settle_from_wimbledon()
    else:
        settle_from_db()
