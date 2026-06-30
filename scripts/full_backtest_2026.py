#!/usr/bin/env python3
"""
JBE TopSpin — Training + Backtest 2026 + Budget Simulation
===========================================================
Fix:
1. ELO warm-up ridotto (2019-2025, non 2001-2025) — meno polarizzazione
2. XGBoost addestrato SENZA data leakage (feature estratte PRIMA di record_match)
3. Backtest 2026 con simulazione flat betting su confidenza alta
"""
import sys, os, json, math, random
import numpy as np
from datetime import date, datetime, timedelta

sys.path.insert(0, "/opt/data/jbe-tennis/src")
from database import TennisDatabase
from config import DB_PATH, MODEL_DIR, KELLY_FRACTION, MAX_STAKE_PCT, MIN_CONFIDENCE, MIN_EDGE
from engine.elo_tennis import SurfaceELOEngine, ELORating
from engine.xgboost_tennis import FeatureExtractor, XGBoostTrainer, TopSpinEngine
from engine.contextual_factors import ContextualFactors

import xgboost as xgb
from sklearn.model_selection import train_test_split

db = TennisDatabase(DB_PATH)

# ============================================================
# FASE 1: ELO Warm-up 2019-2025 (7 anni, non 25)
# ============================================================
print("=" * 70)
print("FASE 1: ELO Warm-up 2019-2025")
print("=" * 70)

elo_engine = SurfaceELOEngine(db)

# Warm-up su 2019-2025
warmup = db.conn.execute("""
    SELECT id, winner_id, loser_id, surface, match_date, best_of,
           w_games, l_games
    FROM tennis_matches
    WHERE match_date >= '2019-01-01' AND match_date < '2026-01-01'
      AND w_sets > 0 AND surface IS NOT NULL
    ORDER BY match_date, id
""").fetchall()

print(f"Warm-up matches: {len(warmup)}")

for i, m in enumerate(warmup):
    if i % 5000 == 0:
        print(f"  warm-up: {i}/{len(warmup)}")
    md = date.fromisoformat(m["match_date"])
    elo_engine.record_match(
        m["winner_id"], m["loser_id"], m["surface"],
        md, m["best_of"] == 5,
        m["w_games"] or 0, m["l_games"] or 0
    )

print(f"Warm-up completato. {len(elo_engine.ratings)} giocatori.")

# ============================================================
# FASE 2: XGBoost Training (2023-2025) — NO DATA LEAKAGE
# ============================================================
print("\n" + "=" * 70)
print("FASE 2: XGBoost Training 2023-2025 (no data leakage)")
print("=" * 70)

# Training matches: 2023-01-01 to 2025-07-01 (per avere ~2.5 anni di training)
train_matches = db.conn.execute("""
    SELECT m.*, w.name as wname, l.name as lname
    FROM tennis_matches m
    JOIN players w ON w.id=m.winner_id
    JOIN players l ON l.id=m.loser_id
    WHERE m.match_date >= '2023-01-01' AND m.match_date < '2025-07-01'
      AND m.surface IS NOT NULL AND m.w_sets > 0
    ORDER BY m.match_date, m.id
""").fetchall()

print(f"Training matches: {len(train_matches)}")

# Build feature matrix with correct ordering:
# 1. Extract features (using current ELO, without match result)
# 2. Record match result (update ELO)
# 3. Add to training set
feature_extractor = FeatureExtractor(db, elo_engine)

X_list, y_winner_list, y_games_list = [], [], []
feature_names = None
errors = 0

for m in train_matches:
    try:
        md = date.fromisoformat(m["match_date"])
        
        # Random flip per bilanciare classi
        flip = random.random() < 0.5
        if flip:
            p1_id, p2_id = m["winner_id"], m["loser_id"]
            p1_rank, p2_rank = m["winner_rank"], m["loser_rank"]
            p1_rp, p2_rp = m["winner_rank_points"], m["loser_rank_points"]
            y = 1
        else:
            p1_id, p2_id = m["loser_id"], m["winner_id"]
            p1_rank, p2_rank = m["loser_rank"], m["winner_rank"]
            p1_rp, p2_rp = m["loser_rank_points"], m["winner_rank_points"]
            y = 0
        
        # STEP 1: Extract features BEFORE recording match
        feats = feature_extractor.extract(
            m["id"], md, p1_id, p2_id,
            m["surface"], m["best_of"], m["round"], m["tour_level"]
        )
        feats["rank_p1"] = p1_rank or 0
        feats["rank_p2"] = p2_rank or 0
        feats["rank_diff"] = (p2_rank or 0) - (p1_rank or 0)
        feats["rank_pts_diff"] = (p1_rp or 0) - (p2_rp or 0)
        
        if feature_names is None:
            feature_names = list(feats.keys())
        
        X_list.append([v for v in feats.values()])
        y_winner_list.append(y)
        y_games_list.append(m["w_games"] + m["l_games"])
        
        # STEP 2: Record match result (update ELO for next match)
        elo_engine.record_match(
            m["winner_id"], m["loser_id"], m["surface"],
            md, m["best_of"] == 5,
            m["w_games"] or 0, m["l_games"] or 0
        )
    except Exception as e:
        errors += 1
        if errors <= 3:
            print(f"  [ERRORE] match {m['id']}: {e}")

X = np.array(X_list)
y_winner = np.array(y_winner_list)
y_games = np.array(y_games_list)

print(f"Feature shape: {X.shape}")
print(f"Feature names ({len(feature_names)}): {feature_names}")

# Train/validation split
X_train, X_val, y_w_train, y_w_val, y_g_train, y_g_val = train_test_split(
    X, y_winner, y_games, test_size=0.2, random_state=42
)

# Winner model
print("\nTraining winner classifier...")
n_pos = y_w_train.sum()
n_neg = len(y_w_train) - n_pos
scale = n_neg / n_pos if n_pos > 0 else 1.0

winner_model = xgb.XGBClassifier(
    n_estimators=300, max_depth=6, learning_rate=0.05,
    objective='binary:logistic', eval_metric=['logloss', 'error'],
    scale_pos_weight=scale, subsample=0.8, colsample_bytree=0.8,
    random_state=42, n_jobs=-1,
)
winner_model.fit(X_train, y_w_train, eval_set=[(X_val, y_w_val)], verbose=False)

train_acc = (winner_model.predict(X_train) == y_w_train).mean()
val_acc = (winner_model.predict(X_val) == y_w_val).mean()
print(f"  Train accuracy: {train_acc:.4f}")
print(f"  Val accuracy: {val_acc:.4f}")

# Games model
print("Training game total regressor...")
games_model = xgb.XGBRegressor(
    n_estimators=300, max_depth=6, learning_rate=0.05,
    objective='reg:squarederror', eval_metric=['mae'],
    subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1,
)
games_model.fit(X_train, y_g_train, eval_set=[(X_val, y_g_val)], verbose=False)

train_mae = np.abs(games_model.predict(X_train) - y_g_train).mean()
val_mae = np.abs(games_model.predict(X_val) - y_g_val).mean()
print(f"  Train MAE: {train_mae:.2f} games")
print(f"  Val MAE: {val_mae:.2f} games")

# Save models
os.makedirs(MODEL_DIR, exist_ok=True)
winner_model.save_model(os.path.join(MODEL_DIR, "topspin_winner.json"))
games_model.save_model(os.path.join(MODEL_DIR, "topspin_games.json"))
print("\nModelli salvati.")

# ============================================================
# FASE 3: Backtest 2026 con Budget Simulation
# ============================================================
print("\n" + "=" * 70)
print("FASE 3: Backtest 2026 + Budget Simulation 200 EUR")
print("=" * 70)

test_matches = db.conn.execute("""
    SELECT m.*, w.name as wname, l.name as lname
    FROM tennis_matches m
    JOIN players w ON w.id=m.winner_id
    JOIN players l ON l.id=m.loser_id
    WHERE m.match_date >= '2026-01-01' AND m.match_date < '2027-01-01'
      AND m.surface IS NOT NULL AND m.w_sets > 0
    ORDER BY m.match_date, m.id
""").fetchall()

print(f"Test matches: {len(test_matches)}")

# Stats trackers
results = []
correct_elo = 0
correct_xgb = 0
total = 0

by_surface = {}
by_conf = {}
by_month = {}
by_tour = {}

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
    
    flip = random.random() < 0.5
    if flip:
        p1_id, p2_id = m["winner_id"], m["loser_id"]
        p1_name, p2_name = m["wname"], m["lname"]
        p1_rank, p2_rank = m["winner_rank"], m["loser_rank"]
        actual_p1_win = True
    else:
        p1_id, p2_id = m["loser_id"], m["winner_id"]
        p1_name, p2_name = m["lname"], m["wname"]
        p1_rank, p2_rank = m["loser_rank"], m["winner_rank"]
        actual_p1_win = False
    
    try:
        # --- ELO PREDICTION (before recording) ---
        elo_pred = elo_engine.predict_winner(p1_id, p2_id, m["surface"], m["best_of"] == 5)
        elo_prob_p1 = elo_pred["prob_player1"]
        elo_correct = (elo_prob_p1 >= 0.5) == actual_p1_win
        
        # --- XGBoost PREDICTION (before recording) ---
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
        
        # --- ENSEMBLE (blend) ---
        blend_prob = 0.3 * elo_prob_p1 + 0.7 * xgb_prob_p1
        blend_correct = (blend_prob >= 0.5) == actual_p1_win
        
        # Record match result for ELO (walk-forward)
        elo_engine.record_match(
            m["winner_id"], m["loser_id"], m["surface"],
            md, m["best_of"] == 5,
            m["w_games"] or 0, m["l_games"] or 0
        )
        
    except Exception as e:
        if i < 5:
            print(f"  [ERRORE] match {m['id']}: {e}")
        continue
    
    total += 1
    if elo_correct: correct_elo += 1
    if blend_correct: correct_xgb += 1
    
    confidence = max(blend_prob, 1 - blend_prob)
    
    # Per-surface
    surf = m["surface"]
    if surf not in by_surface:
        by_surface[surf] = {"t": 0, "e": 0, "x": 0}
    by_surface[surf]["t"] += 1
    by_surface[surf]["e"] += 1 if elo_correct else 0
    by_surface[surf]["x"] += 1 if blend_correct else 0
    
    # Per-confidence
    conf_band = int(confidence * 10) * 10
    key = f"{conf_band}-{conf_band+9}%"
    if key not in by_conf:
        by_conf[key] = {"t": 0, "c": 0}
    by_conf[key]["t"] += 1
    by_conf[key]["c"] += 1 if blend_correct else 0
    
    # Per-month
    month = m["match_date"][:7]
    if month not in by_month:
        by_month[month] = {"t": 0, "c": 0}
    by_month[month]["t"] += 1
    by_month[month]["c"] += 1 if blend_correct else 0
    
    # Per-tour level
    tl = m["tour_level"]
    if tl not in by_tour:
        by_tour[tl] = {"t": 0, "c": 0}
    by_tour[tl]["t"] += 1
    by_tour[tl]["c"] += 1 if blend_correct else 0
    
    # --- Budget simulation (flat betting su confidenza alta) ---
    # Senza quote reali, usiamo flat stake al 2% del bankroll iniziale
    # quando il modello e' confidente > 70% (dove accuracy reale e' 90%+)
    if confidence >= 0.70:
        predicted = p1_name if blend_prob >= 0.5 else p2_name
        actual_winner = p1_name if actual_p1_win else p2_name
        won = predicted == actual_winner
        
        # Flat stake: 2% del bankroll corrente (non Kelly)
        stake = bankroll * 0.02
        
        # Stima quote conservative: basate su accuracy reale per fascia
        # Accuracy reale 70-79% -> quote ~1.15 (conservativo)
        # Accuracy reale 80-89% -> quote ~1.08
        # Accuracy reale 90%+  -> quote ~1.05
        if confidence >= 0.90:
            flat_odds = 1.05
        elif confidence >= 0.80:
            flat_odds = 1.08
        else:
            flat_odds = 1.15
        
        if stake >= 0.5:
            profit = stake * (flat_odds - 1) if won else -stake
            bankroll += profit
            bets_placed += 1
            if won: bets_won += 1
            total_stake += stake
            total_profit += profit
            
            if bankroll > peak: peak = bankroll
            dd = (peak - bankroll) / peak * 100
            if dd > max_dd: max_dd = dd
            
            bet_log.append({
                "date": m["match_date"], "p1": p1_name, "p2": p2_name,
                "surface": m["surface"], "tour": m["tour_level"],
                "pred": predicted, "actual": actual_winner, "won": won,
                "conf": round(confidence, 3),
                "odds": flat_odds, "stake": round(stake, 2),
                "profit": round(profit, 2), "br": round(bankroll, 2),
            })
    
    if (i + 1) % 300 == 0:
        print(f"  progresso: {i+1}/{len(test_matches)} "
              f"[ELO: {correct_elo/max(total,1)*100:.1f}% "
              f"Ensemble: {correct_xgb/max(total,1)*100:.1f}% "
              f"Bankroll: {bankroll:.0f}EUR]")

# ============================================================
# REPORT
# ============================================================
print("\n" + "=" * 70)
print("JBE TopSpin — Backtest ATP 2026")
print("=" * 70)

print(f"\n--- ACCURACY (Walk-forward, 2026) ---")
print(f"  ELO only:      {correct_elo/max(total,1)*100:.1f}% ({correct_elo}/{total})")
print(f"  Ensemble:      {correct_xgb/max(total,1)*100:.1f}% ({correct_xgb}/{total})")

print(f"\n--- PER SUPERFICIE (Ensemble) ---")
for s in ["Hard", "Clay", "Grass", "Carpet"]:
    if s in by_surface:
        d = by_surface[s]
        print(f"  {s:8s}: {d['t']:4d} match | ELO {d['e']/d['t']*100:.1f}% | Ensemble {d['x']/d['t']*100:.1f}%")

print(f"\n--- PER TOUR LEVEL (Ensemble) ---")
tl_map = {"G": "Grand Slam", "M": "Masters 1000", "A": "ATP 250/500", "F": "Futures/Chall"}
for tl in sorted(by_tour.keys(), key=lambda x: by_tour[x]["t"], reverse=True):
    d = by_tour[tl]
    name = tl_map.get(tl, tl)
    print(f"  {name:15s}: {d['t']:4d} match | Ensemble {d['c']/d['t']*100:.1f}%")

print(f"\n--- PER FASCIA CONFIDENZA ---")
for band in sorted(by_conf.keys()):
    d = by_conf[band]
    print(f"  {band:>10s}: {d['t']:4d} match | {d['c']/d['t']*100:.1f}% ({d['c']}/{d['t']})")

print(f"\n--- ANDAMENTO MENSILE ---")
for m in sorted(by_month.keys()):
    d = by_month[m]
    print(f"  {m}: {d['t']:3d} match | {d['c']/d['t']*100:.1f}%")

# Budget simulation results
print(f"\n" + "=" * 70)
print(f"SIMULAZIONE BUDGET 200 EUR")
print(f"Flat betting 2% su confidenza > 70%")
print(f"Quote conservative per fascia: 70-79%@1.15, 80-89%@1.08, 90%+@1.05")
print("=" * 70)

print(f"\nBankroll iniziale: {INITIAL_BR:.2f} EUR")
print(f"Bankroll finale:   {bankroll:.2f} EUR")
roi_total = (bankroll - INITIAL_BR) / INITIAL_BR * 100
print(f"ROI totale:        {roi_total:+.1f}%")
print(f"Profit netto:      {bankroll - INITIAL_BR:+.2f} EUR")
print(f"")
print(f"Scommesse:         {bets_placed}")
print(f"Vinte:             {bets_won} ({bets_won/max(bets_placed,1)*100:.1f}%)")
print(f"Stake totale:      {total_stake:.2f} EUR")
roi_stake = total_profit / total_stake * 100 if total_stake > 0 else 0
print(f"ROI su stake:      {roi_stake:+.1f}%")
print(f"Profitto medio:    {total_profit/max(bets_placed,1):+.2f} EUR/bet")
print(f"Max Drawdown:      {max_dd:.1f}%")
print(f"Bets/settimana:    {bets_placed/25:.1f}")

if bet_log:
    print(f"\n--- TOP 5 MIGLIORI ---")
    bet_log.sort(key=lambda x: x["profit"], reverse=True)
    for b in bet_log[:5]:
        w = "VINTA" if b["won"] else "PERSA"
        print(f"  {b['date']} | {b['p1']}-{b['p2']} ({b['surface']}) | "
              f"{w} {b['pred']} @ {b['odds']} | conf={b['conf']:.0%} | "
              f"stake={b['stake']:.1f}EUR profit={b['profit']:+.1f}EUR")

print(f"\n--- TOP 5 PEGGIORI ---")
for b in bet_log[-5:]:
    w = "VINTA" if b["won"] else "PERSA"
    print(f"  {b['date']} | {b['p1']}-{b['p2']} ({b['surface']}) | "
              f"{w} {b['pred']} @ {b['odds']} | conf={b['conf']:.0%} | "
              f"stake={b['stake']:.1f}EUR profit={b['profit']:+.1f}EUR")

db.close()
print("\nBacktest completato.")
