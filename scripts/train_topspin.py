"""
JBE TopSpin — Script di Training e Backtest

Addestra XGBoost su dati storici, esegue backtest walk-forward
e valuta le performance per strato e per mercato.
"""
import sys
import os
import numpy as np
from datetime import date, timedelta
from sklearn.model_selection import train_test_split

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from engine.xgboost_tennis import XGBoostTrainer, TopSpinEngine
from config import MODEL_DIR


def train_walk_forward():
    """
    Addestramento walk-forward: train 5 anni, test 1 anno.
    Finestre: 2016-2020 > 2021, 2017-2021 > 2022, ...
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

        # Reset ELO per ogni finestra
        trainer.elo_engine = __import__('engine.elo_tennis', fromlist=['SurfaceELOEngine']).SurfaceELOEngine(db)

        # Build training data
        print("Costruzione training data...")
        X, y_winner, y_games, y_sets, feature_names = trainer.build_training_data(train_start, train_end)
        
        if len(X) < 100:
            print(f"Dati insufficienti: {len(X)} campioni")
            continue

        # Split train/val
        X_train, X_val, y_w_train, y_w_val, y_g_train, y_g_val, y_s_train, y_s_val = train_test_split(
            X, y_winner, y_games, y_sets, test_size=0.2, random_state=42
        )

        # Train models
        val_acc = trainer.train_winner_model(X_train, y_w_train, X_val, y_w_val)
        trainer.train_games_model(X_train, y_g_train, X_val, y_g_val)
        trainer.train_sets_model(X_train, y_s_train, X_val, y_s_val)

        # Test on out-of-sample
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
                
                # Predict with ELO
                elo_pred = trainer.elo_engine.predict_winner(
                    m["winner_id"], m["loser_id"], m["surface"], m["best_of"] == 5
                )
                pred_elo = m["winner_id"] if elo_pred["prob_player1"] > 0.5 else m["loser_id"]
                if pred_elo == m["winner_id"]:
                    correct_elo += 1

                # Predict with XGBoost
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

                # Record match for future
                trainer.elo_engine.record_match(
                    m["winner_id"], m["loser_id"], m["surface"],
                    match_date, m["best_of"] == 5,
                    m["w_games"] or 0, m["l_games"] or 0
                )

            except Exception as e:
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

        print(f"\nRisultati {test_start[:4]}-{test_end[:4]}:")
        print(f"  ELO solo:     {acc_elo:.1f}%")
        print(f"  XGBoost:      {acc_xgb:.1f}%")
        print(f"  Improvement:  {acc_xgb - acc_elo:+.1f}pp")

        # Salva modello dopo ogni finestra
        trainer.save_models(prefix=f"topspin_{test_end[:4]}")

    # Summary
    print(f"\n{'='*60}")
    print("RIEPILOGO BACKTEST WALK-FORWARD")
    print(f"{'='*60}")
    print(f"{'Window':>15} {'Train':>8} {'Test':>8} {'ELO':>8} {'XGB':>8} {'+/-':>8}")
    print("-" * 60)
    for r in all_results:
        print(f"{r['window']:>15} {r['n_train']:>8} {r['n_test']:>8} {r['acc_elo']:>7.1f}% {r['acc_xgb']:>7.1f}% {r['improvement']:>+7.1f}pp")

    # Train final model on ALL data
    print(f"\nTraining modello finale su tutti i dati (2001-2024)...")
    trainer.elo_engine = __import__('engine.elo_tennis', fromlist=['SurfaceELOEngine']).SurfaceELOEngine(db)
    X_all, y_all, _, _, _ = trainer.build_training_data("2001-01-01", "2025-01-01")
    
    X_train, X_val, y_train, y_val = train_test_split(X_all, y_all, test_size=0.2, random_state=42)
    trainer.train_winner_model(X_train, y_train, X_val, y_val)
    trainer.save_models(prefix="topspin_final")
    
    print(f"\nModello finale salvato con accuracy validation: {val_acc:.4f}")

    db.close()


if __name__ == "__main__":
    train_walk_forward()
