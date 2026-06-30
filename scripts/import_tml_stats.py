#!/usr/bin/env python3
"""
JBE TopSpin — Import TML Serve/Return Stats
=============================================
Legge i dati TML (Jeff Sackmann tennis_matches), calcola p_serve
per ogni giocatore per superficie, e popola serve_return_params.

p_serve = (w_1stWon + w_2ndWon) / w_svpt
q_return = 1 - opponent_p_serve

Uso: PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/import_tml_stats.py
"""
import sys, os, csv, zipfile, math
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import DB_PATH, SURFACES

TML_ZIP = "/opt/data/jbe-tennis/data/import/tml_all.zip"
MIN_POINTS = 50  # Minimo punti servizio per calcolo affidabile


def compute_p_serve(serve_data):
    """
    Dati serve_data = lista di dict con w_svpt, w_1stWon, w_2ndWon per un giocatore.
    Calcola p_serve rolling con decay (più peso ai match recenti).
    """
    if not serve_data:
        return None

    total_won = 0
    total_pts = 0
    total_weight = 0

    for sd in serve_data:
        svpt = sd.get("w_svpt") or sd.get("l_svpt") or 0
        if svpt < 10:
            continue

        # Chi ha servito: se winner -> w_ stats, se loser -> l_ stats
        is_winner = "w_svpt" in sd
        if is_winner:
            won = (sd.get("w_1stWon") or 0) + (sd.get("w_2ndWon") or 0)
        else:
            won = (sd.get("l_1stWon") or 0) + (sd.get("l_2ndWon") or 0)

        # Decay quadratico: match recenti pesano di piu'
        # weight = 1 / sqrt(match_age_in_months + 1)
        match_date = sd.get("match_date")
        days_ago = max(0, (date.today() - match_date).days) if match_date else 0
        weight = 1.0 / math.sqrt(days_ago / 30 + 1)

        total_won += won * weight
        total_pts += svpt * weight
        total_weight += weight

    if total_pts < MIN_POINTS:
        return None

    return total_won / total_pts


def main():
    print("=" * 60)
    print("  JBE TopSpin — Import TML Serve/Return Stats")
    print("=" * 60)

    db = TennisDatabase(DB_PATH)

    # Verifica esistenza zip
    if not os.path.exists(TML_ZIP):
        print(f"[ERRORE] File non trovato: {TML_ZIP}")
        return

    # Leggi tutti i file dal 2010 in poi (dati più rilevanti)
    z = zipfile.ZipFile(TML_ZIP)

    years = sorted(set(f.split(".")[0].split("_")[0] for f in z.namelist()
                        if f.split(".")[0].split("_")[0].isdigit()))
    years = [y for y in years if int(y) >= 2010]
    print(f"\n  Anni da processare: {years[0]}-{years[-1]} ({len(years)} file)")

    # Colleziona dati serve per ogni giocatore per superficie
    player_surface_data = defaultdict(list)

    for year in years:
        files_in_year = [f for f in z.namelist() if f.startswith(year)]
        for filepath in files_in_year:
            try:
                with z.open(filepath) as f:
                    content = f.read().decode("utf-8", errors="replace")
                    reader = csv.DictReader(content.splitlines())

                    for row in reader:
                        surface = row.get("surface", "").strip()
                        if not surface or surface not in SURFACES:
                            continue

                        # Salta walkover/retirement senza stats
                        w_svpt = row.get("w_svpt", "").strip()
                        if not w_svpt or int(w_svpt) < 5:
                            continue

                        winner_id = row.get("winner_id", "").strip()
                        loser_id = row.get("loser_id", "").strip()
                        if not winner_id or not loser_id:
                            continue

                        # Converte punteggi in int
                        w_1stWon = int(row.get("w_1stWon", 0) or 0)
                        w_2ndWon = int(row.get("w_2ndWon", 0) or 0)
                        w_svpt = int(w_svpt)
                        l_1stWon = int(row.get("l_1stWon", 0) or 0)
                        l_2ndWon = int(row.get("l_2ndWon", 0) or 0)
                        l_svpt = int(row.get("l_svpt", 0) or 0)

                        tourney_date_str = row.get("tourney_date", "").strip()
                        try:
                            match_date = date(
                                int(tourney_date_str[:4]),
                                int(tourney_date_str[4:6]),
                                int(tourney_date_str[6:8])
                            )
                        except (ValueError, IndexError):
                            match_date = date.today()

                        # Salviamo dati serve per winner e loser
                        player_surface_data[(winner_id, surface)].append({
                            "w_svpt": w_svpt,
                            "w_1stWon": w_1stWon,
                            "w_2ndWon": w_2ndWon,
                            "match_date": match_date,
                            "tourney_name": row.get("tourney_name", ""),
                        })
                        player_surface_data[(loser_id, surface)].append({
                            "l_svpt": l_svpt,
                            "l_1stWon": l_1stWon,
                            "l_2ndWon": l_2ndWon,
                            "match_date": match_date,
                            "tourney_name": row.get("tourney_name", ""),
                        })

            except Exception as e:
                print(f"  [ERRORE] {filepath}: {e}")
                continue

        if int(year) % 5 == 0:
            print(f"  Processato {year}: {len(player_surface_data)} giocatori/superficie")

    print(f"\n  Dati raccolti: {len(player_surface_data)} combinazioni giocatore/superficie")

    # Calcola p_serve per ogni giocatore per superficie
    surface_defaults = {
        "Hard": 0.63,
        "Clay": 0.60,
        "Grass": 0.64,
        "Carpet": 0.62,
    }

    inserted = 0
    for (tml_id, surface), serve_data in player_surface_data.items():
        # Match TML ID -> DB player
        player = db.conn.execute(
            "SELECT id FROM players WHERE atp_id=?",
            (tml_id,)
        ).fetchone()

        if not player:
            # Prova name matching
            continue

        player_id = player["id"]

        # Calcola p_serve
        p_serve = compute_p_serve(serve_data)
        if p_serve is None:
            continue

        # q_return = 1 - p_serve medio dell'avversario su quella superficie
        # Per ora usiamo il default di superficie come approssimazione
        q_return = 1.0 - surface_defaults.get(surface, 0.63)

        n_matches = len([s for s in serve_data
                        if (s.get("w_svpt") or s.get("l_svpt") or 0) >= 10])

        confidence = min(n_matches / 20, 1.0)

        db.conn.execute("""
            INSERT INTO serve_return_params
            (player_id, surface, p_serve, q_return, matches_on_surface,
             points_serve_won, points_serve_total, confidence, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(player_id, surface) DO UPDATE SET
                p_serve=excluded.p_serve,
                q_return=excluded.q_return,
                matches_on_surface=excluded.matches_on_surface,
                confidence=excluded.confidence,
                last_updated=CURRENT_TIMESTAMP
        """, (
            player_id, surface, round(p_serve, 4), round(q_return, 4),
            n_matches,
            int(sum(s.get("w_1stWon", 0) + s.get("w_2ndWon", 0) +
                    s.get("l_1stWon", 0) + s.get("l_2ndWon", 0)
                    for s in serve_data)),
            int(sum(s.get("w_svpt", 0) or s.get("l_svpt", 0) or 0
                    for s in serve_data)),
            round(confidence, 4),
        ))
        inserted += 1

    db.conn.commit()

    # Stats finali
    total = db.conn.execute("SELECT COUNT(*) FROM serve_return_params").fetchone()[0]
    players = db.conn.execute("SELECT COUNT(DISTINCT player_id) FROM serve_return_params").fetchone()[0]
    print(f"\n  Inseriti/aggiornati: {inserted}")
    print(f"  Totale serve_return_params: {total}")
    print(f"  Giocatori con stats: {players}")

    # Top p_serve per superficie
    for surface in SURFACES:
        top = db.conn.execute("""
            SELECT p.name, s.p_serve, s.matches_on_surface
            FROM serve_return_params s
            JOIN players p ON p.id = s.player_id
            WHERE s.surface = ? AND s.matches_on_surface > 20
            ORDER BY s.p_serve DESC LIMIT 5
        """, (surface,)).fetchall()
        if top:
            print(f"\n  Top p_serve {surface}:")
            for row in top:
                print(f"    {row['name']:20s} {row['p_serve']:.4f} ({row['matches_on_surface']} match)")

    z.close()
    db.close()
    print("\n[OK] Import TML completato!")


if __name__ == "__main__":
    main()
