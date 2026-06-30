#!/usr/bin/env python3
"""
JBE TopSpin — Platt Calibration
================================
Calibra le probabilita' XGBoost con Platt scaling.
Rende le probabilita' oneste: un 80% deve vincere ~80% delle volte.

Uso: PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/calibrate.py
"""
import sys, os, json, pickle
import numpy as np
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from engine.xgboost_tennis import TopSpinEngine
from config import DB_PATH, MODEL_DIR

from sklearn.linear_model import LogisticRegression
from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss


def calibrate():
    db = TennisDatabase(DB_PATH)
    engine = TopSpinEngine(db, load_models=True)

    # Verifica che il modello sia caricato
    if not engine.xgb.winner_model:
        print("[ERRORE] winner_model non caricato. Addestra prima il modello.")
        db.close()
        return

    # Carica match 2024-2025 come validazione
    print("[INFO] Caricamento match di validazione 2024-2025...")
    val_matches = db.conn.execute("""
        SELECT m.id, m.winner_id, m.loser_id, m.surface, m.match_date,
               m.best_of, m.round, m.tour_level, m.w_sets, m.l_sets,
               w.name as wname, l.name as lname
        FROM tennis_matches m
        JOIN players w ON w.id=m.winner_id
        JOIN players l ON l.id=m.loser_id
        WHERE m.match_date >= '2024-01-01' AND m.match_date < '2026-01-01'
          AND m.surface IS NOT NULL AND m.w_sets > 0
        ORDER BY m.match_date
    """).fetchall()
    print(f"  {len(val_matches)} match di validazione")

    # Carica ultimi rating ELO dal DB
    engine.elo_engine.load_all_ratings()

    # Processa match in ordine cronologico per aggiornare ELO
    # ma raccogliamo le predizioni PRIMA di aggiornare
    raw_probs = []
    actuals = []
    match_dates = []

    print("[INFO] Raccolta predizioni...")
    total = len(val_matches)
    for i, m in enumerate(val_matches):
        try:
            match_date = date.fromisoformat(m["match_date"])

            # Predict BEFORE recording the match
            # Flip perspective random so we get both classes
            if np.random.random() < 0.5:
                p1_id, p2_id = m["winner_id"], m["loser_id"]
                y = 1
            else:
                p1_id, p2_id = m["loser_id"], m["winner_id"]
                y = 0

            # Apply decay first
            for pid in (p1_id, p2_id):
                if pid in engine.elo_engine.ratings:
                    engine.elo_engine.ratings[pid].apply_decay(match_date)

            # Extract features and predict
            feats = engine.feature_extractor.extract(
                m["id"], match_date, p1_id, p2_id,
                m["surface"], m["best_of"], m["round"], m["tour_level"]
            )
            feats["rank_p1"] = 0
            feats["rank_p2"] = 0
            feats["rank_diff"] = 0
            feats["rank_pts_diff"] = 0

            X = np.array([list(feats.values())])
            prob_xgb = engine.xgb.winner_model.predict_proba(X)[0][1]

            raw_probs.append(float(prob_xgb))
            actuals.append(y)
            match_dates.append(match_date)

            # Now record the match for future ELO
            engine.elo_engine.record_match(
                m["winner_id"], m["loser_id"], m["surface"],
                match_date, m["best_of"] == 5,
                m["w_games"] or 0, m["l_games"] or 0
            )

        except Exception as e:
            if i < 5:
                print(f"  [ERRORE] match {m['id']}: {e}")

    raw_probs = np.array(raw_probs)
    actuals = np.array(actuals)

    print(f"\n[INFO] Raccolte {len(raw_probs)} predizioni")
    print(f"  Classe positiva: {actuals.sum()}/{len(actuals)} ({actuals.mean()*100:.1f}%)")
    print(f"  Prob. media grezza: {raw_probs.mean():.4f}")

    # Calcola metriche pre-calibrazione
    brier_raw = brier_score_loss(actuals, raw_probs)
    acc_raw = ((raw_probs >= 0.5) == actuals).mean()

    # === PLATT SCALING ===
    print("\n[INFO] Fitting Platt scaling...")
    platts = LogisticRegression(C=1e10, solver='lbfgs')  # C grande = no regolarizzazione
    # Transform: logit delle probabilità come feature
    eps = 1e-7
    logit_probs = np.clip(raw_probs, eps, 1 - eps)
    X_logit = np.log(logit_probs / (1 - logit_probs)).reshape(-1, 1)
    platts.fit(X_logit, actuals)

    # Applica calibrazione
    calib_probs = platts.predict_proba(X_logit)[:, 1]

    # Metriche post-calibrazione
    brier_cal = brier_score_loss(actuals, calib_probs)
    acc_cal = ((calib_probs >= 0.5) == actuals).mean()

    print(f"\n[RISULTATI]")
    print(f"  Accuracy raw:            {acc_raw:.4f}")
    print(f"  Accuracy calibrata:      {acc_cal:.4f}")
    print(f"  Brier score raw:         {brier_raw:.6f}")
    print(f"  Brier score calibrata:   {brier_cal:.6f}")
    print(f"  Miglioramento Brier:     {(brier_raw - brier_cal):.6f}")

    # Stima parametri Platt: slope e intercept
    # LogisticRegression coef_[0] = slope, intercept_ = intercept
    params = {
        "slope": float(platts.coef_[0][0]),
        "intercept": float(platts.intercept_[0]),
        "brier_raw": brier_raw,
        "brier_cal": brier_cal,
        "accuracy_raw": float(acc_raw),
        "accuracy_cal": float(acc_cal),
        "n_samples": len(raw_probs),
        "calibration_date": date.today().isoformat(),
    }

    # Salva parametri
    cal_path = os.path.join(MODEL_DIR, "platt_calibration.json")
    with open(cal_path, "w") as f:
        json.dump(params, f, indent=2)
    print(f"\n[OK] Calibrazione salvata: {cal_path}")

    # Reliability by decile
    print("\n--- Reliability Diagram (decili) ---")
    print(f"{'Bin':>6} {'N':>5} {'Prob media':>10} {'Freq attuale':>12} {'Differenza':>10}")
    print("-" * 45)
    for bin_idx in range(10):
        lo, hi = bin_idx * 0.1, (bin_idx + 1) * 0.1
        mask = (raw_probs >= lo) & (raw_probs < hi)
        n = mask.sum()
        if n > 0:
            mean_prob = raw_probs[mask].mean()
            actual_freq = actuals[mask].mean()
            diff = actual_freq - mean_prob
            print(f"  {lo:.0%}-{hi:.0%}  {n:5d}  {mean_prob:.4f}       {actual_freq:.4f}          {diff:+.4f}")

    cal_path2 = os.path.join(MODEL_DIR, "platt_calibration.json")
    print(f"\n[INFO] Stessa calibrazione anche per calibrated_model")

    # Crea un wrapper che applica la calibrazione
    # Lo useremo nel TopSpinEngine
    db.close()
    print("\n[OK] Calibrazione completata. Abilita nel TopSpinEngine")


if __name__ == "__main__":
    calibrate()
