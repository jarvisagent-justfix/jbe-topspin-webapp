#!/usr/bin/env python3
"""
JBE TopSpin — Backtest Strategy Comparison: Max-2, MW-only, Under-only
======================================================================
Compare 3 strategie su dati reali 2026 (ELO warm-up 2019-2025).
Simula budget 200€ con Kelly 12.5%.
Fix rispetto a backtest_strategy.py: odds NON invertiti, no random flip.
"""
import sys, os, json, math
import numpy as np
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import TennisDatabase
from config import DB_PATH, MODEL_DIR, KELLY_FRACTION, MAX_STAKE_PCT, MIN_EDGE, MIN_CONFIDENCE
from engine.elo_tennis import SurfaceELOEngine
from engine.xgboost_tennis import FeatureExtractor

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
# FASE 2: Carica XGBoost
# ============================================================
print("\n" + "=" * 70)
print("FASE 2: Backtest 2026 con Quote Reali (Corretto)")
print("=" * 70)

winner_model = xgb.XGBClassifier()
model_path = os.path.join(MODEL_DIR, "topspin_winner.json")
if os.path.exists(model_path):
    winner_model.load_model(model_path)
    print("XGBoost model loaded.")
else:
    print("[ERROR] Model not found.")
    sys.exit(1)

feature_extractor = FeatureExtractor(db, elo_engine)

# Get 2026 matches with Bet365 odds
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
total = 0
correct_ensemble = 0

all_details = []  # Per analisi dettagliata

for i, m in enumerate(test_matches):
    md = date.fromisoformat(m["match_date"])
    winner_id, loser_id = m["winner_id"], m["loser_id"]

    # Deterministic: p1 = primo giocatore nella query (id minore non garantito)
    # Usiamo winner/loser direttamente: p1 = vincitore reale, p2 = perdente
    # Così odds_winner = odds per p1, odds_loser = odds per p2
    p1_id, p2_id = winner_id, loser_id
    p1_name, p2_name = m["wname"], m["lname"]
    actual_p1_win = True  # p1 è il winner reale
    odds_p1 = m["odds_winner"]  # odds per chi ha vinto
    odds_p2 = m["odds_loser"]   # odds per chi ha perso

    if not odds_p1 or odds_p1 <= 1.01:
        continue

    try:
        # ELO prediction
        elo_pred = elo_engine.predict_winner(p1_id, p2_id, m["surface"], m["best_of"] == 5)
        elo_prob_p1 = elo_pred["prob_player1"]
        elo_correct = (elo_prob_p1 >= 0.5) == actual_p1_win

        # XGBoost prediction
        feats = feature_extractor.extract(
            m["id"], md, p1_id, p2_id,
            m["surface"], m["best_of"], m["round"], m["tour_level"]
        )
        feats["rank_p1"] = m["winner_rank"] or 0
        feats["rank_p2"] = m["loser_rank"] or 0
        feats["rank_diff"] = (m["loser_rank"] or 0) - (m["winner_rank"] or 0)
        feats["rank_pts_diff"] = 0

        X_test = np.array([list(feats.values())])
        xgb_prob_p1 = winner_model.predict_proba(X_test)[0][1]

        # Ensemble blend
        blend_prob = 0.3 * elo_prob_p1 + 0.7 * xgb_prob_p1
        ensemble_correct = (blend_prob >= 0.5) == actual_p1_win
        if ensemble_correct:
            correct_ensemble += 1

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

    if (i + 1) % 200 == 0:
        print(f"  {i+1}/{len(test_matches)} [Ensemble: {correct_ensemble/max(total,1)*100:.1f}%]")

# ============================================================
# STRATEGIA HYBRID: Max 2 bet per match, mercati diversi
# ============================================================
print("\n" + "=" * 70)
print("STRATEGIA A — Max 2 per match (MW + Under)")
print("Imita la logica di predict_and_find_value: max 1 MW + 1 Under per match")
print("=" * 70)

# Reset ELO
elo_engine2 = SurfaceELOEngine(db)
for m in warmup:
    md = date.fromisoformat(m["match_date"])
    elo_engine2.record_match(
        m["winner_id"], m["loser_id"], m["surface"],
        md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
    )

feature_extractor2 = FeatureExtractor(db, elo_engine2)

BANKROLL = 200.0
KELLY = 0.125
MAX_STAKE = 0.05

def simulate_strategy(name, filter_type, matches):
    """
    filter_type: 'hybrid' = max 2 (MW best + Under best)
                 'mw_only' = solo Match Winner
                 'under_only' = solo Under
    """
    bankroll = 200.0
    peak = 200.0
    bets_placed = 0
    bets_won = 0
    total_stake = 0.0
    total_profit = 0.0
    max_dd = 0.0
    consecutive_losses = 0
    max_cl = 0
    daily_pnl = {}

    for m in matches:
        md = date.fromisoformat(m["match_date"])
        winner_id, loser_id = m["winner_id"], m["loser_id"]
        p1_id, p2_id = winner_id, loser_id
        p1_name, p2_name = m["wname"], m["lname"]
        actual_p1_win = True
        odds_p1 = m["odds_winner"]
        odds_p2 = m["odds_loser"]

        if not odds_p1 or odds_p1 <= 1.01:
            continue

        try:
            elo_pred = elo_engine2.predict_winner(p1_id, p2_id, m["surface"], m["best_of"] == 5)
            elo_prob_p1 = elo_pred["prob_player1"]

            feats = feature_extractor2.extract(
                m["id"], md, p1_id, p2_id,
                m["surface"], m["best_of"], m["round"], m["tour_level"]
            )
            feats["rank_p1"] = m["winner_rank"] or 0
            feats["rank_p2"] = m["loser_rank"] or 0
            feats["rank_diff"] = (m["loser_rank"] or 0) - (m["winner_rank"] or 0)
            feats["rank_pts_diff"] = 0

            X_test = np.array([list(feats.values())])
            xgb_prob_p1 = winner_model.predict_proba(X_test)[0][1]
            blend_prob = 0.3 * elo_prob_p1 + 0.7 * xgb_prob_p1

            elo_engine2.record_match(
                winner_id, loser_id, m["surface"],
                md, m["best_of"] == 5, m["w_games"] or 0, m["l_games"] or 0
            )

        except Exception:
            continue

        # --- CANDIDATE BETS (MW) ---
        bets = []
        prob_p1 = blend_prob
        prob_p2 = 1 - blend_prob

        # MW: p1 = winner reale, odds_p1 = odds per il winner
        # Il modello dice prob_p1 = prob che p1 (winner) vinca
        # Se prob_p1 >= 0.5, il modello prevede p1 (il winner reale)
        # In value betting, vogliamo prob_model > 1/odds
        implied_p1 = 1.0 / odds_p1
        edge_p1 = prob_p1 - implied_p1

        implied_p2 = 1.0 / odds_p2
        edge_p2 = prob_p2 - implied_p2

        # Scommessa su p1 (il winner reale)
        if prob_p1 >= 0.50 and edge_p1 >= MIN_EDGE and edge_p1 <= 0.25:
            stake = bankroll * KELLY * edge_p1 / (odds_p1 - 1)
            stake = min(stake, bankroll * MAX_STAKE)
            if stake >= 0.5:
                bets.append({
                    "market": "match_winner",
                    "selection": p1_name,
                    "odds": odds_p1,
                    "edge": edge_p1,
                    "stake": stake,
                    "won": True,  # p1 = winner reale
                    "prob": prob_p1,
                })

        # Scommessa su p2 (perdente reale)
        if prob_p2 >= 0.50 and edge_p2 >= MIN_EDGE and edge_p2 <= 0.25:
            stake = bankroll * KELLY * edge_p2 / (odds_p2 - 1)
            stake = min(stake, bankroll * MAX_STAKE)
            if stake >= 0.5:
                bets.append({
                    "market": "match_winner",
                    "selection": p2_name,
                    "odds": odds_p2,
                    "edge": edge_p2,
                    "stake": stake,
                    "won": False,  # p2 = perdente reale
                    "prob": prob_p2,
                })

        # --- SIMULA UNDER (solo se abbiamo un modello games) ---
        # Per simulazione Under, usiamo l'edge medio O/U osservato nei dati reali
        # Non abbiamo odds O/U nel DB, ma possiamo simulare uno scenario realistico
        # basato sui dati: Under ha WR ~52% con edge medio ~8-12%
        # Per ora: simula Under SOLO se abbiamo un modello games valido
        # Saltiamo per ora — torneremo su questo quando avremo odds O/U

        if not bets:
            continue

        # --- APPLICA FILTRO STRATEGIA ---
        selected = []
        if filter_type == 'mw_only':
            # Solo MW, prendi la migliore
            bets.sort(key=lambda b: -b["edge"])
            if bets:
                selected = [bets[0]]

        elif filter_type == 'under_only':
            # Solo Under (non abbiamo dati O/U reali qui, skip)
            continue

        elif filter_type == 'hybrid':
            # Max 2, mercati diversi
            # Per hybrid, al momento abbiamo solo MW bets nel DB
            # Quindi è equivalente a mw_only
            bets.sort(key=lambda b: -b["edge"])
            if bets:
                selected = [bets[0]]

        # --- ESECUZIONE BET ---
        for b in selected:
            profit = b["stake"] * (b["odds"] - 1) if b["won"] else -b["stake"]
            bankroll += profit
            bets_placed += 1
            if b["won"]:
                bets_won += 1
                consecutive_losses = 0
            else:
                consecutive_losses += 1
                if consecutive_losses > max_cl:
                    max_cl = consecutive_losses

            total_stake += b["stake"]
            total_profit += profit
            if bankroll > peak:
                peak = bankroll
            dd = (peak - bankroll) / peak * 100
            if dd > max_dd:
                max_dd = dd

    roi_total = (bankroll - 200.0) / 200.0 * 100
    roi_stake = total_profit / total_stake * 100 if total_stake > 0 else 0

    return {
        "name": name,
        "bankroll_finale": round(bankroll, 2),
        "profit": round(bankroll - 200.0, 2),
        "roi_totale": round(roi_total, 1),
        "bets": bets_placed,
        "won": bets_won,
        "winrate": round(bets_won / max(bets_placed, 1) * 100, 1),
        "stake_totale": round(total_stake, 2),
        "roi_stake": round(roi_stake, 1),
        "max_drawdown": round(max_dd, 1),
        "max_consecutive_losses": max_cl,
        "profit_per_bet": round(total_profit / max(bets_placed, 1), 2),
    }

# Esegui tutte le strategie
strategies = [
    ("Max-2 (MW + Under)", "hybrid"),
    ("MW Only", "mw_only"),
    ("Under Only", "under_only"),
]

results = []
for name, ftype in strategies:
    print(f"\n--- {name} ---")
    r = simulate_strategy(name, ftype, test_matches)
    results.append(r)
    print(f"  Bets: {r['bets']} | Won: {r['won']} ({r['winrate']}%)")
    print(f"  Profit: {r['profit']:+.2f}€ | ROI: {r['roi_totale']:+.1f}%")
    print(f"  ROI/stake: {r['roi_stake']:+.1f}% | DD: {r['max_drawdown']}%")
    print(f"  Max consecutive losses: {r['max_consecutive_losses']}")

# ============================================================
# REPORT FINALE
# ============================================================
print("\n" + "=" * 70)
print("JBE TopSpin — Backtest Strategie 2026 (Budget 200€ Kelly 12.5%)")
print("=" * 70)
print(f"\n  Match testati: {total}")
print(f"  Ensemble accuracy: {correct_ensemble/max(total,1)*100:.1f}%")
print()

for r in results:
    if r["bets"] == 0:
        print(f"  {r['name']:25s}: Nessuna bet (dati insufficienti per O/U/GH)")
        continue
    print(f"  {r['name']:25s}:")
    print(f"    Bet:      {r['bets']:4d} | Vinte: {r['won']:4d} ({r['winrate']}%)")
    print(f"    P&L:      {r['profit']:+8.2f}€ | ROI: {r['roi_totale']:+6.1f}%")
    print(f"    Stake:    {r['stake_totale']:>8.2f}€ | ROI stake: {r['roi_stake']:+6.1f}%")
    print(f"    DD max:   {r['max_drawdown']:>6.1f}% | Max streak loss: {r['max_consecutive_losses']}")
    print(f"    Profitto/bet: {r['profit_per_bet']:+.2f}€")
    print()

db.close()
print("Backtest completato.")
