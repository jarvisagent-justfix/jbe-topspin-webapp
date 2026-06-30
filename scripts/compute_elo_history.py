#!/usr/bin/env python3
"""
JBE TopSpin — ELO History Compute & Persist
============================================
Processa TUTTI i match storici in ordine cronologico,
aggiorna i rating ELO dopo ogni match e li salva nel DB (elo_ratings).

Uso: PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/compute_elo_history.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from datetime import date, timedelta
from database import TennisDatabase
from engine.elo_tennis import SurfaceELOEngine, ELORating
from config import DB_PATH

BATCH_SIZE = 500  # Righe ELO per COMMIT

def compute_all():
    db = TennisDatabase(DB_PATH)
    engine = SurfaceELOEngine(db)

    # Verifica se ci sono gia' rating
    existing = db.conn.execute("SELECT COUNT(*) FROM elo_ratings").fetchone()[0]
    if existing > 0:
        print(f"[INFO] elo_ratings ha gia' {existing} righe. Svuoto e ri-computo...")
        db.conn.execute("DELETE FROM elo_ratings")
        db.conn.commit()

    # Carica tutti i match in ordine cronologico
    matches = db.conn.execute("""
        SELECT m.id, m.winner_id, m.loser_id, m.surface, m.match_date,
               m.best_of, m.w_games, m.l_games,
               m.w_sets, m.l_sets
        FROM tennis_matches m
        WHERE m.w_sets > 0 AND m.surface IS NOT NULL
          AND m.winner_id IS NOT NULL AND m.loser_id IS NOT NULL
        ORDER BY m.match_date, m.id
    """).fetchall()

    print(f"[INFO] {len(matches)} match da processare")

    total = len(matches)
    saved_count = 0
    last_pct = 0
    batch = []

    for i, m in enumerate(matches):
        try:
            match_date = date.fromisoformat(m["match_date"]) if isinstance(m["match_date"], str) else m["match_date"]

            # Pre-match decay
            for pid in (m["winner_id"], m["loser_id"]):
                if pid in engine.ratings:
                    engine.ratings[pid].apply_decay(match_date)

            # Record match (aggiorna rating)
            engine.record_match(
                m["winner_id"], m["loser_id"], m["surface"],
                match_date, m["best_of"] == 5,
                m["w_games"] or 0, m["l_games"] or 0
            )

            # Salva rating per winner e loser DOPO l'aggiornamento
            for pid in (m["winner_id"], m["loser_id"]):
                if pid in engine.ratings:
                    r = engine.ratings[pid]
                    batch.append((
                        pid, m["id"], match_date.isoformat(),
                        r.overall, r.hard, r.clay, r.grass, r.carpet, r.mov,
                        r.matches_played, r.matches_hard, r.matches_clay,
                        r.matches_grass, r.matches_carpet
                    ))

            # Commit a batch
            if len(batch) >= BATCH_SIZE * 2:
                db.conn.executemany("""
                    INSERT INTO elo_ratings
                    (player_id, match_id, rating_date,
                     rating_overall, rating_hard, rating_clay, rating_grass,
                     rating_carpet, rating_mov,
                     matches_played, matches_hard, matches_clay,
                     matches_grass, matches_carpet)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                db.conn.commit()
                saved_count += len(batch)
                batch = []

            # Progress
            pct = (i + 1) * 100 // total
            if pct >= last_pct + 5:
                # Non serve report ogni volta
                players = len(engine.ratings)
                print(f"  {pct}% — {i+1}/{total} match, {saved_count + len(batch)} ratings, {players} player")
                last_pct = pct

        except Exception as e:
            print(f"  [ERRORE] match {m['id']}: {e}")

    # Final batch
    if batch:
        db.conn.executemany("""
            INSERT INTO elo_ratings
            (player_id, match_id, rating_date,
             rating_overall, rating_hard, rating_clay, rating_grass,
             rating_carpet, rating_mov,
             matches_played, matches_hard, matches_clay,
             matches_grass, matches_carpet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)
        db.conn.commit()
        saved_count += len(batch)

    db.commit()

    # Stats finali
    total_ratings = db.conn.execute("SELECT COUNT(*) FROM elo_ratings").fetchone()[0]
    total_players = db.conn.execute("SELECT COUNT(DISTINCT player_id) FROM elo_ratings").fetchone()[0]
    last_date = db.conn.execute("SELECT MAX(rating_date) FROM elo_ratings").fetchone()[0]

    print(f"\n[OK] ELO persistence completata!")
    print(f"     Ratings salvati: {total_ratings}")
    print(f"     Giocatori tracciati: {total_players}")
    print(f"     Ultimo aggiornamento: {last_date}")
    print(f"     Giocatori con rating: {len(engine.ratings)}")

    # Top 10 per overall
    print("\n--- Top 10 Giocatori (rating overall finale) ---")
    top = sorted(engine.ratings.items(), key=lambda x: x[1].overall, reverse=True)[:10]
    for pid, r in top:
        name = db.conn.execute("SELECT name FROM players WHERE id=?", (pid,)).fetchone()
        if name:
            print(f"  {name['name']:20s} overall={r.overall:.0f} hard={r.hard:.0f} clay={r.clay:.0f} grass={r.grass:.0f} ({r.matches_played} match)")

    db.close()

if __name__ == "__main__":
    compute_all()
