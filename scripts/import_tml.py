"""
JBE TopSpin — Import da TML Database (TennisMyLife)
===================================================
Unico punto di ingresso per l'import dei dati TML.
Jeff Sackmann / TML-Database: stats.tennismylife.org

Modalità:
  --mode matches  : Importa match storici (risultati, ranking, statistiche)
  --mode stats    : Calcola e importa statistiche serve/return per giocatore

Perché unificato:
  - import_tml.py e import_tml_stats.py operavano sullo stesso dataset TML
    ma in due script separati, condividendo logica di parsing e connessione DB.
  - Unificarli evita duplicazione di codice e garantisce consistenza tra
    i match importati e le statistiche calcolate.

Uso:
  PYTHONPATH=src python3 scripts/import_tml.py --mode matches
  PYTHONPATH=src python3 scripts/import_tml.py --mode stats
"""
import csv, os, sys, zipfile, math
from datetime import datetime, date, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import IMPORT_DIR, DB_PATH, SURFACES

MIN_POINTS = 50      # Minimo punti servizio per calcolo statistiche affidabile
MIN_MATCHES = 5      # Match minimi su superficie per calcolo affidabile
DECAY_YEARS = 3      # Solo match degli ultimi N anni per stats


# ============================================================
# SHARED UTILITY
# ============================================================
def parse_score(score_str):
    """Parsa score '6-3 4-6 6-4' in sets e games."""
    if not score_str:
        return 0, 0, 0, 0, score_str
    score_str = score_str.strip()
    if score_str.upper() in ("W/O", "RET", "DEF", "WALKOVER", "ABD"):
        return 0, 0, 0, 0, score_str
    sets = score_str.replace('[', ' ').replace(']', ' ').replace('RET', '').replace('DEF', '').split()
    w_sets = 0
    l_sets = 0
    w_games_total = 0
    l_games_total = 0
    for s in sets:
        if '-' in s and '(' not in s:
            try:
                parts = s.split('-')
                g1, g2 = int(parts[0]), int(parts[1])
                w_games_total += g1
                l_games_total += g2
                if g1 > g2:
                    w_sets += 1
                else:
                    l_sets += 1
            except (ValueError, IndexError):
                pass
    return w_sets, l_sets, w_games_total, l_games_total, score_str


def determine_retirement(score_str):
    """Verifica se il risultato indica un ritiro."""
    if not score_str:
        return False, False
    s = score_str.upper()
    return "RET" in s, "W/O" in s or "WALKOVER" in s or "DEF" in s


# ============================================================
# MODALITÀ MATCHES
# ============================================================
def _load_tml_zip(zip_path):
    """Carica i dati dal file ZIP TML."""
    if not os.path.exists(zip_path):
        print(f"[ERRORE] ZIP non trovato: {zip_path}")
        return []
    with zipfile.ZipFile(zip_path) as z:
        csv_files = [n for n in z.namelist() if n.endswith('.csv') and 'atp_matches' in n]
        if not csv_files:
            print(f"[ERRORE] Nessun file CSV trovato in {zip_path}")
            return []
        csv_files.sort()
        print(f"  File CSV trovati: {len(csv_files)}")
        all_rows = []
        for cf in csv_files:
            print(f"  Leggo {cf}...")
            with z.open(cf) as f:
                reader = csv.DictReader(line.decode('latin-1') for line in f)
                for row in reader:
                    all_rows.append(row)
        return all_rows


def import_matches():
    """Importa match storici TML nel database."""
    zip_path = os.path.join(IMPORT_DIR, "tml_all.zip")
    print(f"[INFO] Caricamento TML ZIP: {zip_path}")
    rows = _load_tml_zip(zip_path)
    print(f"  Righe totali: {len(rows)}")

    db = TennisDatabase(DB_PATH)
    imported = 0
    skipped = 0

    for row in rows:
        try:
            score_str = (row.get("score") or "").strip()
            retirement, walkover = determine_retirement(score_str)
            w_sets, l_sets, w_games, l_games, clean_score = parse_score(score_str)
            if w_sets == 0 and l_sets == 0 and not retirement and not walkover:
                skipped += 1
                continue

            # Crea/recupera giocatori
            winner_id, _ = db.get_or_create_player(
                row.get("winner_name", "").strip(),
                atp_id=row.get("winner_id"),
                country=row.get("winner_ioc"),
            )
            loser_id, _ = db.get_or_create_player(
                row.get("loser_name", "").strip(),
                atp_id=row.get("loser_id"),
                country=row.get("loser_ioc"),
            )

            # Data torneo
            tourney_date = row.get("tourney_date", "")
            if len(tourney_date) == 8:
                try:
                    match_date = datetime.strptime(tourney_date, "%Y%m%d").date()
                except ValueError:
                    match_date = date.today()
            else:
                continue

            surface = row.get("surface", "Hard")
            if surface not in SURFACES:
                surface = "Hard"

            best_of = int(row.get("best_of", 3)) if row.get("best_of") else 3

            match_id = db.insert_match({
                "match_date": match_date.isoformat(),
                "tournament": row.get("tourney_name", "").strip(),
                "tour_level": row.get("tourney_level", "A"),
                "surface": surface,
                "round": row.get("round", ""),
                "best_of": best_of,
                "winner_id": winner_id,
                "loser_id": loser_id,
                "winner_rank": int(row.get("winner_rank", 0)) if row.get("winner_rank") else None,
                "loser_rank": int(row.get("loser_rank", 0)) if row.get("loser_rank") else None,
                "winner_rank_points": int(row.get("winner_rank_points", 0)) if row.get("winner_rank_points") else None,
                "loser_rank_points": int(row.get("loser_rank_points", 0)) if row.get("loser_rank_points") else None,
                "w_sets": w_sets,
                "l_sets": l_sets,
                "w_games": w_games,
                "l_games": l_games,
                "score": clean_score or score_str,
                "retirement": retirement,
                "walkover": walkover,
                "source": "TML-Database",
                # Statistiche servizio
                "w_ace": int(row.get("w_ace", 0)) if row.get("w_ace") else 0,
                "w_df": int(row.get("w_df", 0)) if row.get("w_df") else 0,
                "w_svpt": int(row.get("w_svpt", 0)) if row.get("w_svpt") else 0,
                "w_1st_in": int(row.get("w_1stIn", 0)) if row.get("w_1stIn") else 0,
                "w_1st_won": int(row.get("w_1stWon", 0)) if row.get("w_1stWon") else 0,
                "w_2nd_won": int(row.get("w_2ndWon", 0)) if row.get("w_2ndWon") else 0,
                "l_ace": int(row.get("l_ace", 0)) if row.get("l_ace") else 0,
                "l_df": int(row.get("l_df", 0)) if row.get("l_df") else 0,
                "l_svpt": int(row.get("l_svpt", 0)) if row.get("l_svpt") else 0,
                "l_1st_in": int(row.get("l_1stIn", 0)) if row.get("l_1stIn") else 0,
                "l_1st_won": int(row.get("l_1stWon", 0)) if row.get("l_1stWon") else 0,
                "l_2nd_won": int(row.get("l_2ndWon", 0)) if row.get("l_2ndWon") else 0,
            })
            if match_id:
                imported += 1
            else:
                skipped += 1
        except Exception as e:
            skipped += 1
            if skipped < 5:
                print(f"  [ERRORE] Riga {imported + skipped}: {e}")
            continue

    db.commit()
    db.close()
    print(f"\n[OK] Importati: {imported} | Saltati: {skipped} | Totale: {imported + skipped}")


# ============================================================
# MODALITÀ STATS (calcolo serve/return params)
# ============================================================
def _compute_p_serve_from_db(db):
    """
    Calcola p_serve per ogni giocatore per superficie dai match del DB.
    I match recenti hanno peso maggiore (decay lineare).
    """
    cut_date = (date.today() - timedelta(days=365 * DECAY_YEARS)).isoformat()

    rows = db.conn.execute("""
        SELECT m.id, m.winner_id, m.loser_id, m.surface, m.match_date,
               m.w_svpt, m.w_1st_won, m.w_2nd_won, m.l_svpt,
               m.l_1st_won, m.l_2nd_won,
               w.name as wname, l.name as lname
        FROM tennis_matches m
        JOIN players w ON w.id = m.winner_id
        JOIN players l ON l.id = m.loser_id
        WHERE m.match_date >= ?
          AND m.w_svpt > 0
          AND m.surface IS NOT NULL
        ORDER BY m.match_date
    """, (cut_date,)).fetchall()

    print(f"  Match con stats servizio: {len(rows)}")

    # Per ogni giocatore e superficie, accumula punti servizio e punti vinti
    serve_data = defaultdict(lambda: defaultdict(lambda: {"points_won": 0, "points_total": 0, "matches": 0}))
    return_data = defaultdict(lambda: defaultdict(lambda: {"points_won": 0, "points_total": 0, "matches": 0}))

    for m in rows:
        surface = m["surface"]
        if surface not in SURFACES:
            continue
        # Winner serve stats
        w_pts = (m["w_1st_won"] or 0) + (m["w_2nd_won"] or 0)
        w_tot = m["w_svpt"] or 0
        if w_tot >= MIN_POINTS:
            serve_data[m["winner_id"]][surface]["points_won"] += int(w_pts)
            serve_data[m["winner_id"]][surface]["points_total"] += int(w_tot)
            serve_data[m["winner_id"]][surface]["matches"] += 1
            # Loser return data (return against winner's serve)
            return_data[m["loser_id"]][surface]["points_total"] += int(w_tot)
            return_data[m["loser_id"]][surface]["points_won"] += int(w_tot - w_pts)
            return_data[m["loser_id"]][surface]["matches"] += 1

        # Loser serve stats
        l_pts = (m["l_1st_won"] or 0) + (m["l_2nd_won"] or 0)
        l_tot = m["l_svpt"] or 0
        if l_tot >= MIN_POINTS:
            serve_data[m["loser_id"]][surface]["points_won"] += int(l_pts)
            serve_data[m["loser_id"]][surface]["points_total"] += int(l_tot)
            serve_data[m["loser_id"]][surface]["matches"] += 1
            # Winner return data
            return_data[m["winner_id"]][surface]["points_total"] += int(l_tot)
            return_data[m["winner_id"]][surface]["points_won"] += int(l_tot - l_pts)
            return_data[m["winner_id"]][surface]["matches"] += 1

    # Defaults per superficie (se non ci sono dati sufficienti)
    defaults = {"Hard": (0.63, 0.37), "Clay": (0.60, 0.40), "Grass": (0.64, 0.36), "Carpet": (0.62, 0.38)}

    # Salva parametri nel DB
    saved = 0
    for player_id, surfaces in serve_data.items():
        for surface, data in surfaces.items():
            p_serve = data["points_won"] / data["points_total"] if data["points_total"] > 0 else defaults.get(surface, (0.63, 0.37))[0]

            # q_return = probabilita' di vincere un punto in risposta
            # = 1 - p_serve medio dell'avversario su quella superficie
            ret_data = return_data.get(player_id, {}).get(surface, {})
            if ret_data["points_total"] > 0:
                q_return = ret_data["points_won"] / ret_data["points_total"]
            else:
                q_return = defaults.get(surface, (0.63, 0.37))[1]

            confidence = min(data["matches"] / MIN_MATCHES, 1.0)

            db.conn.execute("""
                INSERT INTO serve_return_params
                (player_id, surface, p_serve, q_return, matches_on_surface,
                 points_serve_won, points_serve_total, confidence, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, surface) DO UPDATE SET
                    p_serve = excluded.p_serve, q_return = excluded.q_return,
                    matches_on_surface = excluded.matches_on_surface,
                    points_serve_won = excluded.points_serve_won,
                    points_serve_total = excluded.points_serve_total,
                    confidence = excluded.confidence,
                    last_updated = excluded.last_updated
            """, (player_id, surface, p_serve, q_return, data["matches"],
                  data["points_won"], data["points_total"], confidence, date.today().isoformat()))
            saved += 1

    db.commit()
    print(f"  Parametri serve/return salvati: {saved}")
    return saved


def import_stats():
    """Calcola e importa statistiche serve/return per tutti i giocatori."""
    print("[INFO] Calcolo statistiche serve/return...")
    db = TennisDatabase(DB_PATH)
    saved = _compute_p_serve_from_db(db)
    db.close()
    print(f"[OK] Calcolo completato. {saved} parametri salvati.")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    mode = "matches"
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--mode" and i + 1 < len(sys.argv[1:]):
            mode = sys.argv[i + 2]
        elif arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]

    if mode == "stats":
        import_stats()
    else:
        import_matches()
