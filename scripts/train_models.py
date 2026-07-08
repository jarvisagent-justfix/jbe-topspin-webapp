#!/usr/bin/env python3
"""
JBE TopSpin — Training & Calibration dei modelli XGBoost
=========================================================
Unico punto di ingresso per: addestramento, retraining e calibrazione.

Perché unificato:
  - train_topspin.py, retrain_2026.py e calibrate.py facevano tutti la stessa cosa
    (addestrare/calibrare i modelli XGBoost) ma in file separati con logiche simili.
  - Unificarli evita la duplicazione di codice e garantisce che training e calibrazione
    usino sempre gli stessi parametri e la stessa pipeline.

Modalità:
  --mode train       : Addestramento walk-forward (train 5 anni, test 1 anno)
  --mode retrain     : Retrain completo su periodo specifico (default: 2023-2026)
  --mode calibrate   : Platt calibration sul modello esistente

Uso:
  PYTHONPATH=src python3 scripts/train_models.py --mode train
  PYTHONPATH=src python3 scripts/train_models.py --mode retrain
  PYTHONPATH=src python3 scripts/train_models.py --mode calibrate
"""
import sys, os, json
import numpy as np
from datetime import date, timedelta
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from engine.xgboost_tennis import XGBoostTrainer, TopSpinEngine
from engine.elo_tennis import SurfaceELOEngine
from config import DB_PATH, MODEL_DIR


# ============================================================
# MODALITÀ 1: TRAIN (walk-forward)
# ============================================================
def mode_train():
    """
    Addestramento walk-forward: finestre mobili 5 anni train → 1 anno test.
    Perché walk-forward: simula l'uso reale del modello nel tempo,
    evitando il data leakage di un singolo split train/test.
    """
    db = TennisDatabase()
    trainer = XGBoostTrainer(db)

    windows = [
        ("2016-01-01", "2021-01-01", "2021-01-01", "2022-01-01"),
        ("2017-01-01", "2022-01-01", "2022-01-01", "2023-01-01"),
        ("2018-01-01", "2023-01-01", "2023-01-01", "2024-01-01"),
        ("2019-01-01", "2024-01-01", "2024-01-01", "2025-01-01"),
    ]

    all_results = []

    for train_start, train_end, test_start, test_end in windows:
        print(f"\n{'='*60}")
        print(f"Walk-forward: Train {train_start[:4]}-{train_end[:4]} > Test {test_start[:4]}-{test_end[:4]}")
        print(f"{'='*60}")

        trainer.elo_engine = SurfaceELOEngine(db)

        print("Costruzione training data...")
        X, y_winner, y_games, y_sets, feature_names = trainer.build_training_data(train_start, train_end)

        if len(X) < 100:
            print(f"Dati insufficienti: {len(X)} campioni")
            continue

        X_train, X_val, y_w_train, y_w_val, y_g_train, y_g_val, y_s_train, y_s_val = train_test_split(
            X, y_winner, y_games, y_sets, test_size=0.2, random_state=42
        )

        val_acc = trainer.train_winner_model(X_train, y_w_train, X_val, y_w_val)
        trainer.train_games_model(X_train, y_g_train, X_val, y_g_val)
        trainer.train_sets_model(X_train, y_s_train, X_val, y_s_val)

        print(f"\nTest su {test_start[:4]}-{test_end[:4]}...")
        test_matches = db.conn.execute(
            """SELECT m.*, w.name as wname, l.name as lname
               FROM tennis_matches m
               JOIN players w ON w.id=m.winner_id
               JOIN players l ON l.id=m.loser_id
               WHERE m.match_date >= ? AND m.match_date < ?
               AND m.surface IS NOT NULL AND m.w_sets > 0
               ORDER BY m.match_date""",
            (test_start, test_end),
        ).fetchall()

        correct_elo = 0
        correct_xgb = 0
        total_test = 0

        for m in test_matches:
            try:
                match_date = date.fromisoformat(m["match_date"])

                elo_pred = trainer.elo_engine.predict_winner(
                    m["winner_id"], m["loser_id"], m["surface"], m["best_of"] == 5
                )
                pred_elo = m["winner_id"] if elo_pred["prob_player1"] > 0.5 else m["loser_id"]
                if pred_elo == m["winner_id"]:
                    correct_elo += 1

                feats = trainer.feature_extractor.extract(
                    m["id"], match_date, m["winner_id"], m["loser_id"],
                    m["surface"], m["best_of"], m["round"], m["tour_level"]
                )
                feats["rank_p1"] = m["winner_rank"] or 0
                feats["rank_p2"] = m["loser_rank"] or 0
                feats["rank_diff"] = (m["loser_rank"] or 0) - (m["winner_rank"] or 0)
                feats["rank_pts_diff"] = (m["winner_rank_points"] or 0) - (m["loser_rank_points"] or 0)

                X_test = np.array([list(feats.values())])

                if trainer.winner_model:
                    prob_xgb = trainer.winner_model.predict_proba(X_test)[0][1]
                    pred_xgb = m["winner_id"] if prob_xgb > 0.5 else m["loser_id"]
                    if pred_xgb == m["winner_id"]:
                        correct_xgb += 1

                total_test += 1

                trainer.elo_engine.record_match(
                    m["winner_id"], m["loser_id"], m["surface"],
                    match_date, m["best_of"] == 5,
                    m["w_games"] or 0, m["l_games"] or 0
                )

            except Exception:
                pass

        acc_elo = correct_elo / total_test * 100 if total_test else 0
        acc_xgb = correct_xgb / total_test * 100 if total_test else 0

        result = {
            "window": f"{test_start[:4]}-{test_end[:4]}",
            "n_train": len(X),
            "n_test": total_test,
            "acc_elo": acc_elo,
            "acc_xgb": acc_xgb,
            "improvement": acc_xgb - acc_elo,
        }
        all_results.append(result)

        print(f"  ELO solo:     {acc_elo:.1f}%")
        print(f"  XGBoost:      {acc_xgb:.1f}%")
        trainer.save_models(prefix=f"topspin_{test_end[:4]}")

    print(f"\n{'='*60}")
    print("RIEPILOGO BACKTEST WALK-FORWARD")
    print(f"{'='*60}")
    print(f"{'Window':>15} {'Train':>8} {'Test':>8} {'ELO':>8} {'XGB':>8} {'+/-':>8}")
    print("-" * 60)
    for r in all_results:
        print(f"{r['window']:>15} {r['n_train']:>8} {r['n_test']:>8} {r['acc_elo']:>7.1f}% {r['acc_xgb']:>7.1f}% {r['improvement']:>+7.1f}pp")

    print(f"\nTraining modello finale su tutti i dati (2001-2024)...")
    trainer.elo_engine = SurfaceELOEngine(db)
    X_all, y_all, _, _, _ = trainer.build_training_data("2001-01-01", "2025-01-01")
    X_train, X_val, y_train, y_val = train_test_split(X_all, y_all, test_size=0.2, random_state=42)
    val_acc = trainer.train_winner_model(X_train, y_train, X_val, y_val)
    trainer.save_models(prefix="topspin_final")
    print(f"Modello finale salvato. Validation accuracy: {val_acc:.4f}")
    db.close()


# ============================================================
# MODALITÀ 2: RETRAIN (addestramento completo su periodo recente)
# ============================================================
def mode_retrain(train_start="2023-01-01", train_end="2026-06-25"):
    """
    Retrain completo del modello XGBoost su un periodo specifico.
    Utile dopo aver accumulato nuovi match (es. dopo Wimbledon).
    Include ELO warm-up, training, e Platt calibration in un unico passaggio.
    """
    db = TennisDatabase(DB_PATH)
    trainer = XGBoostTrainer(db)
    N_ELO_WARMUP = 5000

    print("=" * 60)
    print(f"Retrain {train_start} to {train_end}")
    print("=" * 60)

    # FASE 1: ELO Warm-up
    print(f"\n[1/4] ELO warm-up ({N_ELO_WARMUP} match prima del training)...")
    warmup = db.conn.execute("""
        SELECT id, winner_id, loser_id, surface, match_date, best_of, w_games, l_games
        FROM tennis_matches
        WHERE match_date < ?
          AND w_sets > 0 AND surface IS NOT NULL
        ORDER BY match_date
        LIMIT ?
    """, (train_start, N_ELO_WARMUP)).fetchall()
    for m in warmup:
        md = date.fromisoformat(m["match_date"])
        trainer.elo_engine.record_match(m["winner_id"], m["loser_id"], m["surface"],
                                        md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0)
    print(f"  ELO warm-up: {len(warmup)} matches, {len(trainer.elo_engine.ratings)} players")

    # FASE 2: Build data
    print(f"\n[2/4] Building training data...")
    X, y_winner, y_games, y_sets, feature_names = trainer.build_training_data(train_start, train_end)
    print(f"  Training samples: {len(X)}")

    # FASE 3: Train
    print(f"\n[3/4] Training models...")
    X_train, X_val, y_w_train, y_w_val, y_g_train, y_g_val, y_s_train, y_s_val = train_test_split(
        X, y_winner, y_games, y_sets, test_size=0.2, random_state=42
    )
    val_acc = trainer.train_winner_model(X_train, y_w_train, X_val, y_w_val)
    trainer.train_games_model(X_train, y_g_train, X_val, y_g_val)
    trainer.train_sets_model(X_train, y_s_train, X_val, y_s_val)

    # FASE 4: Platt Calibration integrata
    print(f"\n[4/4] Platt calibration...")
    val_probs = trainer.winner_model.predict_proba(X_val)[:, 1]
    slope, intercept = 1.0, 0.0
    try:
        calibrator = LogisticRegression(C=9999, solver='lbfgs')
        logits_val = np.log(val_probs / (1 - val_probs + 1e-7)).reshape(-1, 1)
        calibrator.fit(logits_val, y_w_val)
        slope = float(calibrator.coef_[0][0])
        intercept = float(calibrator.intercept_[0])

        cal_path = os.path.join(MODEL_DIR, "platt_calibration.json")
        with open(cal_path, "w") as f:
            json.dump({"slope": slope, "intercept": intercept}, f)
        print(f"  Platt: slope={slope:.4f}, intercept={intercept:.4f}")
    except Exception as e:
        print(f"  [WARN] Platt calibration fallita: {e}")

    # FASE 5: Save
    trainer.save_models(prefix="topspin")

    print(f"\n{'=' * 60}")
    print(f"RETRAIN COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Periodo: {train_start} to {train_end}")
    print(f"  Campioni: {len(X)}")
    print(f"  Validation accuracy: {val_acc:.4f}")
    print(f"  Platt: slope={slope:.4f}, intercept={intercept:.4f}")
    db.close()


# ============================================================
# MODALITÀ 3: CALIBRATE (sola calibrazione su modello esistente)
# ============================================================
def mode_calibrate():
    """
    Platt Calibration sul modello XGBoost già addestrato.
    Non riaddestra il modello, solo la calibrazione delle probabilità.

    Perché serve: XGBoost tende a produrre probabilità estreme (vicine a 0 o 1).
    Platt scaling le "ammorbidisce" rendendole più oneste: un 80% deve vincere ~80% delle volte.
    Questo migliora l'edge detection perché evita falsi positivi su probabilità gonfiate.
    """
    db = TennisDatabase(DB_PATH)
    engine = TopSpinEngine(db, load_models=True)

    if not engine.xgb.winner_model:
        print("[ERRORE] winner_model non caricato. Esegui --mode train o --mode retrain prima.")
        db.close()
        return

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

    engine.elo_engine.load_all_ratings()

    raw_probs = []
    actuals = []
    total = len(val_matches)

    print("[INFO] Raccolta predizioni...")
    for i, m in enumerate(val_matches):
        try:
            match_date = date.fromisoformat(m["match_date"])

            # Flip casuale per avere entrambe le classi
            if np.random.random() < 0.5:
                p1_id, p2_id = m["winner_id"], m["loser_id"]
                y = 1
            else:
                p1_id, p2_id = m["loser_id"], m["winner_id"]
                y = 0

            for pid in (p1_id, p2_id):
                if pid in engine.elo_engine.ratings:
                    engine.elo_engine.ratings[pid].apply_decay(match_date)

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

    brier_raw = brier_score_loss(actuals, raw_probs)

    # Platt scaling
    print("\n[INFO] Fitting Platt scaling...")
    platts = LogisticRegression(C=1e10, solver='lbfgs')
    eps = 1e-7
    logit_probs = np.clip(raw_probs, eps, 1 - eps)
    X_logit = np.log(logit_probs / (1 - logit_probs)).reshape(-1, 1)
    platts.fit(X_logit, actuals)

    calib_probs = platts.predict_proba(X_logit)[:, 1]
    brier_cal = brier_score_loss(actuals, calib_probs)

    params = {
        "slope": float(platts.coef_[0][0]),
        "intercept": float(platts.intercept_[0]),
        "brier_raw": brier_raw,
        "brier_cal": brier_cal,
        "accuracy_raw": float(((raw_probs >= 0.5) == actuals).mean()),
        "accuracy_cal": float(((calib_probs >= 0.5) == actuals).mean()),
        "n_samples": len(raw_probs),
        "calibration_date": date.today().isoformat(),
    }

    cal_path = os.path.join(MODEL_DIR, "platt_calibration.json")
    with open(cal_path, "w") as f:
        json.dump(params, f, indent=2)

    print(f"\n[RISULTATI]")
    print(f"  Slope: {params['slope']:.4f}, Intercept: {params['intercept']:.4f}")
    print(f"  Brier raw: {brier_raw:.6f} → calibrato: {brier_cal:.6f}")
    print(f"  Accuracy raw: {params['accuracy_raw']:.4f} → calibrato: {params['accuracy_cal']:.4f}")

    # Reliability by decile
    print("\n--- Reliability Diagram ---")
    print(f"{'Bin':>6} {'N':>5} {'Prob media':>10} {'Freq':>12} {'Diff':>10}")
    print("-" * 45)
    for bin_idx in range(10):
        lo, hi = bin_idx * 0.1, (bin_idx + 1) * 0.1
        mask = (raw_probs >= lo) & (raw_probs < hi)
        n = mask.sum()
        if n > 0:
            print(f"  {lo:.0%}-{hi:.0%}  {n:5d}  {raw_probs[mask].mean():.4f}       {actuals[mask].mean():.4f}          {actuals[mask].mean() - raw_probs[mask].mean():+.4f}")

    db.close()
    print(f"\n[OK] Calibrazione salvata in {cal_path}")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    mode = "train"
    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]
        elif arg == "--mode" and len(sys.argv) > sys.argv.index(arg) + 1:
            idx = sys.argv.index(arg)
            mode = sys.argv[idx + 1]

    if mode == "train":
        mode_train()
    elif mode == "retrain":
        mode_retrain()
    elif mode == "calibrate":
        mode_calibrate()
    else:
        print(f"Modalità sconosciuta: {mode}")
        print("Usa: --mode train | retrain | calibrate")
        sys.exit(1)
