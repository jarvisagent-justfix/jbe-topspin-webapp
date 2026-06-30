#!/usr/bin/env python3
"""
JBE TopSpin — Backtest finale 2026 con QUOTE REALI Bet365/Pinnacle
===================================================================
ELO warm-up 2019-2025 + XGBoost 2023-2025 + Value detection con Kelly 12.5%.
Budget 200 EUR.
"""
import sys, os, json, math, random
import numpy as np
from datetime import date, datetime

sys.path.insert(0, "/opt/data/jbe-tennis/src")
from database import TennisDatabase
from config import DB_PATH, MODEL_DIR, KELLY_FRACTION, MAX_STAKE_PCT, MIN_CONFIDENCE, MIN_EDGE
from engine.elo_tennis import SurfaceELOEngine
from engine.xgboost_tennis import FeatureExtractor, TopSpinEngine

import xgboost as xgb

db = TennisDatabase(DB_PATH)

# ============================================================
# FASE 1: ELO Warm-up 2019-2025
# ============================================================
print("=" * 70)
print("FASE 1: ELO Warm-up 2019-2025")
print("=" * 70)

elo_engine = SurfaceELOEngine(db)

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
    elo_engine.record_match(
        m["winner_id"], m["loser_id"], m["surface"],
        md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
    )

print(f"Warm-up OK. {len(elo_engine.ratings)} players.")

# ============================================================
# FASE 2: Carica XGBoost e Backtest 2026
# ============================================================
print("\n" + "=" * 70)
print("FASE 2: Backtest 2026 con Quote Reali")
print("=" * 70)

# Load XGBoost model
winner_model = xgb.XGBClassifier()
model_path = os.path.join(MODEL_DIR, "topspin_winner.json")
if os.path.exists(model_path):
    winner_model.load_model(model_path)
    print("XGBoost model loaded.")
else:
    print("[ERROR] Model not found. Run training first.")
    sys.exit(1)

feature_extractor = FeatureExtractor(db, elo_engine)

# Get 2026 matches with Bet365 odds (most coverage)
test_matches = db.conn.execute("""
    SELECT DISTINCT m.*, w.name as wname, l.name as lname,
           o.odds_winner, o.odds_loser, o.bookmaker
    FROM tennis_matches m
    JOIN players w ON w.id=m.winner_id
    JOIN players l ON l.id=m.loser_id
    JOIN tennis_odds o ON o.match_id = m.id
    WHERE m.match_date >= '2026-01-01' AND m.match_date < '2027-01-01'
      AND m.surface IS NOT NULL AND m.w_sets > 0
      AND o.bookmaker = 'Bet365'
      AND o.odds_winner > 0
    ORDER BY m.match_date, m.id
""").fetchall()

print(f"Test matches with Bet365 odds: {len(test_matches)}")

# Stats
results = []
correct_elo = 0
correct_ensemble = 0
total = 0

by_surface = {}
by_conf = {}
by_month = {}

# Budget simulation
BANKROLL = 200.0
INITIAL_BR = 200.0
bankroll = BANKROLL
bets_placed = 0
bets_won = 0
total_stake = 0.0
total_profit = 0.0
peak = 200.0
max_dd = 0.0
bet_log = []

for i, m in enumerate(test_matches):
    md = date.fromisoformat(m["match_date"])
    winner_id, loser_id = m["winner_id"], m["loser_id"]
    
    # Random flip per bilanciare (player1 = winner o loser)
    flip = random.random() < 0.5
    if flip:
        p1_id, p2_id = winner_id, loser_id
        p1_name, p2_name = m["wname"], m["lname"]
        p1_rank, p2_rank = m["winner_rank"], m["loser_rank"]
        actual_p1_win = True
        # Odds: player1 = winner -> odds_loser
        odds_p1 = m["odds_loser"]
        odds_p2 = m["odds_winner"]
    else:
        p1_id, p2_id = loser_id, winner_id
        p1_name, p2_name = m["lname"], m["wname"]
        p1_rank, p2_rank = m["loser_rank"], m["winner_rank"]
        actual_p1_win = False
        # Odds: player1 = loser -> odds_loser is for actual winner
        odds_p1 = m["odds_winner"]
        odds_p2 = m["odds_loser"]
    
    if not odds_p1 or odds_p1 <= 1.01:
        continue
    
    try:
        # ELO prediction
        elo_pred = elo_engine.predict_winner(p1_id, p2_id, m["surface"], m["best_of"] == 5)
        elo_prob_p1 = elo_pred["prob_player1"]
        elo_correct = (elo_prob_p1 >= 0.5) == actual_p1_win
        if elo_correct: correct_elo += 1
        
        # XGBoost prediction
        feats = feature_extractor.extract(
            m["id"], md, p1_id, p2_id,
            m["surface"], m["best_of"], m["round"], m["tour_level"]
        )
        feats["rank_p1"] = p1_rank or 0
        feats["rank_p2"] = p2_rank or 0
        feats["rank_diff"] = (p2_rank or 0) - (p1_rank or 0)
        feats["rank_pts_diff"] = 0
        
        X_test = np.array([list(feats.values())])
        xgb_prob_p1 = winner_model.predict_proba(X_test)[0][1]
        
        # Ensemble blend
        blend_prob = 0.3 * elo_prob_p1 + 0.7 * xgb_prob_p1
        ensemble_correct = (blend_prob >= 0.5) == actual_p1_win
        if ensemble_correct: correct_ensemble += 1
        
        # Record ELO
        elo_engine.record_match(
            winner_id, loser_id, m["surface"],
            md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
        )
        
    except Exception as e:
        continue
    
    total += 1
    confidence = max(blend_prob, 1 - blend_prob)
    model_prob = blend_prob if blend_prob >= 0.5 else (1 - blend_prob)
    
    # Stats
    surf = m["surface"]
    by_surface.setdefault(surf, {"t": 0, "e": 0, "x": 0})
    by_surface[surf]["t"] += 1
    by_surface[surf]["e"] += 1 if elo_correct else 0
    by_surface[surf]["x"] += 1 if ensemble_correct else 0
    
    conf_key = f"{int(confidence*10)*10}-{min(int(confidence*10)*10+9, 99)}%"
    by_conf.setdefault(conf_key, {"t": 0, "c": 0})
    by_conf[conf_key]["t"] += 1
    by_conf[conf_key]["c"] += 1 if ensemble_correct else 0
    
    month = m["match_date"][:7]
    by_month.setdefault(month, {"t": 0, "c": 0})
    by_month[month]["t"] += 1
    by_month[month]["c"] += 1 if ensemble_correct else 0
    
    # ---- VALUE DETECTION con quote reali ----
    # Edge = model_prob - 1/market_odds
    if confidence >= 0.50:
        implied_prob = 1.0 / odds_p1
        edge = model_prob - implied_prob
        
        predicted = p1_name if blend_prob >= 0.5 else p2_name
        actual_winner = p1_name if actual_p1_win else p2_name
        won = predicted == actual_winner
        
        if edge > MIN_EDGE:
            # Kelly 12.5%
            stake = bankroll * KELLY_FRACTION * edge / (odds_p1 - 1)
            stake = min(stake, bankroll * MAX_STAKE_PCT)
            
            if stake >= 0.5:
                profit = stake * (odds_p1 - 1) if won else -stake
                bankroll += profit
                bets_placed += 1
                if won: bets_won += 1
                total_stake += stake
                total_profit += profit
                
                if bankroll > peak: peak = bankroll
                dd = (peak - bankroll) / peak * 100
                if dd > max_dd: max_dd = dd
                
                bet_log.append({
                    "date": m["match_date"],
                    "p1": p1_name, "p2": p2_name,
                    "surface": m["surface"],
                    "pred": predicted, "actual": actual_winner,
                    "won": won,
                    "conf": round(confidence, 3),
                    "edge": round(edge, 3),
                    "odds": round(odds_p1, 2),
                    "stake": round(stake, 2),
                    "profit": round(profit, 2),
                    "br": round(bankroll, 2),
                })
    
    if (i + 1) % 200 == 0:
        print(f"  {i+1}/{len(test_matches)} "
              f"[ELO: {correct_elo/max(total,1)*100:.1f}% "
              f"Ensemble: {correct_ensemble/max(total,1)*100:.1f}% "
              f"Bets: {bets_placed}/{bets_placed} BR: {bankroll:.0f}EUR]")

# ============================================================
# REPORT
# ============================================================
print("\n" + "=" * 70)
print("JBE TopSpin — Backtest ATP 2026 con Quote Reali (Bet365)")
print("=" * 70)

print(f"\n--- ACCURACY ---")
print(f"  Match testati:      {total}")
print(f"  ELO only:           {correct_elo/max(total,1)*100:.1f}% ({correct_elo}/{total})")
print(f"  Ensemble (XGB+ELO): {correct_ensemble/max(total,1)*100:.1f}% ({correct_ensemble}/{total})")

print(f"\n--- PER SUPERFICIE (Ensemble) ---")
for s in ["Hard", "Clay", "Grass", "Carpet"]:
    if s in by_surface:
        d = by_surface[s]
        print(f"  {s:8s}: {d['t']:4d} match | ELO {d['e']/d['t']*100:.1f}% | Ensemble {d['x']/d['t']*100:.1f}%")

print(f"\n--- PER FASCIA CONFIDENZA ---")
for k in sorted(by_conf.keys()):
    d = by_conf[k]
    print(f"  {k:>10s}: {d['t']:4d} match | {d['c']/d['t']*100:.1f}%")

print(f"\n" + "=" * 70)
print(f"SIMULAZIONE BUDGET 200 EUR — Kelly {KELLY_FRACTION*100:.1f}%")
print(f"Filtro: edge > {MIN_EDGE*100:.0f}% | max stake {MAX_STAKE_PCT*100:.0f}% del bankroll")
print(f"Quote reali: Bet365")
print("=" * 70)

print(f"\nBankroll iniziale:    {INITIAL_BR:.2f} EUR")
print(f"Bankroll finale:      {bankroll:.2f} EUR")
roi_total = (bankroll - INITIAL_BR) / INITIAL_BR * 100
print(f"ROI totale:           {roi_total:+.1f}%")
print(f"Profit netto:         {bankroll - INITIAL_BR:+.2f} EUR")
print(f"")
print(f"Scommesse piazzate:   {bets_placed}")
print(f"Vinte:                {bets_won} ({bets_won/max(bets_placed,1)*100:.1f}%)")
print(f"Stake totale:         {total_stake:.2f} EUR")
roi_stake = total_profit / total_stake * 100 if total_stake > 0 else 0
print(f"ROI su stake:         {roi_stake:+.1f}%")
print(f"Profitto medio/bet:   {total_profit/max(bets_placed,1):+.2f} EUR")
print(f"Max Drawdown:         {max_dd:.1f}%")
print(f"Bets/settimana:       {bets_placed/25:.1f}")
print(f"Edge medio:           {sum(b['edge'] for b in bet_log)/max(len(bet_log),1)*100:.1f}%" if bet_log else "")
print(f"Quota media:          {sum(b['odds'] for b in bet_log)/max(len(bet_log),1):.2f}" if bet_log else "")

if bet_log:
    print(f"\n--- TOP 5 BET MIGLIORI ---")
    bet_log.sort(key=lambda x: x["profit"], reverse=True)
    for b in bet_log[:5]:
        w = "V" if b["won"] else "P"
        print(f"  {b['date']} | {b['p1']}-{b['p2']} ({b['surface']}) | "
              f"{w} {b['pred']} @ {b['odds']} | "
              f"conf={b['conf']:.0%} edge={b['edge']:.1%} | "
              f"{b['stake']:.1f}EUR -> {b['profit']:+.1f}EUR")
    
    print(f"\n--- TOP 5 BET PEGGIORI ---")
    for b in bet_log[-5:]:
        w = "V" if b["won"] else "P"
        print(f"  {b['date']} | {b['p1']}-{b['p2']} ({b['surface']}) | "
              f"{w} {b['pred']} @ {b['odds']} | "
              f"conf={b['conf']:.0%} edge={b['edge']:.1%} | "
              f"{b['stake']:.1f}EUR -> {b['profit']:+.1f}EUR")

db.close()
print("\nBacktest completato.")
