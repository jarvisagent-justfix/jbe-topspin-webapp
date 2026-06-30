#!/usr/bin/env python3
"""
JBE TopSpin — Calcola p_serve dal DB
======================================
Usa i dati serve/return gia' presenti in tennis_matches (92% copertura)
per calcolare p_serve e q_return per ogni giocatore per superficie.

p_serve = (w_1stWon + w_2ndWon) / w_svpt  (quando il giocatore serve)
q_return = 1 - p_serve avversario medio sulla superficie

Uso: PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/compute_serve_params.py
"""
import sys, os, math
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import DB_PATH, SURFACES

MIN_MATCHES = 5       # Match minimi su superficie per calcolo affidabile
DECAY_YEARS = 3       # Solo match degli ultimi N anni contano


def compute_p_serve(db, surface_defaults=None):
    """
    Calcola p_serve per ogni giocatore per superficie dai match del DB.
    Match recenti hanno peso maggiore (decay lineare).
    """
    if surface_defaults is None:
        surface_defaults = {
            "Hard": 0.63, "Clay": 0.60, "Grass": 0.64, "Carpet": 0.62
        }

    cutoff = (date.today() - timedelta(days=DECAY_YEARS * 365)).isoformat()
    print(f"  Cutoff: {cutoff} (ultimi {DECAY_YEARS} anni)")

    # Raccogli dati serve per ogni giocatore per superficie
    # Quando il giocatore e' winner -> w_svpt, w_1st_won, w_2nd_won
    # Quando e' loser -> l_svpt, l_1st_won, l_2nd_won
    player_surface_data = defaultdict(lambda: {
        "total_serve_pts": 0,
        "total_serve_won": 0,
        "total_return_pts": 0,
        "total_return_won": 0,
        "n_matches": 0,
        "weighted_serve_pts": 0,
        "weighted_serve_won": 0,
    })

    matches = db.conn.execute("""
        SELECT m.id, m.winner_id, m.loser_id, m.surface, m.match_date,
               m.w_svpt, m.w_1st_won, m.w_2nd_won,
               m.l_svpt, m.l_1st_won, m.l_2nd_won
        FROM tennis_matches m
        WHERE m.match_date >= ?
          AND m.surface IS NOT NULL
          AND m.w_svpt IS NOT NULL AND m.w_svpt > 0
    """, (cutoff,)).fetchall()

    print(f"  Match con dati serve: {len(matches)}")

    for m in matches:
        surface = m["surface"]
        if surface not in surface_defaults:
            continue

        match_date = m["match_date"]

        # Calcola eta' del match in mesi per decay weight
        try:
            if isinstance(match_date, str):
                md = date.fromisoformat(match_date) if len(match_date) == 10 else date.today()
            else:
                md = match_date
            age_months = max(0, (date.today() - md).days / 30)
        except Exception:
            age_months = 0

        weight = 1.0 / math.sqrt(age_months + 1)  # Decay non-lineare

        # Winner serve stats
        w_svpt = m["w_svpt"] or 0
        w_won = (m["w_1st_won"] or 0) + (m["w_2nd_won"] or 0)
        if w_svpt >= 10:
            pid = m["winner_id"]
            pd = player_surface_data[(pid, surface)]
            pd["total_serve_pts"] += w_svpt
            pd["total_serve_won"] += w_won
            pd["weighted_serve_pts"] += w_svpt * weight
            pd["weighted_serve_won"] += w_won * weight
            pd["n_matches"] += 1

        # Loser serve stats
        l_svpt = m["l_svpt"] or 0
        l_won = (m["l_1st_won"] or 0) + (m["l_2nd_won"] or 0)
        if l_svpt >= 10:
            pid = m["loser_id"]
            pd = player_surface_data[(pid, surface)]
            pd["total_serve_pts"] += l_svpt
            pd["total_serve_won"] += l_won
            pd["weighted_serve_pts"] += l_svpt * weight
            pd["weighted_serve_won"] += l_won * weight
            pd["n_matches"] += 1

    print(f"  Combinazioni giocatore/superficie: {len(player_surface_data)}")

    # Calcola p_serve medio per superficie (per default)
    surface_avg = {}
    for surface in SURFACES:
        all_data = [v for k, v in player_surface_data.items() if k[1] == surface and v["total_serve_pts"] >= 1000]
        if all_data:
            total_won = sum(d["total_serve_won"] for d in all_data)
            total_pts = sum(d["total_serve_pts"] for d in all_data)
            surface_avg[surface] = total_won / total_pts if total_pts > 0 else surface_defaults[surface]
        else:
            surface_avg[surface] = surface_defaults[surface]
        print(f"  p_serve medio {surface}: {surface_avg[surface]:.4f}")

    # Popola serve_return_params
    inserted = 0
    for (player_id, surface), data in player_surface_data.items():
        if data["n_matches"] < MIN_MATCHES:
            continue

        # p_serve = weighted average
        if data["weighted_serve_pts"] > 0:
            p_serve = data["weighted_serve_won"] / data["weighted_serve_pts"]
        else:
            p_serve = data["total_serve_won"] / data["total_serve_pts"] if data["total_serve_pts"] > 0 else surface_avg[surface]

        # q_return = 1 - p_serve medio avversario su quella superficie
        # Approssimazione: 1 - p_serve default superficie
        q_return = 1.0 - surface_avg.get(surface, 0.63)
        
        # Confidence (0-1) basata sul numero di match
        confidence = min(data["n_matches"] / 50, 1.0)

        # Regolarizzazione: se pochi match, tira verso la media di superficie
        if data["n_matches"] < 20:
            alpha = data["n_matches"] / 20
            p_serve = alpha * p_serve + (1 - alpha) * surface_avg.get(surface, 0.63)

        db.conn.execute("""
            INSERT INTO serve_return_params
            (player_id, surface, p_serve, q_return, matches_on_surface,
             points_serve_won, points_serve_total, confidence, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(player_id, surface) DO UPDATE SET
                p_serve=excluded.p_serve, q_return=excluded.q_return,
                matches_on_surface=excluded.matches_on_surface,
                confidence=excluded.confidence, last_updated=CURRENT_TIMESTAMP
        """, (
            player_id, surface,
            round(p_serve, 4), round(q_return, 4),
            data["n_matches"],
            data["total_serve_won"], data["total_serve_pts"],
            round(confidence, 4),
        ))
        inserted += 1

    db.conn.commit()
    return inserted, surface_avg


def main():
    print("=" * 60)
    print("  JBE TopSpin — Compute Serve/Return Parameters")
    print("=" * 60)

    db = TennisDatabase(DB_PATH)

    # Calcola p_serve da tennis_matches
    inserted, surface_avg = compute_p_serve(db)

    total = db.conn.execute("SELECT COUNT(*) FROM serve_return_params").fetchone()[0]
    players = db.conn.execute("SELECT COUNT(DISTINCT player_id) FROM serve_return_params").fetchone()[0]

    print(f"\n  Inseriti: {inserted}")
    print(f"  Totale serve_return_params: {total}")
    print(f"  Giocatori con dati serve: {players}")

    # Top p_serve per superficie
    for surface in SURFACES:
        top = db.conn.execute("""
            SELECT p.name, s.p_serve, s.matches_on_surface
            FROM serve_return_params s
            JOIN players p ON p.id = s.player_id
            WHERE s.surface = ? AND s.matches_on_surface > 10
            ORDER BY s.p_serve DESC LIMIT 5
        """, (surface,)).fetchall()
        if top:
            print(f"\n  Top p_serve {surface} (media: {surface_avg.get(surface, 0):.4f}):")
            for row in top:
                print(f"    {row['name']:25s} {row['p_serve']:.4f} ({row['matches_on_surface']} match)")

    # Bottom p_serve
    for surface in SURFACES:
        bot = db.conn.execute("""
            SELECT p.name, s.p_serve, s.matches_on_surface
            FROM serve_return_params s
            JOIN players p ON p.id = s.player_id
            WHERE s.surface = ? AND s.matches_on_surface > 10
            ORDER BY s.p_serve ASC LIMIT 3
        """, (surface,)).fetchall()
        if bot:
            print(f"\n  Bottom p_serve {surface}:")
            for row in bot:
                print(f"    {row['name']:25s} {row['p_serve']:.4f} ({row['matches_on_surface']} match)")

    db.close()
    print(f"\n[OK] Serve/Return params computed!")


if __name__ == "__main__":
    main()
