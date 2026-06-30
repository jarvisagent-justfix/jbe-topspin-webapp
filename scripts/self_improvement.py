#!/usr/bin/env python3
"""
JBE TopSpin — Self-Improvement Loop
=====================================
1. Analizza prediction_errors per slice (superficie, torneo, range quota)
2. Calcola bias per ogni slice e salva in bias_corrections
3. Se errori >= 100 da ultimo retrain, avvia retrain XGBoost
4. Opzionalmente riaddestra il modello

Uso: PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/self_improvement.py [--retrain]
"""
import sys, os, json, math
import numpy as np
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from engine.xgboost_tennis import TopSpinEngine, XGBoostTrainer
from config import DB_PATH, MODEL_DIR, XGB_RETRAIN_EVERY

# All debug output to stderr - cron no_agent delivers only stdout
def log(msg: str = ""):
    print(msg, file=sys.stderr)

LAST_RETRAIN_FILE = os.path.join(MODEL_DIR, "last_retrain.json")


def compute_bias_by_slice(db, slice_type: str, slice_values: list) -> dict:
    """
    Calcola il bias per uno slice specifico.
    bias = actual_freq - mean_prob  (positivo = modello sottostima, negativo = sovrastima)
    
    Returns:
        dict {slice_value: {"bias": float, "n": int, "accuracy": float}}
    """
    results = {}
    # Whitelist colonne consentite per prevenire SQL injection
    ALLOWED_SLICES = {"surface", "tour_level", "round"}
    if slice_type not in ALLOWED_SLICES:
        log(f"  [WARN] Slice type '{slice_type}' non valida. Skipping.")
        return results
    
    for sv in slice_values:
        rows = db.conn.execute(f"""
            SELECT pred_prob, winner_correct
            FROM prediction_errors
            WHERE {slice_type} = ? AND winner_correct IS NOT NULL
        """, (sv,)).fetchall()

        if len(rows) < 5:
            continue  # Troppi pochi campioni

        probs = [r["pred_prob"] for r in rows]
        actuals = [r["winner_correct"] for r in rows]
        mean_prob = np.mean(probs)
        actual_freq = np.mean(actuals)
        accuracy = sum(actuals) / len(actuals)
        n = len(rows)

        results[sv] = {
            "bias": round(actual_freq - mean_prob, 4),
            "n": n,
            "accuracy": round(accuracy * 100, 1),
            "mean_prob": round(mean_prob, 4),
            "actual_freq": round(actual_freq, 4),
        }

    return results


def run_analysis(db):
    """Analizza tutti gli errori per slice e salva bias_corrections."""
    log("[INFO] Self-Improvement: analisi errori per slice...")

    # Quanti errori abbiamo?
    total = db.conn.execute(
        "SELECT COUNT(*) FROM prediction_errors WHERE winner_correct IS NOT NULL"
    ).fetchone()[0]

    if total < 10:
        log(f"  Troppi pochi errori ({total}) per un'analisi significativa.")
        return False

    correct = db.conn.execute(
        "SELECT COUNT(*) FROM prediction_errors WHERE winner_correct=1"
    ).fetchone()[0]
    wrong = total - correct
    accuracy = correct / total * 100
    log(f"  {total} errori totali ({correct} OK, {wrong} KO) — accuracy {accuracy:.1f}%")

    # Slice definition
    slices = {
        "surface": ["Hard", "Clay", "Grass", "Carpet"],
        "tour_level": ["G", "M", "A", "C", "F"],
        "round": ["R128", "R64", "R32", "R16", "QF", "SF", "F", "RR"],
    }

    updates = 0
    for slice_type, slice_values in slices.items():
        results = compute_bias_by_slice(db, slice_type, slice_values)
        for sv, r in results.items():
            # Salviamo bias = actual_freq - mean_prob
            # Questo valore verra' usato per correggere le probabilita' future
            db.conn.execute("""
                INSERT INTO bias_corrections (slice_type, slice_value, bias, n_errors, last_updated)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(slice_type, slice_value) DO UPDATE SET
                    bias=excluded.bias,
                    n_errors=excluded.n_errors,
                    last_updated=CURRENT_TIMESTAMP
            """, (slice_type, sv, r["bias"], r["n"]))
            updates += 1

            if abs(r["bias"]) > 0.05:
                direction = "sottostima" if r["bias"] > 0 else "sovrastima"
                log(f"  ⚠ {slice_type}={sv}: bias={r['bias']:+.4f} ({direction}), n={r['n']}, acc={r['accuracy']:.1f}%")

    db.conn.commit()
    log(f"  Bias salvati: {updates} slice")

    # Odds-range specific bias
    odds_bias = {}
    rows = db.conn.execute("""
        SELECT best_odds_winner, winner_correct, pred_prob
        FROM prediction_errors
        WHERE winner_correct IS NOT NULL AND best_odds_winner IS NOT NULL
    """).fetchall()

    if rows:
        # Divide per range quota: <1.5, 1.5-2.0, 2.0-3.0, 3.0-5.0, >5.0
        ranges = [(0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 999)]
        for lo, hi in ranges:
            subset = [r for r in rows if lo <= r["best_odds_winner"] < hi]
            if len(subset) >= 5:
                probs = [r["pred_prob"] for r in subset]
                actuals = [r["winner_correct"] for r in subset]
                bias = np.mean(actuals) - np.mean(probs)
                db.conn.execute("""
                    INSERT INTO bias_corrections (slice_type, slice_value, bias, n_errors, last_updated)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(slice_type, slice_value) DO UPDATE SET
                        bias=excluded.bias, n_errors=excluded.n_errors, last_updated=CURRENT_TIMESTAMP
                """, ("odds_range", f"{lo}-{hi}", round(bias, 4), len(subset)))
                if abs(bias) > 0.05:
                    log(f"  ⚠ odds_range {lo}-{hi}: bias={bias:+.4f}, n={len(subset)}")

        db.conn.commit()

    return True


def get_retrain_needed(db):
    """Verifica se serve un retrain XGBoost."""
    total = db.conn.execute(
        "SELECT COUNT(*) FROM prediction_errors WHERE winner_correct IS NOT NULL"
    ).fetchone()[0]

    # Leggi ultimo retrain
    last_count = 0
    if os.path.exists(LAST_RETRAIN_FILE):
        try:
            with open(LAST_RETRAIN_FILE) as f:
                data = json.load(f)
                last_count = data.get("error_count_at_retrain", 0)
        except Exception:
            pass

    new_errors_since_retrain = total - last_count
    log(f"\n  Errori da ultimo retrain: {new_errors_since_retrain} (soglia: {XGB_RETRAIN_EVERY})")
    return new_errors_since_retrain >= XGB_RETRAIN_EVERY


def do_retrain(db):
    """Esegue il retrain XGBoost sui dati piu' recenti."""
    log("\n[RETRAIN] Avvio retrain XGBoost...")
    trainer = XGBoostTrainer(db)

    # Carica ELO persistito
    from engine.elo_tennis import SurfaceELOEngine
    elo_engine = SurfaceELOEngine(db)
    elo_engine.load_all_ratings()
    trainer.elo_engine = elo_engine

    # Training su 2024-2026 (dati recenti)
    X, y_winner, y_games, y_sets, feature_names = trainer.build_training_data(
        "2024-01-01", (date.today() - timedelta(days=5)).isoformat()
    )

    if len(X) < 200:
        log(f"  Dati insufficienti: {len(X)} campioni. Skip retrain.")
        return False

    from sklearn.model_selection import train_test_split
    X_train, X_val, y_w_train, y_w_val = train_test_split(
        X, y_winner, test_size=0.2, random_state=42
    )

    val_acc = trainer.train_winner_model(X_train, y_w_train, X_val, y_w_val)
    trainer.save_models(prefix="topspin")

    # Salva ultimo retrain
    total = db.conn.execute(
        "SELECT COUNT(*) FROM prediction_errors WHERE winner_correct IS NOT NULL"
    ).fetchone()[0]
    with open(LAST_RETRAIN_FILE, "w") as f:
        json.dump({
            "error_count_at_retrain": total,
            "val_accuracy": float(val_acc),
            "retrain_date": date.today().isoformat(),
            "n_train": len(X),
        }, f, indent=2)

    log(f"  [OK] Retrain completato. Validation accuracy: {val_acc:.4f}")
    log(f"  Error count at retrain: {total}")
    return True


def run_self_improvement(db=None, do_retrain_if_needed=True):
    """Esegue l'intero loop di self-improvement."""
    own_db = db is None
    if own_db:
        db = TennisDatabase(DB_PATH)

    log("=" * 60)
    log("  JBE TopSpin — Self-Improvement Loop")
    log("=" * 60)

    # 1. Fill pending results (implementazione inline per evitare import circolare)
    filled = 0
    errors = db.conn.execute("""
        SELECT pe.id, pe.match_id, pe.pred_winner_id
        FROM prediction_errors pe
        WHERE pe.winner_correct IS NULL
    """).fetchall()
    for e in errors:
        match = db.conn.execute(
            "SELECT winner_id FROM tennis_matches WHERE id=?",
            (e["match_id"],)
        ).fetchone()
        if match and match["winner_id"]:
            correct = 1 if match["winner_id"] == e["pred_winner_id"] else 0
            db.conn.execute("""
                UPDATE prediction_errors
                SET winner_correct=?, actual_winner_id=?
                WHERE id=?
            """, (correct, match["winner_id"], e["id"]))
            filled += 1
    if filled:
        db.conn.commit()
        log(f"  Prediction results filled: {filled}")

    # 2. Analizza bias per slice
    analysis_ok = run_analysis(db)

    # 3. Verifica se serve retrain
    if do_retrain_if_needed and analysis_ok:
        if get_retrain_needed(db):
            do_retrain(db)
        else:
            log("  Retrain non necessario.")

    if own_db:
        db.close()

    log("=" * 60)
    log("  Self-Improvement completato.")
    log("=" * 60)
    return True


if __name__ == "__main__":
    do_retrain_flag = "--retrain" in sys.argv
    db = TennisDatabase(DB_PATH)
    run_self_improvement(db, do_retrain_if_needed=do_retrain_flag)
    db.close()
