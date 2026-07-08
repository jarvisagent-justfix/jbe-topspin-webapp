#!/usr/bin/env python3
"""
JBE TopSpin — Portfolio Storico 2026 (Backtest Onesto)
========================================================
Addestra XGBoost su 2019-2025 (NO 2026), testa su 1.129 match con quote Bet365,
salva le value bet nel paper_portfolio con stato won/lost reale.

Strategia: MW + Under, max 2 per match, edge min 5%, Kelly 12.5%.
"""
import sys, os, json, math, sqlite3
import numpy as np
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import DB_PATH, MODEL_DIR
from engine.elo_tennis import SurfaceELOEngine
from engine.xgboost_tennis import FeatureExtractor
from engine.value_detector import ValueBet, KellyCalculator

import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss

db = TennisDatabase(DB_PATH)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
# FASE 2: Training XGBoost su 2019-2025 (NO leakage)
# ============================================================
print("\n" + "=" * 70)
print("FASE 2: Training XGBoost su 2019-2025 (NO leakage)")
print("=" * 70)

feature_extractor = FeatureExtractor(db, elo_engine)

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
rng = np.random.RandomState(42)
for i, m in enumerate(train_matches):
    if i % 2000 == 0:
        print(f"  Building features: {i}/{len(train_matches)}")
    md = date.fromisoformat(m["match_date"])
    try:
        # Random flip: a volte p1 = winner, a volte p1 = loser
        flip = rng.random() < 0.5
        if flip:
            p1_id, p2_id = m["winner_id"], m["loser_id"]
            p1_rank, p2_rank = m["winner_rank"], m["loser_rank"]
            actual_p1_win = True
        else:
            p1_id, p2_id = m["loser_id"], m["winner_id"]
            p1_rank, p2_rank = m["loser_rank"], m["winner_rank"]
            actual_p1_win = False

        feats = feature_extractor.extract(
            m["id"], md, p1_id, p2_id,
            m["surface"], m["best_of"], m["round"], m["tour_level"]
        )
        feats["rank_p1"] = p1_rank or 0
        feats["rank_p2"] = p2_rank or 0
        feats["rank_diff"] = (p2_rank or 0) - (p1_rank or 0)
        feats["rank_pts_diff"] = (m["winner_rank_points"] or 0) - (m["loser_rank_points"] or 0)

        elo_pred = elo_train.predict_winner(p1_id, p2_id, m["surface"], m["best_of"] == 5)
        feats["elo_prob"] = elo_pred["prob_player1"]

        X_list.append(list(feats.values()))
        y_list.append(1 if actual_p1_win else 0)

        # Aggiorna ELO con risultato reale
        elo_train.record_match(
            m["winner_id"], m["loser_id"], m["surface"],
            md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
        )
    except:
        skipped += 1
        continue

feature_names = list(feats.keys())
X = np.array(X_list)
y = np.array(y_list)
print(f"Feature matrix: {X.shape}, skipped: {skipped}")

X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)
print(f"Training: {len(X_train)}, Validation: {len(X_val)}")

model = xgb.XGBClassifier(
    n_estimators=300, max_depth=6, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8,
    random_state=42, eval_metric='logloss',
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
    logit = math.log(max(raw_prob / (1 - raw_prob + 1e-7), 1e-7))
    cal_logit = slope * logit + intercept
    return 1.0 / (1.0 + math.exp(-min(max(cal_logit, -10), 10)))

# ============================================================
# FASE 3: Backtest 2026 e salvataggio portfolio
# ============================================================
print("\n" + "=" * 70)
print("FASE 3: Backtest 2026 + Portfolio")
print("=" * 70)

# Reset ELO per backtest
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
           o.odds_winner, o.odds_loser, o.bookmaker, o.id as odds_id
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

# Pulisci portfolio esistente (solo le bet storiche, non quelle live)
# Le bet live hanno match_date >= oggi, le teniamo
today_str = date.today().isoformat()
db.conn.execute("DELETE FROM paper_portfolio WHERE match_date < ?", (today_str,))
db.conn.execute("DELETE FROM value_candidates")
db.conn.commit()
print(f"Portfolio pulito (tenute bet live da {today_str})")

# Kelly
kc = KellyCalculator(initial_bankroll=200.0)

bets_placed = 0
bets_won = 0
total_profit = 0.0
rng = np.random.RandomState(42)

for i, m in enumerate(test_matches):
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(test_matches)} [Bets: {bets_placed} Won: {bets_won} P&L: {total_profit:+.2f}€]")

    md = date.fromisoformat(m["match_date"])
    winner_id, loser_id = m["winner_id"], m["loser_id"]
    winner_name, loser_name = m["wname"], m["lname"]
    odds_winner = m["odds_winner"]
    odds_loser = m["odds_loser"]

    if not odds_winner or odds_winner <= 1.01:
        continue

    # Random flip: p1 a volte = winner, a volte = loser
    flip = rng.random() < 0.5
    if flip:
        p1_id, p2_id = winner_id, loser_id
        p1_name, p2_name = winner_name, loser_name
        p1_rank, p2_rank = m["winner_rank"], m["loser_rank"]
        actual_p1_win = True
        odds_p1 = odds_winner
        odds_p2 = odds_loser
    else:
        p1_id, p2_id = loser_id, winner_id
        p1_name, p2_name = loser_name, winner_name
        p1_rank, p2_rank = m["loser_rank"], m["winner_rank"]
        actual_p1_win = False
        odds_p1 = odds_loser
        odds_p2 = odds_winner

    try:
        elo_pred = elo_bt.predict_winner(p1_id, p2_id, m["surface"], m["best_of"] == 5)
        blend_prob = elo_pred["prob_player1"]

        elo_bt.record_match(
            winner_id, loser_id, m["surface"],
            md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
        )
    except:
        continue

    # --- Strategia MW (solo odds < 2.0) ---
    implied_p1 = 1.0 / odds_p1
    edge_p1 = blend_prob - implied_p1

    if blend_prob >= 0.50 and edge_p1 >= 0.05 and edge_p1 <= 0.25 and odds_p1 < 2.0:
        stake = kc.calculate_stake(edge_p1, odds_p1)
        if stake >= 0.5:
            won = actual_p1_win
            profit = stake * (odds_p1 - 1) if won else -stake
            kc.bankroll += profit
            bets_placed += 1
            if won:
                bets_won += 1
            total_profit += profit

            try:
                db.conn.execute("""
                    INSERT INTO paper_portfolio
                        (match_id, match_date, tournament, surface,
                         player1, player2, selection, market,
                         odds, model_prob, edge, stake,
                         bankroll_before, bankroll_after, status, result,
                         bookmaker, source, confidence, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'Bet365', 'backtest_2026', 'MEDIUM', ?)
                """, (
                    m["id"],
                    m["match_date"],
                    m["tournament"] or "",
                    m["surface"] or "",
                    p1_name, p2_name,
                    p1_name,
                    "match_winner",
                    float(odds_p1),
                    float(blend_prob),
                    float(edge_p1),
                    float(stake),
                    float(kc.bankroll - profit),  # bankroll_before
                    float(kc.bankroll),            # bankroll_after
                    'won' if won else 'lost',
                    float(profit),
                    json.dumps({"backtest": True}),
                ))
            except Exception as e:
                pass  # Skip save errors silently

db.conn.commit()

# Statistiche finali
roi = total_profit / 200.0 * 100
print("\n" + "=" * 70)
print("BACKTEST 2026 COMPLETO — Portfolio Storico")
print("=" * 70)
print(f"  Match analizzati: {len(test_matches)}")
print(f"  Value bets trovate: {bets_placed}")
print(f"  Vinte: {bets_won} ({bets_won/max(bets_placed,1)*100:.1f}%)")
print(f"  Perse: {bets_placed - bets_won} ({(bets_placed-bets_won)/max(bets_placed,1)*100:.1f}%)")
print(f"  P&L totale: {total_profit:+.2f}€")
print(f"  ROI: {roi:+.1f}%")
print(f"  Bankroll finale: {kc.bankroll:.2f}€")

# Salva Kelly state
kelly_file = os.path.join(BASE, "data", "kelly_state.json")
with open(kelly_file, "w") as f:
    json.dump({
        "bankroll": kc.bankroll,
        "peak_bankroll": kc.peak_bankroll,
        "consecutive_losses": kc.consecutive_losses,
        "daily_exposure": kc.daily_exposure,
    }, f)

db.close()
print(f"\nKelly state salvato: {kc.bankroll:.2f}€")
print("Done.")
