#!/usr/bin/env python3
"""JBE TopSpin — Retrain XGBoost su dati 2023-2026 + Platt Calibration"""
import sys, os, json, numpy as np
from datetime import date
from sklearn.model_selection import train_test_split
from sklearn.isotonic import IsotonicRegression
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from engine.xgboost_tennis import XGBoostTrainer
from engine.elo_tennis import SurfaceELOEngine
from config import DB_PATH, MODEL_DIR

db = TennisDatabase(DB_PATH)
trainer = XGBoostTrainer(db)

TRAIN_START = "2023-01-01"
TRAIN_END = "2026-06-25"
N_ELO_WARMUP = 5000  # Warm-up ELO con N match prima del training

print("=" * 60)
print(f"JBE TopSpin — Retrain {TRAIN_START} to {TRAIN_END}")
print("=" * 60)

# FASE 1: ELO Warm-up (2000-2022)
print(f"\n[1/4] ELO warm-up...")
warmup = db.conn.execute("""
    SELECT id, winner_id, loser_id, surface, match_date, best_of, w_games, l_games
    FROM tennis_matches
    WHERE match_date < ?
      AND w_sets > 0 AND surface IS NOT NULL
    ORDER BY match_date
    LIMIT ?
""", (TRAIN_START, N_ELO_WARMUP)).fetchall()
for m in warmup:
    md = date.fromisoformat(m["match_date"])
    trainer.elo_engine.record_match(m["winner_id"], m["loser_id"], m["surface"],
                                    md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0)
print(f"  ELO warm-up: {len(warmup)} matches, {len(trainer.elo_engine.ratings)} players")

# FASE 2: Build Training Data
print(f"\n[2/4] Building training data {TRAIN_START} to {TRAIN_END}...")
X, y_winner, y_games, y_sets, feature_names = trainer.build_training_data(TRAIN_START, TRAIN_END)
print(f"  Training samples: {len(X)}")

# FASE 3: Split and Train
print(f"\n[3/4] Training models...")
X_train, X_val, y_w_train, y_w_val, y_g_train, y_g_val, y_s_train, y_s_val = train_test_split(
    X, y_winner, y_games, y_sets, test_size=0.2, random_state=42
)

val_acc = trainer.train_winner_model(X_train, y_w_train, X_val, y_w_val)
trainer.train_games_model(X_train, y_g_train, X_val, y_g_val)
trainer.train_sets_model(X_train, y_s_train, X_val, y_s_val)

# FASE 4: Platt Calibration
print(f"\n[4/4] Computing Platt calibration...")
val_probs = trainer.winner_model.predict_proba(X_val)[:, 1]
from sklearn.linear_model import LogisticRegression
# Platt scaling: fit logistic regression on log-odds of XGBoost output
from sklearn.calibration import CalibratedClassifierCV
try:
    calibrator = LogisticRegression(C=9999, solver='lbfgs')
    logits_val = np.log(val_probs / (1 - val_probs + 1e-7)).reshape(-1, 1)
    calibrator.fit(logits_val, y_w_val)
    
    slope = float(calibrator.coef_[0][0])
    intercept = float(calibrator.intercept_[0])
    
    cal_path = os.path.join(MODEL_DIR, "platt_calibration.json")
    with open(cal_path, "w") as f:
        json.dump({"slope": slope, "intercept": intercept}, f)
    print(f"  Platt calibration: slope={slope:.4f}, intercept={intercept:.4f}")
    print(f"  Saved: {cal_path}")
except Exception as e:
    print(f"  [WARN] Platt calibration failed: {e}")
    print("  Using identity calibration instead")

# FASE 5: Save models (overwrites current topspin_*.json)
print(f"\n[5/4] Saving models...")
trainer.save_models(prefix="topspin")

# Verify
print(f"\nVerifying reload...")
from engine.xgboost_tennis import TopSpinEngine
engine = TopSpinEngine(db, load_models=True)
print(f"  Models reloaded successfully: Winner={'yes' if engine.xgb.winner_model else 'no'}, Games={'yes' if engine.xgb.games_model else 'no'}")

# Summary
print(f"\n{'=' * 60}")
print(f"RETRAIN COMPLETE")
print(f"{'=' * 60}")
print(f"  Training period: {TRAIN_START} to {TRAIN_END}")
print(f"  Training samples: {len(X)}")
print(f"  Validation accuracy: {val_acc:.4f} ({val_acc*100:.1f}%)")
print(f"  Platt calibration: slope={slope:.4f}, intercept={intercept:.4f}")
db.close()
print("  Done! Models updated for Wimbledon.")
