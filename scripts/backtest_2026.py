#!/usr/bin/env python3
"""
Backtest JBE TopSpin su 2026 ATP.
Walk-forward: ELO si aggiorna sequentialmente, XGBoost modelli pre-addestrati.
"""
import sys, os, json, math
import numpy as np
from datetime import date, datetime

sys.path.insert(0, "/opt/data/jbe-tennis/src")
from database import TennisDatabase
from config import DB_PATH, MODEL_DIR
from engine.elo_tennis import SurfaceELOEngine
from engine.xgboost_tennis import TopSpinEngine

db = TennisDatabase(DB_PATH)

# ============================================================
# PHASE 1: Warm-up ELO su tutti i match storici (2001-2025)
# ============================================================
print("=" * 70)
print("FASE 1: Warm-up ELO 2001-2025")
print("=" * 70)

elo_engine = SurfaceELOEngine(db)

# Get all historical matches with results, up to end of 2025
historical = db.conn.execute("""
    SELECT id, winner_id, loser_id, surface, match_date, best_of,
           w_games, l_games
    FROM tennis_matches
    WHERE match_date >= '2001-01-01' AND match_date < '2026-01-01'
      AND w_sets > 0 AND surface IS NOT NULL
    ORDER BY match_date, id
""").fetchall()

print(f"Match storici da processare: {len(historical)}")

for i, m in enumerate(historical):
    if i % 10000 == 0:
        print(f"  warm-up: {i}/{len(historical)}")
    
    match_date = date.fromisoformat(m["match_date"])
    elo_engine.record_match(
        m["winner_id"], m["loser_id"], m["surface"],
        match_date, m["best_of"] == 5,
        m["w_games"] or 0, m["l_games"] or 0
    )

# ELO rimane in memoria per la FASE 2
print(f"Warm-up completato. {len(elo_engine.ratings)} giocatori con ELO in memoria.")

# ============================================================
# PHASE 2: Backtest su 2026
# ============================================================
print("\n" + "=" * 70)
print("FASE 2: Backtest 2026")
print("=" * 70)

# Load XGBoost models
engine = TopSpinEngine(db, load_models=True)

# Get all 2026 matches with results
matches_2026 = db.conn.execute("""
    SELECT m.*, w.name as wname, l.name as lname
           , m.winner_rank as wrank, m.loser_rank as lrank
    FROM tennis_matches m
    JOIN players w ON w.id=m.winner_id
    JOIN players l ON l.id=m.loser_id
    WHERE m.match_date >= '2026-01-01' AND m.match_date < '2027-01-01'
      AND m.w_sets > 0 AND m.surface IS NOT NULL
    ORDER BY m.match_date, m.id
""").fetchall()

print(f"Match 2026 da testare: {len(matches_2026)}")

results = []
correct_elo = 0
correct_xgb = 0
correct_blend = 0
total = 0

by_surface = {}
by_tour = {}
by_month = {}

for i, m in enumerate(matches_2026):
    match_date = date.fromisoformat(m["match_date"])
    
    # Predict BEFORE seeing result (ELO already has historical data)
    # Randomly pick perspective (50/50 player1 = winner/loser)
    import random
    flip = random.random() < 0.5
    
    if flip:
        p1_id, p2_id = m["winner_id"], m["loser_id"]
        p1_rank, p2_rank = m["wrank"], m["lrank"]
        p1_name, p2_name = m["wname"], m["lname"]
        actual_winner_p1 = True
    else:
        p1_id, p2_id = m["loser_id"], m["winner_id"]
        p1_rank, p2_rank = m["lrank"], m["wrank"]
        p1_name, p2_name = m["lname"], m["wname"]
        actual_winner_p1 = False
    
    try:
        pred = engine.predict(
            m["id"], p1_id, p2_id, m["surface"], match_date,
            m["best_of"] or 3, m["round"], m["tour_level"],
            p1_rank, p2_rank
        )
    except Exception as e:
        if i < 5:
            print(f"  [ERRORE] match {m['id']}: {e}")
        continue
    
    total += 1
    
    # ELO prediction
    elo_pred_p1 = pred["prob_player1"]
    elo_winner = elo_pred_p1 >= 0.5
    elo_correct = elo_winner == actual_winner_p1
    if elo_correct: correct_elo += 1
    
    # Blend prediction
    prob_p1 = pred["prob_player1"]
    blend_winner = prob_p1 >= 0.5
    blend_correct = blend_winner == actual_winner_p1
    if blend_correct: correct_blend += 1
    
    # Confidence
    confidence = max(prob_p1, 1 - prob_p1)
    
    results.append({
        "date": m["match_date"],
        "p1": p1_name,
        "p2": p2_name,
        "surface": m["surface"],
        "tour_level": m["tour_level"],
        "prob": round(prob_p1, 3),
        "confidence": round(confidence, 3),
        "elo_correct": elo_correct,
        "blend_correct": blend_correct,
        "actual_p1_win": actual_winner_p1,
        "best_of": m["best_of"],
        "round": m["round"],
    })
    
    # Per-surface stats
    surf = m["surface"]
    if surf not in by_surface:
        by_surface[surf] = {"total": 0, "elo_correct": 0, "blend_correct": 0}
    by_surface[surf]["total"] += 1
    by_surface[surf]["elo_correct"] += 1 if elo_correct else 0
    by_surface[surf]["blend_correct"] += 1 if blend_correct else 0
    
    # Per-tour level
    tl = m["tour_level"]
    if tl not in by_tour:
        by_tour[tl] = {"total": 0, "blend_correct": 0}
    by_tour[tl]["total"] += 1
    by_tour[tl]["blend_correct"] += 1 if blend_correct else 0
    
    # Per month
    month = m["match_date"][:7]
    if month not in by_month:
        by_month[month] = {"total": 0, "blend_correct": 0}
    by_month[month]["total"] += 1
    by_month[month]["blend_correct"] += 1 if blend_correct else 0
    
    # Update ELO with actual result (walk-forward)
    elo_engine.record_match(
        m["winner_id"], m["loser_id"], m["surface"],
        match_date, m["best_of"] == 5,
        m["w_games"] or 0, m["l_games"] or 0
    )
    
    if (i + 1) % 200 == 0:
        print(f"  progresso: {i+1}/{len(matches_2026)} "
              f"[ELO: {correct_elo/max(total,1)*100:.1f}% "
              f"Blend: {correct_blend/max(total,1)*100:.1f}%]")

# ELO gia' aggiornato in memoria durante la fase 2

# ============================================================
# REPORT
# ============================================================
print("\n" + "=" * 70)
print("BACKTEST JBE TopSpin — ATP 2026")
print(f"Periodo: Gennaio - Giugno 2026 ({len(matches_2026)} match)")
print("=" * 70)

print(f"\n--- ACCURACY GLOBALE ---")
print(f"  ELO solo:          {correct_elo/max(total,1)*100:.1f}% ({correct_elo}/{total})")
print(f"  Ensemble (ELO+XGB): {correct_blend/max(total,1)*100:.1f}% ({correct_blend}/{total})")
improvement = (correct_blend - correct_elo) / max(total,1) * 100
print(f"  Miglioramento:     {improvement:+.1f}pp")

print(f"\n--- PER SUPERFICIE ---")
for surf in ["Hard", "Clay", "Grass", "Carpet"]:
    if surf in by_surface:
        s = by_surface[surf]
        elo_acc = s["elo_correct"] / s["total"] * 100
        blend_acc = s["blend_correct"] / s["total"] * 100
        print(f"  {surf:8s}: {s['total']:4d} match | "
              f"ELO {elo_acc:.1f}% | Ensemble {blend_acc:.1f}%"
              f" ({'+' if blend_acc >= elo_acc else ''}{blend_acc-elo_acc:.1f}pp)")

print(f"\n--- PER TOUR LEVEL ---")
tl_names = {"G": "Grand Slam", "M": "Masters 1000", "A": "ATP 250/500", "F": "Futures/Chall", "C": "Davis Cup"}
for tl in sorted(by_tour.keys(), key=lambda x: by_tour[x]["total"], reverse=True):
    s = by_tour[tl]
    acc = s["blend_correct"] / s["total"] * 100
    name = tl_names.get(tl, tl)
    print(f"  {name:20s}: {s['total']:4d} match | Ensemble {acc:.1f}%")

print(f"\n--- ANDAMENTO MENSILE ---")
for month in sorted(by_month.keys()):
    s = by_month[month]
    acc = s["blend_correct"] / s["total"] * 100
    print(f"  {month}: {s['total']:3d} match | Ensemble {acc:.1f}%")

# Confidence bands
print(f"\n--- PER FASCIA DI CONFIDENZA ---")
bands = [(0.5, 0.55), (0.55, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
for lo, hi in bands:
    subset = [r for r in results if lo <= r["confidence"] < hi]
    if subset:
        n_correct = sum(1 for r in subset if r["blend_correct"])
        print(f"  {lo:.0%}-{hi:.0%}: {len(subset):3d} match | "
              f"{n_correct/len(subset)*100:.1f}% ({n_correct}/{len(subset)})")

# Top confidence correct/incorrect examples
results.sort(key=lambda x: x["confidence"], reverse=True)
print(f"\n--- TOP 5 PREDIZIONI PIU' CONFIDENTI (CORRETTE) ---")
shown = 0
for r in results:
    if r["blend_correct"] and shown < 5:
        print(f"  {r['date']} | {r['p1']} vs {r['p2']} ({r['surface']}) | "
              f"conf={r['confidence']:.0%} prob={r['prob']:.0%}")
        shown += 1

print(f"\n--- TOP 5 PREDIZIONI PIU' CONFIDENTI (SBAGLIATE) ---")
shown = 0
for r in results:
    if not r["blend_correct"] and shown < 5:
        actual_winner = r["p1"] if r["actual_p1_win"] else r["p2"]
        pred_winner = r["p1"] if r["prob"] >= 0.5 else r["p2"]
        print(f"  {r['date']} | {r['p1']} vs {r['p2']} ({r['surface']}) | "
              f"conf={r['confidence']:.0%} | predetto={pred_winner} ma vinto da {actual_winner}")
        shown += 1

db.close()
print("\nBacktest completato.")
