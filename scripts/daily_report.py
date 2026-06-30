#!/usr/bin/env python3
"""
JBE TopSpin — Daily Report & Value Bet Delivery
================================================
1. Scarica l'ultimo XLSX da tennis-data.co.uk
2. Importa nuovi match + odds nel DB
3. Carica ELO + modelli
4. Per ogni match recente con odds: predici e trova value bet
5. Logga prediction_errors per self-improvement
6. Genera report per Discord delivery

Uso: PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/daily_report.py
"""

import sys, os, json, math, urllib.request, zipfile, io, re
from datetime import date, timedelta, datetime
from xml.etree import ElementTree as ET
from collections import defaultdict

# Context manager: route internal prints to stderr during generation
# Only the final print(report) goes to real stdout (for cron delivery)
class _StdoutToStderr:
    _real_stdout = None
    def __enter__(self):
        self._real_stdout = sys.stdout
        sys.stdout = sys.stderr
    def __exit__(self, *args):
        sys.stdout = self._real_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from engine.xgboost_tennis import TopSpinEngine
from engine.value_detector import ValueDetector
from config import DB_PATH, MODEL_DIR, MIN_EDGE, MIN_CONFIDENCE

BASE_URL = "http://www.tennis-data.co.uk"


# ================================================================
# HELPER: XLSX parsing (same logic as import_odds_xlsx.py)
# ================================================================
def load_xlsx(url: str) -> list:
    """Scarica XLSX, ritorna lista di dict."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"[ERROR] Download {url}: {e}")
        return []

    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        ss_xml = z.read('xl/sharedStrings.xml')
        ss_tree = ET.fromstring(ss_xml)
        shared_strings = [si.find('s:t', ns).text or '' for si in ss_tree.findall('.//s:si', ns)]

        sheet_xml = z.read('xl/worksheets/sheet1.xml')
        sheet_tree = ET.fromstring(sheet_xml)

        all_row_cells = []
        for row_elem in sheet_tree.findall('.//s:row', ns):
            cells = {}
            for cell in row_elem.findall('s:c', ns):
                cell_ref = cell.get('r', '')
                col_letter = ''.join(c for c in cell_ref if c.isalpha())
                v = cell.find('s:v', ns)
                val = v.text if v is not None else ''
                if cell.get('t') == 's' and val:
                    idx = int(float(val))
                    val = shared_strings[idx] if idx < len(shared_strings) else val
                cells[col_letter] = val
            all_row_cells.append(cells)

        if not all_row_cells:
            return []

        header_row = all_row_cells[0]
        header_map = {}
        for col_letter in sorted(header_row.keys(), key=lambda c: (len(c), c)):
            header_map[col_letter] = header_row[col_letter]

        return [header_map] + all_row_cells[1:]


def parse_date(val):
    """Converte data Excel serial o stringa in stringa ISO."""
    try:
        days = int(float(val))
        return (date(1899, 12, 30) + timedelta(days=days)).isoformat()
    except:
        pass
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]:
        try:
            return datetime.strptime(val.strip(), fmt).date().isoformat()
        except:
            pass
    return None


def guess_surname_initial(xlsx_name):
    """Parse 'Surname I.' -> (surname_parts, initial)"""
    name = xlsx_name.strip()
    if not name or '.' not in name:
        return [name.lower()], ''
    tokens = name.split()
    last = tokens[-1]
    initial = last.replace('.', '').strip()
    surname = ' '.join(tokens[:-1])
    surname_parts = surname.lower().split()
    return surname_parts, initial


def import_latest_xlsx(year, db):
    """Scarica XLSX per un anno, importa nuovi match e odds."""
    url = f"{BASE_URL}/{year}/{year}.xlsx"
    xlsx_data = load_xlsx(url)
    if not xlsx_data or len(xlsx_data) < 2:
        return 0, 0, 0

    header_map = xlsx_data[0]
    raw_rows = xlsx_data[1:]

    imported = 0
    odds_added = 0
    skipped = 0

    for cells in raw_rows:
        xm = {}
        for col_letter, header_name in header_map.items():
            xm[header_name] = cells.get(col_letter, "")

        try:
            match_date = parse_date(xm.get("Date", ""))
            if not match_date:
                continue

            winner_xlsx = xm.get("Winner", "").strip()
            loser_xlsx = xm.get("Loser", "").strip()
            surface = xm.get("Surface", "").strip()
            comment = xm.get("Comment", "").strip()

            if not winner_xlsx or not loser_xlsx or not surface:
                continue

            # Già importato? Controlla se match esiste nel DB
            w_parts, w_init = guess_surname_initial(winner_xlsx)
            l_parts, l_init = guess_surname_initial(loser_xlsx)
            if not w_parts or not l_parts:
                skipped += 1
                continue

            w_surname_like = '%' + w_parts[-1] + '%'
            l_surname_like = '%' + l_parts[-1] + '%'

            # Cerca match esistente
            match = db.conn.execute("""
                SELECT m.id, w.name as wn, l.name as ln
                FROM tennis_matches m
                JOIN players w ON w.id=m.winner_id
                JOIN players l ON l.id=m.loser_id
                WHERE m.match_date = ? AND m.surface = ?
                  AND LOWER(w.name) LIKE ? AND w.name LIKE ?
                  AND LOWER(l.name) LIKE ? AND l.name LIKE ?
                LIMIT 1
            """, (match_date, surface,
                  w_surname_like, f'{w_init}%',
                  l_surname_like, f'{l_init}%')).fetchone()

            is_swapped = False
            if not match:
                match = db.conn.execute("""
                    SELECT m.id, w.name as wn, l.name as ln
                    FROM tennis_matches m
                    JOIN players w ON w.id=m.winner_id
                    JOIN players l ON l.id=m.loser_id
                    WHERE m.match_date = ? AND m.surface = ?
                      AND LOWER(w.name) LIKE ? AND w.name LIKE ?
                      AND LOWER(l.name) LIKE ? AND l.name LIKE ?
                    LIMIT 1
                """, (match_date, surface,
                      l_surname_like, f'{l_init}%',
                      w_surname_like, f'{w_init}%')).fetchone()
                is_swapped = True

            if not match:
                skipped += 1
                continue

            # Show matching info
            match_id = match["id"]

            # Check if we already have odds for this match/bookmaker
            existing_b365 = db.conn.execute(
                "SELECT id FROM tennis_odds WHERE match_id=? AND bookmaker='Bet365'",
                (match_id,)
            ).fetchone()
            existing_ps = db.conn.execute(
                "SELECT id FROM tennis_odds WHERE match_id=? AND bookmaker='Pinnacle'",
                (match_id,)
            ).fetchone()

            if is_swapped:
                b365w = xm.get("B365L", "")
                b365l = xm.get("B365W", "")
                psw = xm.get("PSL", "")
                psl = xm.get("PSW", "")
            else:
                b365w = xm.get("B365W", "")
                b365l = xm.get("B365L", "")
                psw = xm.get("PSW", "")
                psl = xm.get("PSL", "")

            if b365w and float(b365w) > 0 and not existing_b365:
                db.conn.execute("""
                    INSERT INTO tennis_odds
                    (match_id, bookmaker, odds_winner, odds_loser)
                    VALUES (?, 'Bet365', ?, ?)
                """, (match_id, float(b365w),
                      float(b365l) if b365l and float(b365l) > 0 else None))
                odds_added += 1

            if psw and float(psw) > 0 and not existing_ps:
                db.conn.execute("""
                    INSERT INTO tennis_odds
                    (match_id, bookmaker, odds_winner, odds_loser)
                    VALUES (?, 'Pinnacle', ?, ?)
                """, (match_id, float(psw),
                      float(psl) if psl and float(psl) > 0 else None))
                odds_added += 1

            imported += 1

        except Exception as e:
            pass

    db.conn.commit()
    return imported, odds_added, skipped


# ================================================================
# CORE: Daily report generation
# ================================================================

def log_prediction_error(db, match_id, pred_winner_id, pred_prob, best_odds_winner,
                          best_odds_loser, edge, surface, tour_level, round_val,
                          best_of, player1_id, player2_id):
    """Inserisce una prediction error record (senza risultato ancora)."""
    db.conn.execute("""
        INSERT INTO prediction_errors
        (match_id, pred_winner_id, pred_prob, best_odds_winner, best_odds_loser,
         edge_winner, surface, tour_level, round, best_of, player1_id, player2_id,
         winner_correct, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)
    """, (match_id, pred_winner_id, pred_prob,
          best_odds_winner, best_odds_loser, edge,
          surface, tour_level, round_val, best_of,
          player1_id, player2_id))
    db.conn.commit()


def fill_prediction_results(db):
    """
    Per ogni prediction_errors senza risultato (winner_correct IS NULL),
    cerca il match completato nel DB e aggiorna il risultato.
    """
    errors = db.conn.execute("""
        SELECT pe.id, pe.match_id, pe.pred_winner_id, pe.player1_id, pe.player2_id
        FROM prediction_errors pe
        WHERE pe.winner_correct IS NULL
    """).fetchall()

    filled = 0
    for e in errors:
        match = db.conn.execute(
            "SELECT winner_id, w_sets, l_sets FROM tennis_matches WHERE id=?",
            (e["match_id"],)
        ).fetchone()
        if match and match["winner_id"]:
            correct = 1 if match["winner_id"] == e["pred_winner_id"] else 0
            db.conn.execute("""
                UPDATE prediction_errors
                SET winner_correct=?, actual_winner_id=?
                WHERE id=?
            """, (correct, match["winner_id"], e["id"]))
            filled += 1

    if filled:
        db.conn.commit()
    return filled


def generate_report(target_date: date = None) -> str:
    """Genera il report giornaliero JBE TopSpin."""
    if target_date is None:
        target_date = date.today()

    db = TennisDatabase(DB_PATH)

    # === FASE 0: Fill prediction results from completed matches ===
    filled = fill_prediction_results(db)
    if filled:
        print(f"[INFO] Prediction errors aggiornati con risultati: {filled}")

    # === FASE 1: Import latest XLSX ===
    print(f"\n[INFO] Import XLSX {target_date.year}...")
    imp, odds_new, skipped = import_latest_xlsx(target_date.year, db)
    print(f"  Importati: {imp} match, {odds_new} odds, {skipped} skipped")

    # === FASE 2: Load engine ===
    print("[INFO] Loading TopSpin engine...")
    engine = TopSpinEngine(db, load_models=True)
    # Carica TUTTI i rating ELO dal DB
    engine.elo_engine.load_all_ratings()
    print(f"  {len(engine.elo_engine.ratings)} player ratings loaded")

    detector = ValueDetector(db)

    # === FASE 3: Find matches with odds for prediction ===
    # Cerca match recenti (ultimi 7 giorni) con odds Bet365 o Pinnacle
    lookback = target_date - timedelta(days=7)
    candidate_matches = db.conn.execute("""
        SELECT DISTINCT m.id, m.match_date, m.winner_id, m.loser_id,
               m.surface, m.best_of, m.round, m.tour_level,
               m.w_sets, m.l_sets, m.w_games, m.l_games,
               w.name as wname, l.name as lname,
               m.winner_rank, m.loser_rank,
               m.tournament
        FROM tennis_matches m
        JOIN tennis_odds o ON o.match_id = m.id
        JOIN players w ON w.id=m.winner_id
        JOIN players l ON l.id=m.loser_id
        WHERE m.match_date >= ? AND m.match_date <= ?
          AND m.surface IS NOT NULL
          AND m.winner_id IS NOT NULL AND m.loser_id IS NOT NULL
          AND (o.bookmaker = 'Bet365' OR o.bookmaker = 'Pinnacle')
        ORDER BY m.match_date DESC
    """, (lookback.isoformat(), target_date.isoformat())).fetchall()

    print(f"\n[INFO] {len(candidate_matches)} match con odds negli ultimi 3 giorni")

    # === FASE 4: Predict + Value Detection ===
    report_lines = []
    report_lines.append(f"🎾 JBE TopSpin Report — {target_date.strftime('%d/%m/%Y')}")
    report_lines.append(f"📊 Match analizzati: {len(candidate_matches)}")
    report_lines.append("")

    value_bets_found = 0
    correct_predictions = 0
    total_predicted = 0
    pnl_flat = 0.0  # Flat betting P&L (1 EUR per value bet)
    pnl_kelly = 0.0  # Kelly P&L
    n_value_bets = 0

    for m in candidate_matches:
        try:
            match_date_obj = date.fromisoformat(m["match_date"]) if isinstance(m["match_date"], str) else m["match_date"]

            # Decay per entrambi i giocatori
            for pid in (m["winner_id"], m["loser_id"]):
                if pid in engine.elo_engine.ratings:
                    engine.elo_engine.ratings[pid].apply_decay(match_date_obj)

            # Prediction con il modello calibrato
            pred = engine.predict(
                m["id"], m["winner_id"], m["loser_id"],
                m["surface"], match_date_obj, m["best_of"],
                m["round"], m["tour_level"],
                m["winner_rank"], m["loser_rank"]
            )

            prob_p1 = pred["prob_player1"]
            prob_p2 = pred["prob_player2"]

            # Value detection
            bets = detector.find_value_bets(
                m["id"], m["wname"], m["lname"],
                prob_p1, prob_p2,
                match_date_obj, m["tournament"], m["surface"]
            )

            # Determine which player the model favors
            fav_id = m["winner_id"] if prob_p1 >= 0.5 else m["loser_id"]
            fav_prob = max(prob_p1, prob_p2)
            fav_name = m["wname"] if prob_p1 >= 0.5 else m["lname"]

            # Check if match is completed (has result)
            is_completed = m["w_sets"] is not None and m["w_sets"] > 0
            was_correct = None
            if is_completed and fav_id == m["winner_id"]:
                was_correct = True
                correct_predictions += 1
                total_predicted += 1
            elif is_completed:
                was_correct = False
                total_predicted += 1

            # Log prediction error for self-improvement
            bookmaker_logged = "N/A"
            odds_w = None
            if is_completed:
                # Get best odds
                odds_w = db.conn.execute(
                    "SELECT odds_winner, odds_loser FROM tennis_odds WHERE match_id=? AND bookmaker='Pinnacle' LIMIT 1",
                    (m["id"],)
                ).fetchone()
                bookmaker_logged = "Pinnacle"
                if not odds_w:
                    odds_w = db.conn.execute(
                        "SELECT odds_winner, odds_loser FROM tennis_odds WHERE match_id=? AND bookmaker='Bet365' LIMIT 1",
                        (m["id"],)
                    ).fetchone()
                    bookmaker_logged = "Bet365"

                if odds_w:
                    log_prediction_error(
                        db, m["id"], fav_id, fav_prob,
                        float(odds_w["odds_winner"] or 0), float(odds_w["odds_loser"] or 0),
                        abs(prob_p1 - 1/odds_w["odds_winner"]) if odds_w["odds_winner"] else 0,
                        m["surface"], m["tour_level"], m["round"],
                        m["best_of"], m["winner_id"], m["loser_id"]
                    )

            # Format report line
            match_str = f"{m['match_date']} | {m['tournament']} ({m['surface']})"
            pred_str = f"{fav_name} {fav_prob:.1%}"

            if bets:
                value_bets_found += len(bets)
                for bet in bets:
                    stake = detector.kelly.calculate_stake(bet.edge, bet.odds)
                    report_lines.append(f"🟢 VALUE BET")
                    report_lines.append(f"   {match_str}")
                    report_lines.append(f"   {m['wname']} vs {m['lname']}")
                    report_lines.append(f"   {bet.to_discord_message(detector.kelly.bankroll)}")

                    # Paper portfolio logging NON eseguito qui — solo odds_api.py per match futuri
                    # (daily_report analizza match completati, non forward-testing)
                    # Track P&L
                    if is_completed:
                        n_value_bets += 1
                        if was_correct:
                            pnl_flat += bet.odds - 1  # Profit of 1 EUR
                            pnl_kelly += stake * (bet.odds - 1)
                        else:
                            pnl_flat -= 1.0
                            pnl_kelly -= stake
            else:
                # Show strong predictions even without value
                if fav_prob >= 0.70 and is_completed:
                    mark = "✅" if was_correct else "❌"
                    report_lines.append(f"{mark} {match_str}")
                    report_lines.append(f"   {m['wname']} vs {m['lname']} → Modello: {pred_str}")
                    report_lines.append(f"   (Nessuna value bet)")

        except Exception as e:
            print(f"  [ERRORE] match {m['id'] if 'id' in m else '?'}: {e}")
            continue

    # === FASE 5: Summary Statistics ===
    report_lines.append(f"\n{'='*50}")
    report_lines.append("📈 RIEPILOGO")
    report_lines.append(f"{'='*50}")
    report_lines.append(f"Match analizzati: {len(candidate_matches)}")
    report_lines.append(f"Value Bets trovate: {value_bets_found}")

    if total_predicted > 0:
        accuracy = correct_predictions / total_predicted * 100
        report_lines.append(f"")
        report_lines.append(f"📊 Accuratezza modello (match completati):")
        report_lines.append(f"   Corrette: {correct_predictions}/{total_predicted} ({accuracy:.1f}%)")

        if n_value_bets > 0:
            report_lines.append(f"")
            report_lines.append(f"💰 Performance Value Bets (paper trading):")
            report_lines.append(f"   N. value bets: {n_value_bets}")
            report_lines.append(f"   Flat (1€/bet): {pnl_flat:+.2f}€ ({pnl_flat/n_value_bets*100:+.1f}% ROI)" if n_value_bets else "")
            report_lines.append(f"   Kelly P&L:    {pnl_kelly:+.2f}€")

    # Self-improvement stats
    err_count = db.conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0]
    err_pending = db.conn.execute("SELECT COUNT(*) FROM prediction_errors WHERE winner_correct IS NULL").fetchone()[0]
    if err_count > 0:
        err_ok = db.conn.execute("SELECT COUNT(*) FROM prediction_errors WHERE winner_correct=1").fetchone()[0]
        err_total = db.conn.execute("SELECT COUNT(*) FROM prediction_errors WHERE winner_correct IS NOT NULL").fetchone()[0]
        err_rate = err_ok / err_total * 100 if err_total > 0 else 0
        report_lines.append(f"")
        report_lines.append(f"🔄 Self-Improvement:")
        report_lines.append(f"   Errori totali loggati: {err_count}")
        report_lines.append(f"   Di cui risolti: {err_total} (accuracy: {err_rate:.1f}%)")
        report_lines.append(f"   In attesa risultato: {err_pending}")

    # Coverage stats
    total_odds = db.conn.execute("SELECT COUNT(*) FROM tennis_odds").fetchone()[0]
    total_match = db.conn.execute("SELECT COUNT(*) FROM tennis_matches WHERE match_date >= '2026-01-01' AND w_sets > 0").fetchone()[0]
    with_odds = db.conn.execute("""
        SELECT COUNT(DISTINCT m.id) FROM tennis_matches m
        JOIN tennis_odds o ON o.match_id = m.id
        WHERE m.match_date >= '2026-01-01' AND m.w_sets > 0
    """).fetchone()[0]
    report_lines.append(f"")
    report_lines.append(f"📊 Copertura 2026:")
    report_lines.append(f"   Match con odds: {with_odds}/{total_match} ({with_odds/max(total_match,1)*100:.0f}%)")
    report_lines.append(f"   Odds totali nel DB: {total_odds}")

    report_lines.append(f"")
    report_lines.append(f"💼 Bankroll corrente: {detector.kelly.bankroll:.0f} EUR")

    report_text = "\n".join(report_lines)
    
    # Also try Odds API for upcoming matches (if configured)
    try:
        from odds_api import generate_odds_report as odds_report
        odds_part = odds_report(target_date)
        if "⚠️ **The Odds API non configurata" not in odds_part and "Nessun match ATP" not in odds_part:
            report_text = odds_part + "\n\n" + "=" * 50 + "\n\n" + report_text
            print("[INFO] Odds API report integrato")
    except Exception as e:
        print(f"  [WARN] Odds API integration: {e}")
    
    # Run self-improvement analysis after predictions
    try:
        from self_improvement import run_self_improvement
        run_self_improvement(db, do_retrain_if_needed=True)
    except Exception as e:
        print(f"  [WARN] Self-improvement error: {e}")
    
    db.close()

    # Salva per delivery Discord
    os.makedirs("/opt/data/jbe-topspin-webapp/data/delivery", exist_ok=True)
    fname = f"report_{target_date.isoformat()}.txt"
    fpath = f"/opt/data/jbe-topspin-webapp/data/delivery/{fname}"
    with open(fpath, "w") as f:
        f.write(report_text)
    print(f"\n[OK] Report salvato: {fpath}")

    return report_text


def main():
    """Entry point per cron job."""
    with _StdoutToStderr():
        report = generate_report()
    print(report)


if __name__ == "__main__":
    main()
