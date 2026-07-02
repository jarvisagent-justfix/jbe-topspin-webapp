#!/usr/bin/env python3
"""Backtest Over/Under: 1 bet per match, solo sul migliore edge."""
import sqlite3, sys, os
import numpy as np
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from database import TennisDatabase
from engine.xgboost_tennis import TopSpinEngine

DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'tennis.db')

tdb = TennisDatabase(DB)
engine = TopSpinEngine(tdb)
raw = sqlite3.connect(DB)
raw.row_factory = sqlite3.Row

cur = raw.execute("""
    SELECT tm.id, tm.match_date, tm.surface, tm.tournament, tm.tour_level,
           tm.round, tm.best_of, tm.winner_id, tm.loser_id,
           p1.name as p1_name, p2.name as p2_name,
           tm.winner_rank, tm.loser_rank,
           tm.winner_rank_points, tm.loser_rank_points,
           tm.w_games, tm.l_games,
           to2.odds_winner, to2.odds_loser
    FROM tennis_matches tm
    JOIN tennis_odds to2 ON tm.id = to2.match_id
    JOIN players p1 ON tm.winner_id = p1.id
    JOIN players p2 ON tm.loser_id = p2.id
    WHERE tm.match_date >= '2026-01-01' AND tm.match_date <= '2026-06-30'
      AND to2.bookmaker = 'Bet365'
      AND to2.odds_winner IS NOT NULL
      AND tm.tour_level IN ('A', 'M', 'G', 'F')
      AND tm.w_games IS NOT NULL AND tm.l_games IS NOT NULL
    ORDER BY tm.match_date
""")
matches = [dict(r) for r in cur.fetchall()]
raw.close()
print(f"Match con risultato: {len(matches)}")

# Predict per ogni match
results = []
for i, m in enumerate(matches):
    if (i+1) % 300 == 0:
        print(f"  [{i+1}/{len(matches)}]...", file=sys.stderr)
    try:
        md = date.fromisoformat(m['match_date'])
        surface = m['surface'] or 'Hard'
        pred = engine.predict(
            match_id=m['id'],
            player1_id=m['winner_id'],
            player2_id=m['loser_id'],
            surface=surface, match_date=md,
            best_of=m['best_of'] or 3,
            round_val=m['round'], tour_level=m['tour_level'],
            rank_p1=m['winner_rank'], rank_p2=m['loser_rank'],
            rank_pts_p1=m['winner_rank_points'], rank_pts_p2=m['loser_rank_points'],
            odds_p1=m['odds_winner'], odds_p2=m['odds_loser'],
        )
        real_total = (m['w_games'] or 0) + (m['l_games'] or 0)
        predicted_total = pred.get('predicted_games')
        if predicted_total and predicted_total > 0:
            results.append({
                'id': m['id'], 'date': m['match_date'],
                'surface': surface, 'level': m['tour_level'],
                'best_of': m['best_of'] or 3,
                'real_games': real_total,
                'pred_games': predicted_total,
                'error': abs(predicted_total - real_total),
            })
    except Exception as e:
        pass

print(f"\nPredizioni valide: {len(results)}")

# Media per categoria (superficie + best_of)
from collections import defaultdict
cats = defaultdict(list)
for r in results:
    key = f"{r['surface']}/{r['best_of']}set"
    cats[key].append(r['real_games'])

medie = {k: np.mean(v) for k, v in cats.items()}

# SIMULAZIONE: 1 bet per match, scegli OVER o UNDER in base a dove il modello ha più edge
# Usiamo la media categoria come "linea Bet365"
print(f"\n{'='*70}")
print(f"SIMULAZIONE OVER/UNDER — 1 bet per match, solo il migliore")
print(f"{'='*70}")
print(f"Linea = media categoria (superficie + best-of)")
print(f"Bet OVER se predicted > media + soglia, UNDER se predicted < media - soglia")
print(f"Stake: 10€ flat | Odds: 1.90 (Bet365 O/U)")

for soglia in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]:
    bets = []
    for r in results:
        key = f"{r['surface']}/{r['best_of']}set"
        media = medie[key]
        diff = r['pred_games'] - media
        
        if diff > soglia:
            if r['real_games'] > media:
                bets.append(1)  # vinto
            elif r['real_games'] < media:
                bets.append(0)  # perso
        elif diff < -soglia:
            if r['real_games'] < media:
                bets.append(1)
            elif r['real_games'] > media:
                bets.append(0)
        # push (real == media) → skip
    
    n = len(bets)
    if n < 5:
        continue
    wins = sum(bets)
    wr = wins / n * 100
    profit = wins * 10 * 1.90 - n * 10
    roi = profit / (n * 10) * 100
    
    # Sharpe
    returns = [(1.90 - 1) if b else -1 for b in bets]
    avg_ret = np.mean(returns)
    std_ret = np.std(returns) if len(returns) > 1 else 0.001
    sharpe = avg_ret / std_ret * (252 ** 0.5) if std_ret > 0 else 0
    
    # Quante OVER vs UNDER
    overs = 0
    for r in results:
        key = f"{r['surface']}/{r['best_of']}set"
        media = medie[key]
        diff = r['pred_games'] - media
        if diff > soglia:
            overs += 1
    unders = n - overs
    
    print(f"  soglia {soglia:>4.1f} | {n:>4} bet | {wins:>3}W {n-wins:>3}L | WR {wr:>5.1f}% | ROI {roi:>+6.2f}% | Profit {profit:>+7.2f}€ | Sharpe {sharpe:>5.2f} | {overs}O/{unders}U")

# Trova soglia ottimale
print(f"\nRicerca soglia ottimale (step 0.5)...")
best_sharpe = -999
best_data = None
for soglia in [x/2 for x in range(1, 20)]:
    bets = []
    for r in results:
        key = f"{r['surface']}/{r['best_of']}set"
        media = medie[key]
        diff = r['pred_games'] - media
        if diff > soglia or diff < -soglia:
            if r['real_games'] == media:
                continue
            won = (diff > 0 and r['real_games'] > media) or (diff < 0 and r['real_games'] < media)
            bets.append(won)
    
    n = len(bets)
    if n < 10:
        continue
    wins = sum(bets)
    profit = wins * 10 * 1.90 - n * 10
    roi = profit / (n * 10) * 100
    returns = [(1.90 - 1) if b else -1 for b in bets]
    sharpe = np.mean(returns) / (np.std(returns) or 0.001) * (252 ** 0.5) if len(returns) > 1 else 0
    
    if sharpe > best_sharpe:
        best_sharpe = sharpe
        best_data = (soglia, n, wins, wr := wins/n*100, roi, profit, sharpe)

if best_data:
    s, n, wins, wr, roi, profit, sharpe = best_data
    print(f"\n{'='*70}")
    print(f"🏆 MIGLIORE CONFIGURAZIONE (per Sharpe)")
    print(f"{'='*70}")
    print(f"  Soglia:         {s:.1f} games di differenza dalla media")
    print(f"  Bet totali:     {n}")
    print(f"  Vinte/Perse:    {wins}W / {n-wins}L")
    print(f"  Win Rate:       {wr:.1f}%")
    print(f"  ROI:            {roi:+.2f}%")
    print(f"  Profit:         {profit:+.2f}€")
    print(f"  Sharpe:         {sharpe:.2f}")
    
    # Breakdown per superficie
    print(f"\n  Breakdown per superficie:")
    for surf in ['Hard', 'Clay', 'Grass']:
        bets_s = []
        for r in results:
            if r['surface'] != surf:
                continue
            key = f"{r['surface']}/{r['best_of']}set"
            media = medie[key]
            diff = r['pred_games'] - media
            if diff > s or diff < -s:
                if r['real_games'] == media:
                    continue
                won = (diff > 0 and r['real_games'] > media) or (diff < 0 and r['real_games'] < media)
                bets_s.append(won)
        if bets_s:
            ws = sum(bets_s)
            ns = len(bets_s)
            print(f"    {surf:8}: {ns:3} bet | {ws}W {ns-ws}L | WR {ws/ns*100:.0f}%")
    
    # Per best-of
    print(f"\n  Breakdown per formato:")
    for bo in [3, 5]:
        bets_b = []
        for r in results:
            if r['best_of'] != bo:
                continue
            key = f"{r['surface']}/{r['best_of']}set"
            media = medie[key]
            diff = r['pred_games'] - media
            if diff > s or diff < -s:
                if r['real_games'] == media:
                    continue
                won = (diff > 0 and r['real_games'] > media) or (diff < 0 and r['real_games'] < media)
                bets_b.append(won)
        if bets_b:
            ws = sum(bets_b)
            ns = len(bets_b)
            print(f"    Best-of-{bo}: {ns:3} bet | {ws}W {ns-ws}L | WR {ws/ns*100:.0f}%")
    
    # Esempi di bet
    print(f"\n  Esempi di bet alla soglia {s:.1f}:")
    shown = 0
    for r in results:
        key = f"{r['surface']}/{r['best_of']}set"
        media = medie[key]
        diff = r['pred_games'] - media
        if (diff > s or diff < -s) and r['real_games'] != media and shown < 8:
            won = (diff > 0 and r['real_games'] > media) or (diff < 0 and r['real_games'] < media)
            direction = 'OVER' if diff > 0 else 'UNDER'
            print(f"    {'✅' if won else '❌'} {r['date']} | {direction:5} | pred {r['pred_games']:.0f} | media {media:.0f} | reale {r['real_games']} | diff {diff:+.1f}")

# CONFRONTO: miglior configurazione vs reale portfolio over/under
print(f"\n{'='*70}")
print(f"CONFRONTO CON PORTAFOGLIO REALE")
print(f"{'='*70}")
cur = sqlite3.connect(DB)
cur.row_factory = sqlite3.Row
cur = cur.execute("""
    SELECT status, COUNT(*) as cnt, SUM(stake) as tot_stake
    FROM paper_portfolio
    WHERE market = 'over_under' AND status IN ('won', 'lost', 'push')
    GROUP BY status
""")
for r in cur.fetchall():
    print(f"  {r['status']}: {r['cnt']} bet, {r['tot_stake']:.0f}€ stake")

tdb.conn.close()
print(f"\nFatto.")
