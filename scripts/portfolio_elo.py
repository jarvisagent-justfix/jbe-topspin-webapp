#!/usr/bin/env python3
"""
JBE TopSpin — Portfolio Storico ELO-only (Backtest Onesto)
===========================================================
Backtest solo ELO (no XGBoost) sui 1.129 match 2026 con quote Bet365,
salva le value bet nel paper_portfolio con stato won/lost.

Strategia: MW, max 1 per match, edge min 5%, odds < 2.0, Kelly 12.5%.
Accuracy ELO realistica ~55% → winrate e P&L credibili.
"""
import sys, os, json
import numpy as np
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import DB_PATH
from engine.elo_tennis import SurfaceELOEngine
from engine.value_detector import KellyCalculator

db = TennisDatabase(DB_PATH)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# FASE 1: ELO Warm-up 2019-2025
# ============================================================
print("=" * 70)
print("FASE 1: ELO Warm-up 2019-2025")
print("=" * 70)

elo = SurfaceELOEngine(db)

warmup = db.conn.execute("""
    SELECT id, winner_id, loser_id, surface, match_date, best_of, w_games, l_games
    FROM tennis_matches
    WHERE match_date >= '2019-01-01' AND match_date < '2026-01-01'
      AND w_sets > 0 AND surface IS NOT NULL
    ORDER BY match_date, id
""").fetchall()

print(f"Warm-up: {len(warmup)} matches")
for i, m in enumerate(warmup):
    if i % 5000 == 0:
        print(f"  {i}/{len(warmup)}")
    md = date.fromisoformat(m["match_date"])
    elo.record_match(m["winner_id"], m["loser_id"], m["surface"],
                     md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0)
print(f"Warm-up OK. {len(elo.ratings)} players.")

# ============================================================
# FASE 2: Backtest 2026 + Portfolio
# ============================================================
print("\n" + "=" * 70)
print("FASE 2: Backtest 2026 + Portfolio (ELO only)")
print("=" * 70)

# 2026 matches with odds
test_matches = db.conn.execute("""
    SELECT DISTINCT m.*, w.name as wname, l.name as lname,
           o.odds_winner, o.odds_loser, o.bookmaker
    FROM tennis_matches m
    JOIN players w ON w.id=m.winner_id
    JOIN players l ON l.id=m.loser_id
    JOIN tennis_odds o ON o.match_id = m.id
    WHERE m.match_date >= '2026-01-01' AND m.match_date < '2027-01-01'
      AND m.surface IS NOT NULL AND m.w_sets > 0
      AND o.bookmaker = 'Bet365' AND o.odds_winner > 0
    ORDER BY m.match_date, m.id
""").fetchall()

print(f"2026 matches with odds: {len(test_matches)}")

# Pulisci portfolio storico (tranne bet live da oggi)
today_str = date.today().isoformat()
db.conn.execute("DELETE FROM paper_portfolio WHERE match_date < ?", (today_str,))
db.conn.execute("DELETE FROM value_candidates")
db.conn.commit()
print(f"Portfolio pulito (tenute bet live da {today_str})")

kc = KellyCalculator(initial_bankroll=200.0)
rng = np.random.RandomState(42)

bets_placed = 0
bets_won = 0
total_profit = 0.0

for i, m in enumerate(test_matches):
    if (i + 1) % 100 == 0:
        wr = bets_won / max(bets_placed, 1) * 100
        print(f"  {i+1}/{len(test_matches)} [Bets: {bets_placed} Won: {bets_won} ({wr:.0f}%) P&L: {total_profit:+.2f}€]")

    md = date.fromisoformat(m["match_date"])
    odds_winner = m["odds_winner"]
    odds_loser = m["odds_loser"]

    if not odds_winner or odds_winner <= 1.01:
        continue

    # Random flip: p1 a volte winner, a volte loser
    flip = rng.random() < 0.5
    if flip:
        p1_id, p2_id = m["winner_id"], m["loser_id"]
        p1_name, p2_name = m["wname"], m["lname"]
        actual_p1_win = True
        odds_p1 = odds_winner
    else:
        p1_id, p2_id = m["loser_id"], m["winner_id"]
        p1_name, p2_name = m["lname"], m["wname"]
        actual_p1_win = False
        odds_p1 = odds_loser

    try:
        pred = elo.predict_winner(p1_id, p2_id, m["surface"], m["best_of"] == 5)
        prob_p1 = pred["prob_player1"]

        # Aggiorna ELO col risultato reale
        elo.record_match(m["winner_id"], m["loser_id"], m["surface"],
                         md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0)
    except:
        continue

    # Strategia MW: odds < 2.0, edge >= 5%, edge <= 25%
    implied = 1.0 / odds_p1
    edge = prob_p1 - implied
    if prob_p1 >= 0.50 and edge >= 0.05 and edge <= 0.25 and odds_p1 < 2.0:
        stake = kc.calculate_stake(edge, odds_p1)
        if stake >= 0.5:
            won = actual_p1_win
            profit = stake * (odds_p1 - 1) if won else -stake
            kc.bankroll += profit
            bets_placed += 1
            if won:
                bets_won += 1
            total_profit += profit

            try:
                db.conn.execute("""
                    INSERT INTO paper_portfolio
                        (match_id, match_date, tournament, surface,
                         player1, player2, selection, market,
                         odds, model_prob, edge, stake,
                         bankroll_before, bankroll_after, status, result,
                         bookmaker, source, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'Bet365', 'backtest_elo', 'MEDIUM')
                """, (
                    m["id"], m["match_date"],
                    m["tournament"] or "", m["surface"] or "",
                    p1_name, p2_name, p1_name,
                    "match_winner",
                    float(odds_p1), float(prob_p1), float(edge),
                    float(stake),
                    float(kc.bankroll - profit),
                    float(kc.bankroll),
                    'won' if won else 'lost',
                    float(profit),
                ))
            except:
                pass

db.conn.commit()

# Report
wr = bets_won / max(bets_placed, 1) * 100
roi = total_profit / 200.0 * 100
print("\n" + "=" * 70)
print("BACKTEST 2026 COMPLETO — Portfolio Storico (ELO only)")
print("=" * 70)
print(f"  Match analizzati:   {len(test_matches)}")
print(f"  Value bets trovate: {bets_placed}")
print(f"  Vinte:  {bets_won} ({wr:.1f}%)")
print(f"  Perse:  {bets_placed - bets_won} ({100-wr:.1f}%)")
print(f"  P&L:    {total_profit:+.2f}€")
print(f"  ROI:    {roi:+.1f}%")
print(f"  Bankroll: {kc.bankroll:.2f}€")

# Kelly state
with open(os.path.join(BASE, "data", "kelly_state.json"), "w") as f:
    json.dump({"bankroll": kc.bankroll, "peak_bankroll": kc.peak_bankroll,
               "consecutive_losses": kc.consecutive_losses, "daily_exposure": kc.daily_exposure}, f)

db.close()
print("Done.")
