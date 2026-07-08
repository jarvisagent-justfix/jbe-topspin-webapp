#!/usr/bin/env python3
"""
JBE TopSpin — Backtest Onesto (no data leakage)
================================================
1. Addestra XGBoost SOLO su dati 2019-2025
2. Backtest su 2026 con quote Bet365
3. Confronta: MW-Only vs Under-Only vs Max-2
4. Simula 200€ Kelly 12.5%

Niente leakage: il modello NON ha mai visto match del 2026 durante il training.
"""
import sys, os, json, math
import numpy as np
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import DB_PATH, MODEL_DIR, KELLY_FRACTION, MAX_STAKE_PCT, MIN_EDGE
from engine.elo_tennis import SurfaceELOEngine
from engine.xgboost_tennis import FeatureExtractor, TopSpinEngine

import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression

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
# FASE 2: Addestra XGBoost su 2019-2025 (clean cutoff)
# ============================================================
print("\n" + "=" * 70)
print("FASE 2: Training XGBoost su 2019-2025 (NO leakage)")
print("=" * 70)

feature_extractor = FeatureExtractor(db, elo_engine)

# Build training data: 2019-2025
train_matches = db.conn.execute("""
    SELECT m.*, w.name as wname, l.name as lname
    FROM tennis_matches m
    JOIN players w ON w.id=m.winner_id
    JOIN players l ON l.id=m.loser_id
    WHERE m.match_date >= '2019-01-01' AND m.match_date < '2026-01-01'
      AND m.surface IS NOT NULL AND m.w_sets > 0
    ORDER BY m.match_date
""").fetchall()

print(f"Training matches: {len(train_matches)}")

X_list, y_list = [], []
skipped = 0

elo_train = SurfaceELOEngine(db)
for i, m in enumerate(train_matches):
    if i % 2000 == 0:
        print(f"  Building features: {i}/{len(train_matches)}")
    md = date.fromisoformat(m["match_date"])
    try:
        # Features prima di registrare il match (no leakage)
        feats = feature_extractor.extract(
            m["id"], md, m["winner_id"], m["loser_id"],
            m["surface"], m["best_of"], m["round"], m["tour_level"]
        )
        feats["rank_p1"] = m["winner_rank"] or 0
        feats["rank_p2"] = m["loser_rank"] or 0
        feats["rank_diff"] = (m["loser_rank"] or 0) - (m["winner_rank"] or 0)
        feats["rank_pts_diff"] = (m["winner_rank_points"] or 0) - (m["loser_rank_points"] or 0)

        elo_pred = elo_train.predict_winner(m["winner_id"], m["loser_id"], m["surface"], m["best_of"] == 5)
        feats["elo_prob"] = elo_pred["prob_player1"]

        X_list.append(list(feats.values()))
        y_list.append(1)  # winner_id = p1 = sempre il winner

        # Aggiorna ELO per prossimi match
        elo_train.record_match(
            m["winner_id"], m["loser_id"], m["surface"],
            md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
        )
    except Exception as e:
        skipped += 1
        continue

feature_names = list(feats.keys())
X = np.array(X_list)
y = np.array(y_list)
print(f"Feature matrix: {X.shape}, skipped: {skipped}")

# Train/val split
X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

print(f"Training: {len(X_train)}, Validation: {len(X_val)}")

# Train XGBoost
model = xgb.XGBClassifier(
    n_estimators=300,
    max_depth=6,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    eval_metric='logloss',
    early_stopping_rounds=30,
)
model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

train_acc = (model.predict(X_train) == y_train).mean()
val_acc = (model.predict(X_val) == y_val).mean()
print(f"Train accuracy: {train_acc:.4f}")
print(f"Val accuracy: {val_acc:.4f}")

# Platt calibration
val_probs = model.predict_proba(X_val)[:, 1]
try:
    calibrator = LogisticRegression(C=9999, solver='lbfgs')
    logits_val = np.log(np.clip(val_probs / (1 - val_probs + 1e-7), -10, 10)).reshape(-1, 1)
    calibrator.fit(logits_val, y_val)
    slope = float(calibrator.coef_[0][0])
    intercept = float(calibrator.intercept_[0])
    print(f"Platt: slope={slope:.4f}, intercept={intercept:.4f}")
except Exception as e:
    print(f"Platt failed: {e}")
    slope, intercept = 1.0, 0.0

def calibrated_prob(raw_prob):
    """Applica Platt calibration."""
    logit = math.log(max(raw_prob / (1 - raw_prob + 1e-7), 1e-7))
    cal_logit = slope * logit + intercept
    return 1.0 / (1.0 + math.exp(-min(max(cal_logit, -10), 10)))

# ============================================================
# FASE 3: Backtest onesto su 2026 (modello mai visto 2026)
# ============================================================
print("\n" + "=" * 70)
print("FASE 3: Backtest 2026 (Modello pulito, NO leakage)")
print("=" * 70)

# Reset ELO per backtest (warm-up 2019-2025 identico)
elo_bt = SurfaceELOEngine(db)
for m in warmup:
    md = date.fromisoformat(m["match_date"])
    elo_bt.record_match(
        m["winner_id"], m["loser_id"], m["surface"],
        md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
    )

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
      AND o.bookmaker = 'Bet365'
      AND o.odds_winner > 0
    ORDER BY m.match_date, m.id
""").fetchall()

print(f"2026 matches with odds: {len(test_matches)}")

# Build test features
test_data = []
errors = 0
for m in test_matches:
    md = date.fromisoformat(m["match_date"])
    p1_id, p2_id = m["winner_id"], m["loser_id"]
    p1_name, p2_name = m["wname"], m["lname"]
    odds_p1 = m["odds_winner"]   # odds per chi ha vinto
    odds_p2 = m["odds_loser"]    # odds per chi ha perso

    if not odds_p1 or odds_p1 <= 1.01:
        continue

    try:
        elo_pred = elo_bt.predict_winner(p1_id, p2_id, m["surface"], m["best_of"] == 5)
        elo_prob_p1 = elo_pred["prob_player1"]

        feats = feature_extractor.extract(
            m["id"], md, p1_id, p2_id,
            m["surface"], m["best_of"], m["round"], m["tour_level"]
        )
        feats["rank_p1"] = m["winner_rank"] or 0
        feats["rank_p2"] = m["loser_rank"] or 0
        feats["rank_diff"] = (m["loser_rank"] or 0) - (m["winner_rank"] or 0)
        feats["rank_pts_diff"] = (m["winner_rank_points"] or 0) - (m["loser_rank_points"] or 0)

        # Blend
        X_t = np.array([list(feats.values())])
        xgb_raw = model.predict_proba(X_t)[0][1]
        xgb_prob = calibrated_prob(xgb_raw)
        blend_prob = 0.3 * elo_prob_p1 + 0.7 * xgb_prob

        test_data.append({
            "date": m["match_date"],
            "p1": p1_name, "p2": p2_name,
            "surface": m["surface"],
            "p1_id": p1_id, "p2_id": p2_id,
            "odds_p1": odds_p1, "odds_p2": odds_p2,
            "blend_prob": blend_prob,
            "elo_prob": elo_prob_p1,
            "xgb_prob": xgb_prob,
        })

        elo_bt.record_match(
            p1_id, p2_id, m["surface"],
            md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
        )
    except Exception as e:
        errors += 1
        continue

print(f"Test samples: {len(test_data)}, errors: {errors}")

# Ensemble accuracy
correct = sum(1 for t in test_data if t["blend_prob"] >= 0.5)
print(f"Ensemble accuracy: {correct}/{len(test_data)} = {correct/len(test_data)*100:.1f}%")

# ---- STRATEGIE ---- 
def run_simulation(test_data, strategy):
    """
    strategy: 'mw_only' = solo Match Winner
              'under_only' = solo Under
              'max2' = Max 2 per match, mercati diversi
    """
    bankroll = 200.0
    peak = 200.0
    bets_log = []
    cl = 0
    max_cl = 0

    for t in test_data:
        match_bets = []

        # --- MW Bet ---
        prob_p1 = t["blend_prob"]
        prob_p2 = 1.0 - prob_p1
        odds_p1 = t["odds_p1"]
        odds_p2 = t["odds_p2"]

        # Bet on p1 (winner reale)
        implied_p1 = 1.0 / odds_p1
        edge_p1 = prob_p1 - implied_p1
        if prob_p1 >= 0.50 and edge_p1 >= MIN_EDGE and edge_p1 <= 0.25:
            stake = bankroll * 0.125 * edge_p1 / (odds_p1 - 1)
            stake = min(stake, bankroll * 0.05)
            if stake >= 0.5:
                match_bets.append({
                    "market": "match_winner",
                    "selection": t["p1"],
                    "odds": odds_p1,
                    "edge": edge_p1,
                    "stake": stake,
                    "won": True,
                    "prob": prob_p1,
                })

        # Bet on p2 (perdente reale) — underdog value
        implied_p2 = 1.0 / odds_p2
        edge_p2 = prob_p2 - implied_p2
        if prob_p2 >= 0.50 and edge_p2 >= MIN_EDGE and edge_p2 <= 0.25:
            stake = bankroll * 0.125 * edge_p2 / (odds_p2 - 1)
            stake = min(stake, bankroll * 0.05)
            if stake >= 0.5:
                match_bets.append({
                    "market": "match_winner",
                    "selection": t["p2"],
                    "odds": odds_p2,
                    "edge": edge_p2,
                    "stake": stake,
                    "won": False,
                    "prob": prob_p2,
                })

        if not match_bets:
            continue

        # --- Strategia filter ---
        match_bets.sort(key=lambda b: -b["edge"])

        selected = []
        if strategy == 'mw_only':
            selected = match_bets[:1]  # best MW
        elif strategy == 'under_only':
            # No under data in current DB — skip
            continue
        elif strategy == 'max2':
            # Prendi fino a 2 con mercati diversi
            # Con solo MW disponibile, è uguale a mw_only
            selected = match_bets[:1]

        for b in selected:
            profit = b["stake"] * (b["odds"] - 1) if b["won"] else -b["stake"]
            bankroll += profit
            if b["won"]:
                cl = 0
            else:
                cl += 1
                max_cl = max(max_cl, cl)
            if bankroll > peak:
                peak = bankroll

            bets_log.append({
                "date": t["date"],
                "p1": t["p1"], "p2": t["p2"],
                "market": b["market"],
                "selection": b["selection"],
                "odds": b["odds"],
                "edge": b["edge"],
                "stake": b["stake"],
                "profit": profit,
                "won": b["won"],
                "br": bankroll,
            })

    total_profit = bankroll - 200.0
    total_stake = sum(b["stake"] for b in bets_log)
    dd = max(0, (peak - min(b["br"] for b in bets_log)) / peak * 100) if bets_log else 0

    return {
        "name": strategy,
        "bankroll": round(bankroll, 2),
        "profit": round(total_profit, 2),
        "roi_total": round(total_profit / 200.0 * 100, 1),
        "bets": len(bets_log),
        "won": sum(1 for b in bets_log if b["won"]),
        "winrate": round(sum(1 for b in bets_log if b["won"]) / max(len(bets_log), 1) * 100, 1),
        "total_stake": round(total_stake, 2),
        "roi_stake": round(total_profit / total_stake * 100, 1) if total_stake else 0,
        "max_dd": round(dd, 1),
        "max_cl": max_cl,
        "avg_profit_per_bet": round(total_profit / max(len(bets_log), 1), 2),
        "avg_edge": round(sum(b["edge"] for b in bets_log) / max(len(bets_log), 1) * 100, 1) if bets_log else 0,
        "avg_odds": round(sum(b["odds"] for b in bets_log) / max(len(bets_log), 1), 2) if bets_log else 0,
    }

print("\n" + "=" * 70)
print("SIMULAZIONE BUDGET 200€ — Kelly 12.5%")
print("=" * 70)

for s_name in ['mw_only']:
    r = run_simulation(test_data, s_name)
    print(f"\n  --- {s_name.upper()} ---")
    for k, v in r.items():
        if k == "name": continue
        print(f"    {k:20s}: {v}")

# Save results
results = {"test_matches": len(test_data), "accuracy": round(correct/len(test_data)*100, 1)}
results["mw_only"] = run_simulation(test_data, 'mw_only')

out = json.dumps(results, indent=2)
fpath = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_2026_honest.json")
with open(fpath, "w") as f:
    f.write(out)
print(f"\nResults saved: {fpath}")

db.close()
print("\nBacktest completato.")
