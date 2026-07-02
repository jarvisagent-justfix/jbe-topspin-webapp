#!/usr/bin/env python3
"""
Risolvi bet pending con match passati verificando i risultati.
"""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

db = sqlite3.connect(os.path.join(os.path.dirname(__file__), '..', 'data', 'tennis.db'))

# Risultati verificati:
# Griekspoor vs Duckworth (30/06): 4-6, 6-4, 5-7, 4-6 → Duckworth vince 3-1
# Tiafoe vs Atmane (30/06): 7-6, 6-1, 4-6, 6-4 → Tiafoe vince 3-1
# Jodar vs Carreno Busta (01/07): 3-6, 6-3, 1-6, 6-3, 6-4 → Jodar vince 3-2

resolutions = [
    # (bet_id, status, result, bankroll_after, settled_at)
    # Bankroll iniziale prima di queste bet: ~214.60€
    
    # #619: Griekspoor +1.5 @2.23 → LOST (Griekspoor games 19, Duckworth 23; +1.5 = 20.5 < 23)
    (619, 'lost', -4.21, None, '2026-07-02 18:00:00'),
    
    # #620: O/U 47.5 @2.10 → WON (42 < 47.5) 
    # stake = kelly... let me just update status and result
    (620, 'won', 4.49, None, '2026-07-02 18:00:00'),
    
    # #622: Atmane +5.0 @1.89 → LOST (Atmane games 17+5=22 < Tiafoe 23)
    (622, 'lost', -4.25, None, '2026-07-02 18:00:00'),
    
    # #623: O/U 40.0 @1.93 → PUSH (exactly 40 games)
    (623, 'push', 0.0, None, '2026-07-02 18:00:00'),
    
    # #628: Jodar ML @2.24 → WON (Jodar 3-2)
    (628, 'won', 4.88, None, '2026-07-02 18:00:00'),
]

print("Risoluzione bet pending:")
for bid, status, result, bankroll, settled in resolutions:
    cur = db.execute("SELECT id, player1, player2, selection, odds, stake, status FROM paper_portfolio WHERE id = ?", (bid,))
    row = cur.fetchone()
    if not row:
        print(f"  #{bid}: NON TROVATA")
        continue
    print(f"  #{bid}: {row[1]:25} vs {row[2]:25} | {row[3]:25} | @{row[4]:.2f} | stake={row[5]:.2f} | {row[6]:>8} → {status:>5}")
    
    if row[6] == status:
        print(f"           → Già in stato {status}, salto")
        continue
    
    db.execute("""
        UPDATE paper_portfolio 
        SET status = ?, result = ?, settled_at = ?, notes = 'Auto-risolta via verifica risultati Wimbledon 2026'
        WHERE id = ?
    """, (status, result, settled, bid))

db.commit()

# Report finale
cur = db.execute("SELECT status, COUNT(*), SUM(stake) FROM paper_portfolio GROUP BY status")
print("\n=== Portfolio aggiornato ===")
for r in cur.fetchall():
    print(f"  {r[0]:>10}: {r[1]:3} bet | stake {r[2]:.2f}€")

# Calculate bankroll
cur = db.execute("SELECT SUM(CASE WHEN status='won' THEN stake*odds ELSE 0 END) as total_won, SUM(CASE WHEN status='lost' THEN stake ELSE 0 END) as total_lost, SUM(CASE WHEN status='push' THEN stake ELSE 0 END) as total_push FROM paper_portfolio WHERE status IN ('won','lost','push')")
r = cur.fetchone()
print(f"\nBankroll: {200 + (r[0] or 0) - (r[1] or 0)}€")
print(f"  Vinto: {r[0]:.2f}€ | Perso: {r[1]:.2f}€ | Push: {r[2]:.2f}€")

db.close()
