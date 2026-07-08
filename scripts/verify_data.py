"""
JBE TopSpin — Verifica Import

Test rapido per verificare che i dati siano stati importati correttamente
e che i modelli base funzionino.
"""
import sys
import os
from datetime import datetime, date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from engine.elo_tennis import SurfaceELOEngine


def verify_import():
    db = TennisDatabase()
    
    # Stats base
    cur = db.conn.execute("SELECT COUNT(*) FROM tennis_matches")
    total = cur.fetchone()[0]
    
    cur = db.conn.execute("SELECT COUNT(*) FROM tennis_matches WHERE w_svpt IS NOT NULL")
    with_stats = cur.fetchone()[0]
    
    cur = db.conn.execute("SELECT COUNT(*) FROM players")
    players = cur.fetchone()[0]
    
    print(f"Match totali: {total}")
    print(f"Match con statistiche serve: {with_stats}")
    print(f"Giocatori: {players}")
    
    # Top giocatori per match
    cur = db.conn.execute("""
        SELECT p.name, COUNT(*) as cnt 
        FROM tennis_matches m 
        JOIN players p ON p.id = m.winner_id OR p.id = m.loser_id
        GROUP BY p.id ORDER BY cnt DESC LIMIT 10
    """)
    print("\nTop 10 giocatori per match giocati:")
    for r in cur.fetchall():
        print(f"  {r['name']}: {r['cnt']}")

    print("\n--- Test ELO Engine ---")
    elo = SurfaceELOEngine(db)
    
    # Prendi l'ultimo match come test
    match = db.conn.execute("""
        SELECT m.*, w.name as wname, l.name as lname 
        FROM tennis_matches m
        JOIN players w ON w.id=m.winner_id
        JOIN players l ON l.id=m.loser_id
        WHERE m.surface IS NOT NULL
        ORDER BY m.match_date DESC LIMIT 1
    """).fetchone()
    
    if match:
        print(f"Test match: {match['wname']} vs {match['lname']} ({match['surface']})")
        elo.record_match(
            match['winner_id'], match['loser_id'],
            match['surface'], date.fromisoformat(match['match_date']),
            match['best_of'] == 5,
            match['w_games'], match['l_games']
        )
        
        pred = elo.predict_winner(match['winner_id'], match['loser_id'], match['surface'])
        print(f"Predizione: {match['wname']} {pred['prob_player1']:.1%} vs {match['lname']} {pred['prob_player2']:.1%}")
        print(f"ELO diff: {pred['elo_diff']:.1f}")
        print(f"Blended diff: {pred['blended_diff']:.1f}")
    
    # Backtest rapido: accuracy ELO su 1000 match recenti
    print("\n--- Backtest rapido ELO (ultimi 1000 match) ---")
    matches = db.conn.execute("""
        SELECT m.*, w.name as wname, l.name as lname 
        FROM tennis_matches m
        JOIN players w ON w.id=m.winner_id
        JOIN players l ON l.id=m.loser_id
        WHERE m.surface IS NOT NULL AND m.w_sets > 0
        ORDER BY m.match_date ASC LIMIT 2000
    """).fetchall()
    
    # Second half for testing
    test_matches = matches[1000:]
    train_matches = matches[:1000]
    
    print(f"Train: {len(train_matches)}, Test: {len(test_matches)}")
    
    # Train
    for m in train_matches:
        try:
            elo.record_match(
                m['winner_id'], m['loser_id'],
                m['surface'], date.fromisoformat(m['match_date']),
                m['best_of'] == 5, m['w_games'], m['l_games']
            )
        except:
            pass
    
    # Test
    correct = 0
    total_test = 0
    for m in test_matches:
        try:
            pred = elo.predict_winner(m['winner_id'], m['loser_id'], m['surface'])
            predicted_winner = m['winner_id'] if pred['prob_player1'] > 0.5 else m['loser_id']
            if predicted_winner == m['winner_id']:
                correct += 1
            total_test += 1
        except:
            pass
    
    print(f"Accuracy ELO: {correct}/{total_test} = {correct/total_test*100:.1f}%")
    
    db.close()


if __name__ == "__main__":
    verify_import()
