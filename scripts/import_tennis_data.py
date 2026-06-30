"""
JBE TopSpin — Import da tennis-data.co.uk
Scarica e importa dati storici ATP (2001-2026).

Formato CSV (tennis-data.co.uk):
ATP,Location,Tournament,Date,Series,Court,Surface,Round,Best of,
Winner,Loser,WRank,LRank,WPts,LPts,W1,L1,W2,L2,W3,L3,W4,L4,W5,L5,
Wsets,Lsets,Comment,B365W,B365L,PSW,PSL,MaxW,MaxL,AvgW,AvgL
"""
import csv
import os
import sys
import urllib.request
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import IMPORT_DIR


SURFACE_MAP = {
    "Hard": "Hard",
    "Clay": "Clay",
    "Grass": "Grass",
    "Carpet": "Carpet",
}

TOUR_LEVEL_MAP = {
    "Grand Slam": "G",
    "Masters 1000": "M",
    "Masters Cup": "M",
    "ATP500": "A",
    "ATP250": "A",
    "ATP Cup": "A",
    "Davis Cup": "F",
    "Challenger": "C",
}

# tennis-data.co.uk URLs for ATP
ATP_URLS = [
    "https://tennis-data.co.uk/2026/2026.csv",
    "https://tennis-data.co.uk/2025/2025.csv",
    "https://tennis-data.co.uk/2024/2024.csv",
    "https://tennis-data.co.uk/2023/2023.csv",
    "https://tennis-data.co.uk/2022/2022.csv",
    "https://tennis-data.co.uk/2021/2021.csv",
    "https://tennis-data.co.uk/2020/2020.csv",
    "https://tennis-data.co.uk/2019/2019.csv",
    "https://tennis-data.co.uk/2018/2018.csv",
    "https://tennis-data.co.uk/2017/2017.csv",
    "https://tennis-data.co.uk/2016/2016.csv",
    "https://tennis-data.co.uk/2015/2015.csv",
    "https://tennis-data.co.uk/2014/2014.csv",
    "https://tennis-data.co.uk/2013/2013.csv",
    "https://tennis-data.co.uk/2012/2012.csv",
    "https://tennis-data.co.uk/2011/2011.csv",
    "https://tennis-data.co.uk/2010/2010.csv",
    "https://tennis-data.co.uk/2009/2009.csv",
    "https://tennis-data.co.uk/2008/2008.csv",
    "https://tennis-data.co.uk/2007/2007.csv",
    "https://tennis-data.co.uk/2006/2006.csv",
    "https://tennis-data.co.uk/2005/2005.csv",
    "https://tennis-data.co.uk/2004/2004.csv",
    "https://tennis-data.co.uk/2003/2003.csv",
    "https://tennis-data.co.uk/2002/2002.csv",
    "https://tennis-data.co.uk/2001/2001.csv",
]


def parse_surface(raw):
    """Parsa la superficie dal CSV."""
    raw = raw.strip().lower()
    if "hard" in raw:
        return "Hard"
    if "clay" in raw:
        return "Clay"
    if "grass" in raw:
        return "Grass"
    if "carpet" in raw:
        return "Carpet"
    return None


def parse_tour_level(series):
    """Determina il livello del torneo dalla serie."""
    series = series.strip()
    if "Grand Slam" in series:
        return "G"
    if "Masters" in series:
        return "M"
    if "ATP500" in series:
        return "A"
    if "ATP250" in series:
        return "A"
    if "Challenger" in series or "CH" in series:
        return "C"
    if "Davis" in series or "Cup" in series:
        return "F"
    return "A"


def download_csv(year_url):
    """Scarica un CSV da tennis-data.co.uk."""
    os.makedirs(IMPORT_DIR, exist_ok=True)
    fname = year_url.split("/")[-1]
    fpath = os.path.join(IMPORT_DIR, fname)

    if os.path.exists(fpath) and os.path.getsize(fpath) > 100:
        print(f"  [SKIP] {fname} gia' scaricato ({os.path.getsize(fpath)} bytes)")
        return fpath

    try:
        print(f"  [DOWNLOAD] {fname}...", end=" ", flush=True)
        urllib.request.urlretrieve(year_url, fpath)
        size = os.path.getsize(fpath)
        print(f"{size} bytes")
        return fpath
    except Exception as e:
        print(f"ERRORE: {e}")
        return None


def parse_score(score_str):
    """Parsa uno score '6-3 4-6 6-4' in game vinti per vincitore e perdente."""
    if not score_str or score_str.strip() == "":
        return 0, 0, 0, 0, score_str

    score_str = score_str.strip()
    sets = score_str.split()
    w_games_total = 0
    l_games_total = 0
    w_sets = 0
    l_sets = 0

    for s in sets:
        if "-" in s and s.count("-") == 1:
            try:
                parts = s.split("-")
                w = int(parts[0])
                l = int(parts[1])
                w_games_total += w
                l_games_total += l
                if w > l:
                    w_sets += 1
                else:
                    l_sets += 1
            except ValueError:
                pass

    return w_sets, l_sets, w_games_total, l_games_total, score_str


def import_csv(db, csv_path, year):
    """Importa un file CSV nel database."""
    if not csv_path:
        return 0

    with open(csv_path, "r", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        count = 0
        errors = 0

        for row in reader:
            try:
                # Salta righe senza risultato (walkover/retirement senza score)
                if not row.get("W1", "") and not row.get("Comment", ""):
                    continue

                # Determina superficie
                court = row.get("Court", "")
                surface = parse_surface(court)

                # Determina tour level
                series = row.get("Series", "")
                tour_level = parse_tour_level(series)

                # Determina indoor
                indoor = 1 if "indoor" in court.lower() else 0

                # Determina Bo3/Bo5
                best_of = int(row.get("Best of", 3)) if row.get("Best of", "").isdigit() else 3

                # Determina round
                round_val = row.get("Round", "").strip()
                # Standardizza: F, SF, QF, R16, R32, R64, R128, RR
                if round_val:
                    round_map = {
                        "Final": "F",
                        "Semifinal": "SF",
                        "Quarterfinal": "QF",
                        "3rd Round": "R16",
                        "4th Round": "R16",
                        "2nd Round": "R32",
                        "1st Round": "R64",
                        "Round Robin": "RR",
                    }
                    round_val = round_map.get(round_val, round_val)

                # Crea o trova giocatori
                winner_name = row.get("Winner", "").strip()
                loser_name = row.get("Loser", "").strip()
                if not winner_name or not loser_name:
                    continue

                winner_id, _ = db.get_or_create_player(winner_name)
                loser_id, _ = db.get_or_create_player(loser_name)

                # Ranking e punti
                w_rank = int(row.get("WRank", "0")) if row.get("WRank", "").isdigit() else 0
                l_rank = int(row.get("LRank", "0")) if row.get("LRank", "").isdigit() else 0
                w_pts = int(row.get("WPts", "0")) if row.get("WPts", "").isdigit() else 0
                l_pts = int(row.get("LPts", "0")) if row.get("LPts", "").isdigit() else 0

                # Score
                w1 = row.get("W1", ""); l1 = row.get("L1", "")
                w2 = row.get("W2", ""); l2 = row.get("L2", "")
                w3 = row.get("W3", ""); l3 = row.get("L3", "")
                w4 = row.get("W4", ""); l4 = row.get("L4", "")
                w5 = row.get("W5", ""); l5 = row.get("L5", "")
                
                score_parts = []
                for i, (ws, ls) in enumerate([(w1,l1),(w2,l2),(w3,l3),(w4,l4),(w5,l5)], 1):
                    if ws and ls and ws.isdigit() and ls.isdigit():
                        score_parts.append(f"{ws}-{ls}")
                score_str = " ".join(score_parts)

                w_sets, l_sets, w_games, l_games, _ = parse_score(score_str)

                # Commento (ritiro/walkover)
                comment = row.get("Comment", "").strip().lower()
                retirement = 1 if "retired" in comment else 0
                walkover = 1 if "walkover" in comment else 0
                retired_id = None
                if retirement or walkover:
                    # Chi si e' ritirato? Di solito il perdente
                    retired_id = loser_id

                # Data
                date_str = row.get("Date", "")
                try:
                    match_date = datetime.strptime(date_str, "%d/%m/%y").date().isoformat()
                except ValueError:
                    try:
                        match_date = datetime.strptime(date_str, "%Y-%m-%d").date().isoformat()
                    except ValueError:
                        continue

                # Inserisci match
                match_data = {
                    "match_date": match_date,
                    "tournament": row.get("Tournament", "").strip(),
                    "tour_level": tour_level,
                    "surface": surface,
                    "indoor": indoor,
                    "round": round_val,
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
                    "score": score_str,
                    "retirement": retirement,
                    "walkover": walkover,
                    "retired_player_id": retired_id,
                    "comment": row.get("Comment", "").strip(),
                    "source": "tennis-data.co.uk",
                }

                match_id = db.insert_match(match_data)
                if match_id is None:
                    continue  # Duplicato

                # Inserisci quote Pinnacle e Bet365
                ps_winner = row.get("PSW", "")
                ps_loser = row.get("PSL", "")
                b365_winner = row.get("B365W", "")
                b365_loser = row.get("B365L", "")

                if ps_winner and ps_loser:
                    try:
                        db.insert_odds(
                            match_id, "Pinnacle",
                            float(ps_winner), float(ps_loser),
                        )
                    except ValueError:
                        pass

                if b365_winner and b365_loser:
                    try:
                        db.insert_odds(
                            match_id, "Bet365",
                            float(b365_winner), float(b365_loser),
                        )
                    except ValueError:
                        pass

                count += 1

            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  [ERRORE] riga {count}: {e}")

    return count


def main():
    db = TennisDatabase()
    total = 0

    for url in ATP_URLS:
        year = url.split("/")[-2]
        print(f"\n--- {year} ---")
        csv_path = download_csv(url)
        n = import_csv(db, csv_path, year)
        total += n
        db.commit()
        print(f"  Importati: {n} match")

    print(f"\n=== TOTALE: {total} match importati ===")
    
    # Stats
    cur = db.conn.execute("SELECT COUNT(*) FROM tennis_matches")
    print(f"Match totali nel DB: {cur.fetchone()[0]}")
    cur = db.conn.execute("SELECT COUNT(*) FROM players")
    print(f"Giocatori: {cur.fetchone()[0]}")
    cur = db.conn.execute("SELECT COUNT(*) FROM tennis_odds")
    print(f"Quote: {cur.fetchone()[0]}")

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
