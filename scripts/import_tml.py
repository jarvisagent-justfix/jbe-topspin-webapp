"""
JBE TopSpin — Import da TML Database (TennisMyLife)
Formato: stats.tennismylife.org/tennis-match-database

Il formato TML segue la struttura Jeff Sackmann:
- tourney_id, tourney_name, surface, draw_size, tourney_level, tourney_date
- winner_id, winner_name, winner_hand, winner_ht, winner_ioc, winner_age, winner_rank
- loser_id, loser_name, loser_hand, loser_ht, loser_ioc, loser_age, loser_rank
- score, best_of, round, minutes
- w_ace, w_df, w_svpt, w_1stIn, w_1stWon, w_2ndWon, l_ace, l_df, l_svpt...
"""
import csv
import os
import sys
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import IMPORT_DIR


def parse_score(score_str):
    """Parsa score '6-3 4-6 6-4' in sets e games."""
    if not score_str:
        return 0, 0, 0, 0, ""

    score_str = score_str.strip()
    # Handle special formats
    if score_str.upper() in ("W/O", "RET", "DEF", "WALKOVER", "ABD"):
        return 0, 0, 0, 0, score_str

    sets = score_str.replace('[', ' ').replace(']', ' ').replace('RET', '').replace('DEF', '').split()
    w_games_total = 0
    l_games_total = 0
    w_sets = 0
    l_sets = 0
    clean_sets = []

    for s in sets:
        # Handle tiebreak notation: 7-6(3) or 7-6³
        s = s.split('(')[0].split('³')[0].split('²')[0].split('¹')[0]
        if "-" in s and s.count("-") == 1:
            try:
                parts = s.split("-")
                w = int(parts[0])
                l = int(parts[1])
                # Skip obviously wrong scores
                if w > 7 or l > 7:
                    continue
                w_games_total += w
                l_games_total += l
                if w > l:
                    w_sets += 1
                else:
                    l_sets += 1
                clean_sets.append(f"{w}-{l}")
            except ValueError:
                continue

    return w_sets, l_sets, w_games_total, l_games_total, " ".join(clean_sets)


def determine_retirement(score_str):
    """Determina se il match e' finito per ritiro/walkover dalla stringa score."""
    if not score_str:
        return False, False
    s = score_str.upper()
    retirement = "RET" in s or "ABD" in s
    walkover = "W/O" in s or "WALKOVER" in s or "DEF" in s
    return retirement, walkover


def determine_tour_level(level_code, tourney_name):
    """Converte il codice tourney_level TML in livello standard."""
    mapping = {
        'G': 'G',   # Grand Slam
        'M': 'M',   # Masters
        'F': 'F',   # Finals / Davis Cup
        'A': 'A',   # ATP Tour
        'C': 'C',   # Challenger
        'D': 'F',   # Davis Cup
    }
    return mapping.get(level_code, 'A')


def parse_date(tourney_date):
    """Parsa tourney_date in formato YYYY-MM-DD."""
    d = str(tourney_date).strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None


def import_csv(db, csv_path, year, challenger=False):
    """Importa un file CSV TML nel database."""
    if not csv_path or not os.path.exists(csv_path):
        return 0

    count = 0
    errors = 0
    skipped = 0
    first = True

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Skip header if re-read
                if first:
                    if 'winner_name' not in row and 'winner_id' not in row:
                        continue
                    first = False

                winner_name = (row.get("winner_name") or "").strip()
                loser_name = (row.get("loser_name") or "").strip()
                if not winner_name or not loser_name:
                    skipped += 1
                    continue

                score_str = (row.get("score") or "").strip()
                retirement, walkover = determine_retirement(score_str)
                w_sets, l_sets, w_games, l_games, clean_score = parse_score(score_str)

                # Salta match senza risultato se non e' ritiro/walkover
                if w_sets == 0 and not retirement and not walkover:
                    skipped += 1
                    continue

                # Crea giocatori
                winner_id, _ = db.get_or_create_player(winner_name)
                loser_id, _ = db.get_or_create_player(loser_name)

                # Salta match identici (stessa data, stessi giocatori)
                match_date = parse_date(row.get("tourney_date", ""))
                if not match_date:
                    match_date = f"{year}-01-01"

                # Ranking
                def safe_int(val, default=0):
                    if val and str(val).isdigit():
                        return int(val)
                    return default

                w_rank = safe_int(row.get("winner_rank"))
                l_rank = safe_int(row.get("loser_rank"))
                w_pts = safe_int(row.get("winner_rank_points"))
                l_pts = safe_int(row.get("loser_rank_points"))

                # Superficie
                surface = row.get("surface", "").strip().capitalize()
                if surface not in ("Hard", "Clay", "Grass", "Carpet"):
                    surface = None

                # Indoor
                indoor_str = (row.get("indoor") or "").strip().lower()
                indoor = 1 if indoor_str in ("yes", "y", "true", "1") else 0

                # Best of
                best_of = safe_int(row.get("best_of"), 3) or 3

                # Tour level e tournament
                tour_level_code = (row.get("tourney_level") or "A").strip()
                tour_level = determine_tour_level(tour_level_code, row.get("tourney_name", ""))

                # Round
                round_val = (row.get("round") or "").strip()
                if round_val:
                    # Già nel formato R128, R64, q, ecc.
                    round_val = round_val.replace("R", "R").replace("Q", "q")
                    # Unifica: q1, q2, q3 -> q
                    if round_val.startswith("q"):
                        round_val = "q"

                # Statistiche serve/return (se presenti)
                def safe_float(val):
                    if val and str(val).replace('.','',1).isdigit():
                        return int(float(val))
                    return None

                match_data = {
                    "match_date": match_date,
                    "tournament": row.get("tourney_name", "").strip() or f"Tournament_{year}",
                    "tour_level": tour_level,
                    "surface": surface,
                    "indoor": indoor,
                    "round": round_val or None,
                    "best_of": best_of,
                    "winner_id": winner_id,
                    "loser_id": loser_id,
                    "winner_rank": w_rank or None,
                    "loser_rank": l_rank or None,
                    "winner_rank_points": w_pts or None,
                    "loser_rank_points": l_pts or None,
                    "w_sets": w_sets,
                    "l_sets": l_sets,
                    "w_games": w_games,
                    "l_games": l_games,
                    "score": clean_score or score_str,
                    "retirement": 1 if retirement else 0,
                    "walkover": 1 if walkover else 0,
                    "retired_player_id": loser_id if retirement else None,
                    "source": "TML",
                    "w_ace": safe_float(row.get("w_ace")),
                    "w_df": safe_float(row.get("w_df")),
                    "w_svpt": safe_float(row.get("w_svpt")),
                    "w_1st_in": safe_float(row.get("w_1stIn")),
                    "w_1st_won": safe_float(row.get("w_1stWon")),
                    "w_2nd_won": safe_float(row.get("w_2ndWon")),
                    "l_ace": safe_float(row.get("l_ace")),
                    "l_df": safe_float(row.get("l_df")),
                    "l_svpt": safe_float(row.get("l_svpt")),
                    "l_1st_in": safe_float(row.get("l_1stIn")),
                    "l_1st_won": safe_float(row.get("l_1stWon")),
                    "l_2nd_won": safe_float(row.get("l_2ndWon")),
                }

                match_id = db.insert_match(match_data)
                if match_id is not None:
                    count += 1

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  [ERR] riga ~{count}: {e}")

    return count


def extract_and_import(db, zip_path, years=None):
    """Estrae i CSV dallo zip e li importa."""
    if not os.path.exists(zip_path):
        print(f"ZIP non trovato: {zip_path}")
        return 0

    total = 0
    with zipfile.ZipFile(zip_path, 'r') as z:
        # Lista file nello zip
        csv_files = [f for f in z.namelist() if f.endswith('.csv') and not f.endswith('_challenger.csv')]
        print(f"File CSV nello zip: {len(csv_files)}")

        for csv_file in sorted(csv_files):
            try:
                # Estrai anno dal nome file
                fname = os.path.basename(csv_file)
                year = fname.replace('.csv', '')
                if not year.isdigit():
                    continue
                
                year_int = int(year)
                if years and year_int not in years:
                    continue
                if year_int < 2001:
                    continue  # Skip pre-2001 (no odds data anyway)

                # Estrai e importa
                z.extract(csv_file, IMPORT_DIR)
                csv_path = os.path.join(IMPORT_DIR, csv_file)
                n = import_csv(db, csv_path, year)
                total += n
                db.commit()
                print(f"  {year}: {n} match importati")
                
                # Pulisci
                os.remove(csv_path)

            except Exception as e:
                print(f"  ERRORE {csv_file}: {e}")

    return total


def main():
    db = TennisDatabase()

    # Verifica se lo zip esiste (scaricato dallo script principale)
    zip_path = os.path.join(IMPORT_DIR, "tml_all.zip")

    if not os.path.exists(zip_path):
        print("ZIP non trovato. Scarica prima con lo script download_tml.py")
        # Prova a importare da CSV gia' presenti
        for f in sorted(os.listdir(IMPORT_DIR)):
            if f.endswith('.csv') and f.replace('.csv','').isdigit():
                year = f.replace('.csv', '')
                n = import_csv(db, os.path.join(IMPORT_DIR, f), year)
                db.commit()
                print(f"  {year}: {n} match")
    else:
        total = extract_and_import(db, zip_path)
        print(f"\n=== TOTALE: {total} match importati ===")

    # Stats finali
    cur = db.conn.execute("SELECT COUNT(*) FROM tennis_matches")
    print(f"Match totali nel DB: {cur.fetchone()[0]}")
    cur = db.conn.execute("SELECT COUNT(*) FROM players")
    print(f"Giocatori: {cur.fetchone()[0]}")
    cur = db.conn.execute("SELECT surface, COUNT(*) FROM tennis_matches WHERE surface IS NOT NULL GROUP BY surface")
    print("\nMatch per superficie:")
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1]}")
    cur = db.conn.execute("SELECT MIN(match_date), MAX(match_date) FROM tennis_matches")
    r = cur.fetchone()
    print(f"\nRange date: {r[0]} -> {r[1]}")

    db.close()


if __name__ == "__main__":
    main()
