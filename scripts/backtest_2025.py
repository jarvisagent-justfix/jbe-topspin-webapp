#!/usr/bin/env python3
"""
JBE TopSpin — Comprehensive 2025 Backtest
=========================================
Walk-forward backtest on all 2025 ATP matches with Bet365/Pinnacle odds.

Methodology:
  1. ELO warm-up: 2019-01-01 to 2024-12-31 (all matches with results)
  2. For each 2025 match with Bet365 or Pinnacle odds:
     a. Predict using TopSpinEngine.predict() (ELO + XGBoost + Platt calibration + bias corrections)
     b. Predict Markov models for game handicap and O/U probabilities
     c. Detect value bets on 3 markets with appropriate edge thresholds
     d. Track Kelly 12.5% (capped 5% BR) AND flat 2% BR betting
     e. Record match result in ELO (walk-forward)
  3. Compute comprehensive metrics
  4. Output detailed report with best/worst 10 bets

Usage:
  PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/backtest_2025.py
"""
import sys, os, json, math, random
from datetime import date, datetime
from collections import defaultdict
from typing import List, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from database import TennisDatabase
from config import DB_PATH, MODEL_DIR
from engine.elo_tennis import SurfaceELOEngine
from engine.xgboost_tennis import TopSpinEngine

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
KELLY_FRACTION = 0.125       # 12.5% Kelly fraction
MAX_STAKE_PCT = 0.05         # 5% of bankroll cap
FLAT_STAKE_PCT = 0.02        # 2% flat stake
INITIAL_BANKROLL = 10000.0   # 10,000 EUR virtual bankroll

# Edge thresholds per market
EDGE_MW = 0.05   # match_winner >= 5%
EDGE_GH = 0.08   # game_handicap >= 8%
EDGE_OU = 0.08   # over_under >= 8%

MIN_CONFIDENCE = 0.50  # minimum model confidence for any bet

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─────────────────────────────────────────────
# Backtesting Engine
# ─────────────────────────────────────────────
class BacktestBet:
    """Record of a single bet placed during backtest."""
    __slots__ = (
        "match_id", "match_date", "tournament", "surface", "tour_level", "round_val",
        "market", "selection", "player1_name", "player2_name",
        "odds", "model_prob", "implied_prob", "edge",
        "stake_kelly", "stake_flat", "actual_won", "profit_kelly", "profit_flat",
        "winner_name",
    )

    def __init__(self, match_id, match_date, tournament, surface, tour_level, round_val,
                 market, selection, player1_name, player2_name,
                 odds, model_prob, edge,
                 stake_kelly, stake_flat, actual_won, profit_kelly, profit_flat,
                 winner_name):
        self.match_id = match_id
        self.match_date = match_date
        self.tournament = tournament
        self.surface = surface
        self.tour_level = tour_level
        self.round_val = round_val
        self.market = market
        self.selection = selection
        self.player1_name = player1_name
        self.player2_name = player2_name
        self.odds = odds
        self.model_prob = model_prob
        self.implied_prob = 1.0 / odds if odds > 1 else 0
        self.edge = edge
        self.stake_kelly = stake_kelly
        self.stake_flat = stake_flat
        self.actual_won = actual_won
        self.profit_kelly = profit_kelly
        self.profit_flat = profit_flat
        self.winner_name = winner_name

    def __repr__(self):
        return (f"{self.match_date} | {self.player1_name} vs {self.player2_name} "
                f"({self.surface}) | {self.market}: {self.selection} @{self.odds:.2f} "
                f"| edge={self.edge:.1%} | {'WON' if self.actual_won else 'LOST'}")


class BankrollTracker:
    """Tracks bankroll across multiple betting strategies."""
    def __init__(self, initial: float):
        self.initial = initial
        self.kelly_br = initial
        self.flat_br = initial
        self.kelly_peak = initial
        self.flat_peak = initial
        self.kelly_drawdown = 0.0
        self.flat_drawdown = 0.0
        self.kelly_returns = []  # daily PnL series for Sharpe
        self.flat_returns = []
        self.current_date = None
        self.daily_kelly_pnl = 0.0
        self.daily_flat_pnl = 0.0

    def update_date(self, match_date):
        """Reset daily accumulator when date changes."""
        if self.current_date is not None and match_date != self.current_date:
            if abs(self.daily_kelly_pnl) > 0.001:
                self.kelly_returns.append(self.daily_kelly_pnl)
            if abs(self.daily_flat_pnl) > 0.001:
                self.flat_returns.append(self.daily_flat_pnl)
            self.daily_kelly_pnl = 0.0
            self.daily_flat_pnl = 0.0
        self.current_date = match_date

    def record_result(self, stake_kelly: float, profit_kelly: float,
                      stake_flat: float, profit_flat: float, match_date):
        self.update_date(match_date)

        self.kelly_br += profit_kelly
        self.flat_br += profit_flat
        self.daily_kelly_pnl += profit_kelly
        self.daily_flat_pnl += profit_flat

        if self.kelly_br > self.kelly_peak:
            self.kelly_peak = self.kelly_br
        if self.flat_br > self.flat_peak:
            self.flat_peak = self.flat_br

        kelly_dd = (self.kelly_peak - self.kelly_br) / self.kelly_peak * 100
        flat_dd = (self.flat_peak - self.flat_br) / self.flat_peak * 100
        if kelly_dd > self.kelly_drawdown:
            self.kelly_drawdown = kelly_dd
        if flat_dd > self.flat_drawdown:
            self.flat_drawdown = flat_dd

    def finalize(self):
        """Flush last day's PnL."""
        if abs(self.daily_kelly_pnl) > 0.001:
            self.kelly_returns.append(self.daily_kelly_pnl)
        if abs(self.daily_flat_pnl) > 0.001:
            self.flat_returns.append(self.daily_flat_pnl)

    @property
    def kelly_roi_total(self):
        return (self.kelly_br - self.initial) / self.initial * 100

    @property
    def flat_roi_total(self):
        return (self.flat_br - self.initial) / self.initial * 100

    @property
    def kelly_sharpe(self):
        if len(self.kelly_returns) < 2:
            return 0.0
        mean_r = sum(self.kelly_returns) / len(self.kelly_returns)
        var_r = sum((r - mean_r) ** 2 for r in self.kelly_returns) / (len(self.kelly_returns) - 1)
        if var_r <= 0:
            return 0.0
        return mean_r / math.sqrt(var_r) * math.sqrt(252)  # annualized

    @property
    def flat_sharpe(self):
        if len(self.flat_returns) < 2:
            return 0.0
        mean_r = sum(self.flat_returns) / len(self.flat_returns)
        var_r = sum((r - mean_r) ** 2 for r in self.flat_returns) / (len(self.flat_returns) - 1)
        if var_r <= 0:
            return 0.0
        return mean_r / math.sqrt(var_r) * math.sqrt(252)


# ─────────────────────────────────────────────
# Main Backtest
# ─────────────────────────────────────────────
def run_backtest():
    print("=" * 70)
    print("JBE TopSpin — Comprehensive 2025 Backtest")
    print("=" * 70)
    print()

    db = TennisDatabase(DB_PATH)

    # ─── PHASE 1: ELO Warm-up ─────────────────
    print("-" * 70)
    print("PHASE 1: ELO Warm-up (2019-2024)")
    print("-" * 70)

    elo_engine = SurfaceELOEngine(db)

    warmup = db.conn.execute("""
        SELECT id, winner_id, loser_id, surface, match_date, best_of,
               w_games, l_games
        FROM tennis_matches
        WHERE match_date >= '2019-01-01' AND match_date < '2025-01-01'
          AND w_sets > 0 AND surface IS NOT NULL
        ORDER BY match_date, id
    """).fetchall()

    print(f"  Warm-up matches: {len(warmup)}")

    for i, m in enumerate(warmup):
        if i > 0 and i % 5000 == 0:
            print(f"    progress: {i}/{len(warmup)} ({len(elo_engine.ratings)} players)")
        md = date.fromisoformat(m["match_date"])
        elo_engine.record_match(
            m["winner_id"], m["loser_id"], m["surface"],
            md, m["best_of"] == 5,
            m["w_games"] or 0, m["l_games"] or 0
        )

    print(f"  Warm-up complete. {len(elo_engine.ratings)} players have ELO ratings.")
    print()

    # ─── PHASE 2: Load TopSpinEngine with existing models ─────
    print("-" * 70)
    print("PHASE 2: Load TopSpinEngine (existing models only, no retrain)")
    print("-" * 70)

    engine = TopSpinEngine(db, load_models=True)
    print()

    # ─── PHASE 3: 2025 Backtest ─────────────────
    print("-" * 70)
    print("PHASE 3: Walk-forward Backtest on 2025")
    print("-" * 70)

    # Get all 2025 matches that have odds
    matches_2025 = db.conn.execute("""
        SELECT DISTINCT m.*, w.name as wname, l.name as lname
        FROM tennis_matches m
        JOIN players w ON w.id = m.winner_id
        JOIN players l ON l.id = m.loser_id
        WHERE m.match_date >= '2025-01-01' AND m.match_date < '2026-01-01'
          AND m.w_sets > 0 AND m.surface IS NOT NULL
          AND m.id IN (
              SELECT match_id FROM tennis_odds
              WHERE bookmaker IN ('Bet365', 'Pinnacle')
                AND odds_winner IS NOT NULL AND odds_loser IS NOT NULL
          )
        ORDER BY m.match_date, m.id
    """).fetchall()

    print(f"  2025 matches with odds: {len(matches_2025)}")
    print()

    # Stats accumulators
    total_matches = 0
    total_elo_correct = 0
    total_blend_correct = 0

    # Per-surface accuracy
    surface_stats = defaultdict(lambda: {"total": 0, "elo_ok": 0, "blend_ok": 0})

    # Per-edge bucket accuracy (match winner predictions)
    edge_buckets = {
        "5-7%":   {"lo": 0.05, "hi": 0.07, "total": 0, "won": 0},
        "7-10%":  {"lo": 0.07, "hi": 0.10, "total": 0, "won": 0},
        "10-15%": {"lo": 0.10, "hi": 0.15, "total": 0, "won": 0},
        "15-20%": {"lo": 0.15, "hi": 0.20, "total": 0, "won": 0},
        "20%+":   {"lo": 0.20, "hi": 1.00, "total": 0, "won": 0},
    }

    # All bets placed
    all_bets: List[BacktestBet] = []

    # Bankroll tracking
    bankroll = BankrollTracker(INITIAL_BANKROLL)

    # Kelly counters
    kelly_bets = 0
    kelly_wins = 0
    kelly_total_stake = 0.0
    kelly_total_profit = 0.0

    # Flat counters
    flat_bets = 0
    flat_wins = 0
    flat_total_stake = 0.0
    flat_total_profit = 0.0

    # P&L curve data (daily cumulative PnL)
    pnl_curve_kelly = []
    pnl_curve_flat = []

    # Per-market stats
    market_stats = defaultdict(lambda: {"bets": 0, "wins": 0})

    for i, m in enumerate(matches_2025):
        match_date = date.fromisoformat(m["match_date"])

        # Random flip perspective (50/50 p1 = winner/loser)
        flip = random.random() < 0.5

        if flip:
            p1_id, p2_id = m["winner_id"], m["loser_id"]
            p1_name, p2_name = m["wname"], m["lname"]
            p1_rank, p2_rank = m["winner_rank"], m["loser_rank"]
            actual_winner_is_p1 = True
        else:
            p1_id, p2_id = m["loser_id"], m["winner_id"]
            p1_name, p2_name = m["lname"], m["wname"]
            p1_rank, p2_rank = m["loser_rank"], m["winner_rank"]
            actual_winner_is_p1 = False

        try:
            # ── TopSpinEngine.predict() ──
            # Uses: ELO + XGBoost + Platt calibration + bias corrections
            pred = engine.predict(
                m["id"], p1_id, p2_id, m["surface"], match_date,
                m["best_of"] or 3, m["round"], m["tour_level"],
                p1_rank, p2_rank
            )

            # ── Markov predictions for handicap/O/U ──
            markov = engine.predict_markov(p1_id, p2_id, m["surface"], m["best_of"] or 3)

        except Exception as e:
            if i < 5:
                print(f"    [ERROR] match {m['id']}: {e}")
            # Still need to record match result for ELO
            elo_engine.record_match(
                m["winner_id"], m["loser_id"], m["surface"],
                match_date, m["best_of"] == 5,
                m["w_games"] or 0, m["l_games"] or 0
            )
            continue

        prob_p1 = pred["prob_player1"]
        prob_p2 = 1.0 - prob_p1
        blend_correct = (prob_p1 >= 0.5) == actual_winner_is_p1

        # ELO alone for comparison
        elo_prob_p1 = pred["prob_elo"]
        elo_correct = (elo_prob_p1 >= 0.5) == actual_winner_is_p1

        total_matches += 1
        if elo_correct:
            total_elo_correct += 1
        if blend_correct:
            total_blend_correct += 1

        # Per-surface
        surf = m["surface"]
        s = surface_stats[surf]
        s["total"] += 1
        if elo_correct:
            s["elo_ok"] += 1
        if blend_correct:
            s["blend_ok"] += 1

        # ── Fetch best odds (Pinnacle preferred, fallback Bet365) ──
        odds_row = db.conn.execute("""
            SELECT * FROM tennis_odds
            WHERE match_id = ? AND bookmaker = 'Pinnacle'
        """, (m["id"],)).fetchone()

        if not odds_row:
            odds_row = db.conn.execute("""
                SELECT * FROM tennis_odds
                WHERE match_id = ? AND bookmaker = 'Bet365'
            """, (m["id"],)).fetchone()

        if odds_row is None:
            # No odds available — just track prediction accuracy and move on
            elo_engine.record_match(
                m["winner_id"], m["loser_id"], m["surface"],
                match_date, m["best_of"] == 5,
                m["w_games"] or 0, m["l_games"] or 0
            )
            continue

        # ── Value Detection ──
        bets_this_match = []

        # Market 1: Match Winner
        for sel_name, sel_prob, col in [
            (p1_name, prob_p1, "odds_winner"),
            (p2_name, prob_p2, "odds_loser"),
        ]:
            odd_val = odds_row[col]
            if odd_val is None or odd_val <= 1.0:
                continue
            implied = 1.0 / odd_val
            edge = sel_prob - implied

            if sel_prob >= MIN_CONFIDENCE and edge >= EDGE_MW:
                bets_this_match.append({
                    "market": "match_winner",
                    "selection": sel_name,
                    "odds": odd_val,
                    "model_prob": sel_prob,
                    "edge": edge,
                    "actual_won": (sel_name == (m["wname"] if m["winner_id"] == p1_id else m["lname"]))
                        if flip
                        else (sel_name == (m["wname"] if m["winner_id"] == p2_id else m["lname"])),
                })
                # Track edge bucket
                for bucket_name, bucket in edge_buckets.items():
                    if bucket["lo"] <= edge < bucket["hi"]:
                        bucket["total"] += 1
                        if bets_this_match[-1]["actual_won"]:
                            bucket["won"] += 1
                        break

        # Market 2: Game Handicap (if odds available)
        gh_line = odds_row["handicap_line"]
        gh_odds_fav = odds_row["handicap_odds_fav"]
        gh_odds_dog = odds_row["handicap_odds_dog"]
        if gh_line is not None and gh_odds_fav is not None and gh_odds_dog is not None:
            # p_cover_handicap(point) returns P(A_diff > point)
            # A = player1. If gh_line > 0, fav is player1 (giving games)
            # If gh_line < 0, fav is player2 (receiving games)
            if gh_line >= 0:
                # A is favored, covering means A_diff > -gh_line
                p_cover = markov["markov_p_cover_handicap"](-gh_line)
                implied_fav = 1.0 / gh_odds_fav
                edge_fav = p_cover - implied_fav
                if p_cover >= MIN_CONFIDENCE and edge_fav >= EDGE_GH:
                    bets_this_match.append({
                        "market": "game_handicap",
                        "selection": f"{p1_name} -{gh_line}",
                        "odds": gh_odds_fav,
                        "model_prob": p_cover,
                        "edge": edge_fav,
                        "actual_won": None,  # computed below
                    })
                # Dog side
                p_not_cover = 1.0 - p_cover
                implied_dog = 1.0 / gh_odds_dog
                edge_dog = p_not_cover - implied_dog
                if p_not_cover >= MIN_CONFIDENCE and edge_dog >= EDGE_GH:
                    bets_this_match.append({
                        "market": "game_handicap",
                        "selection": f"{p2_name} +{gh_line}",
                        "odds": gh_odds_dog,
                        "model_prob": p_not_cover,
                        "edge": edge_dog,
                        "actual_won": None,
                    })
            else:
                # B is favored (negative handicap for B means B gives games)
                # A gets +|gh_line| games, so A covers if A_diff > gh_line (which is negative)
                abv_line = abs(gh_line)
                p_cover = markov["markov_p_cover_handicap"](gh_line)  # P(A_diff > negative = A winning by more than negative)
                implied_dog = 1.0 / gh_odds_dog
                edge_dog = p_cover - implied_dog
                if p_cover >= MIN_CONFIDENCE and edge_dog >= EDGE_GH:
                    bets_this_match.append({
                        "market": "game_handicap",
                        "selection": f"{p1_name} +{abv_line}",
                        "odds": gh_odds_dog,
                        "model_prob": p_cover,
                        "edge": edge_dog,
                        "actual_won": None,
                    })
                p_not_cover = 1.0 - p_cover
                implied_fav = 1.0 / gh_odds_fav
                edge_fav = p_not_cover - implied_fav
                if p_not_cover >= MIN_CONFIDENCE and edge_fav >= EDGE_GH:
                    bets_this_match.append({
                        "market": "game_handicap",
                        "selection": f"{p2_name} -{abv_line}",
                        "odds": gh_odds_fav,
                        "model_prob": p_not_cover,
                        "edge": edge_fav,
                        "actual_won": None,
                    })

        # Market 3: Over/Under Games (if odds available)
        total_line = odds_row["total_line"]
        over_odds = odds_row["over_odds"]
        under_odds = odds_row["under_odds"]
        if total_line is not None and over_odds is not None and under_odds is not None:
            p_over = markov["markov_p_over_threshold"](total_line)
            implied_over = 1.0 / over_odds
            edge_over = p_over - implied_over
            if p_over >= MIN_CONFIDENCE and edge_over >= EDGE_OU:
                bets_this_match.append({
                    "market": "over_under",
                    "selection": f"Over {total_line}",
                    "odds": over_odds,
                    "model_prob": p_over,
                    "edge": edge_over,
                    "actual_won": None,
                })
            p_under = markov["markov_p_under_threshold"](total_line)
            implied_under = 1.0 / under_odds
            edge_under = p_under - implied_under
            if p_under >= MIN_CONFIDENCE and edge_under >= EDGE_OU:
                bets_this_match.append({
                    "market": "over_under",
                    "selection": f"Under {total_line}",
                    "odds": under_odds,
                    "model_prob": p_under,
                    "edge": edge_under,
                    "actual_won": None,
                })

        # ── Resolve handicap/O/U actual results ──
        if m["w_games"] is not None and m["l_games"] is not None:
            actual_gA = m["w_games"] if (m["winner_id"] == p1_id) else m["l_games"]
            actual_gB = m["l_games"] if (m["winner_id"] == p1_id) else m["w_games"]
            actual_diff = actual_gA - actual_gB
            actual_total = actual_gA + actual_gB

            for bet in bets_this_match:
                if bet["market"] == "game_handicap":
                    if gh_line is not None:
                        if gh_line >= 0:
                            # A covers if A_diff > -gh_line
                            bet["actual_won"] = (actual_diff > -gh_line)
                        else:
                            # A covers if A_diff > gh_line (gh_line is negative)
                            bet["actual_won"] = (actual_diff > gh_line)
                elif bet["market"] == "over_under":
                    if "Over" in bet["selection"]:
                        bet["actual_won"] = (actual_total > total_line)
                    else:
                        bet["actual_won"] = (actual_total < total_line)

        # ── Calculate stakes and place bets ──
        for bet in bets_this_match:
            if bet["actual_won"] is None:
                continue  # can't resolve

            odds = bet["odds"]
            edge = bet["edge"]

            # Kelly stake: f = fraction * edge / (odds - 1)
            kelly_pct = KELLY_FRACTION * edge / (odds - 1) if odds > 1 else 0
            stake_kelly = bankroll.kelly_br * kelly_pct
            # Cap at 5% of current bankroll
            max_kelly = bankroll.kelly_br * MAX_STAKE_PCT
            stake_kelly = min(stake_kelly, max_kelly)
            stake_kelly = max(stake_kelly, 0.0)

            # Flat 2% of initial bankroll
            stake_flat = bankroll.flat_br * FLAT_STAKE_PCT

            # Skip tiny bets
            if stake_kelly < 0.5 and stake_flat < 0.5:
                continue

            # Resolve
            won = bet["actual_won"]
            profit_kelly = stake_kelly * (odds - 1) if won else -stake_kelly
            profit_flat = stake_flat * (odds - 1) if won else -stake_flat

            # Create bet record
            winner_name = m["wname"]  # actual match winner
            bb = BacktestBet(
                match_id=m["id"],
                match_date=m["match_date"],
                tournament=m["tournament"],
                surface=m["surface"],
                tour_level=m["tour_level"],
                round_val=m["round"],
                market=bet["market"],
                selection=bet["selection"],
                player1_name=p1_name,
                player2_name=p2_name,
                odds=odds,
                model_prob=bet["model_prob"],
                edge=edge,
                stake_kelly=round(stake_kelly, 2),
                stake_flat=round(stake_flat, 2),
                actual_won=won,
                profit_kelly=round(profit_kelly, 2),
                profit_flat=round(profit_flat, 2),
                winner_name=winner_name,
            )
            all_bets.append(bb)

            # Track bankroll
            bankroll.record_result(stake_kelly, profit_kelly, stake_flat, profit_flat, match_date)

            # Per-market stats
            market_stats[bet["market"]]["bets"] += 1
            if won:
                market_stats[bet["market"]]["wins"] += 1

            # Kelly counters
            if stake_kelly >= 0.5:
                kelly_bets += 1
                kelly_total_stake += stake_kelly
                kelly_total_profit += profit_kelly
                if won:
                    kelly_wins += 1

            # Flat counters
            if stake_flat >= 0.5:
                flat_bets += 1
                flat_total_stake += stake_flat
                flat_total_profit += profit_flat
                if won:
                    flat_wins += 1

        # ── Record ELO result (walk-forward) ──
        elo_engine.record_match(
            m["winner_id"], m["loser_id"], m["surface"],
            match_date, m["best_of"] == 5,
            m["w_games"] or 0, m["l_games"] or 0
        )

        if (i + 1) % 100 == 0:
            print(f"    progress: {i+1}/{len(matches_2025)} "
                  f"[acc: {total_blend_correct/max(total_matches,1)*100:.1f}% "
                  f"kelly_bets: {kelly_bets} flat_bets: {flat_bets}]")

    # Finalize bankroll PnL curve
    bankroll.finalize()

    # ─────────────────────────────────────────────
    # REPORT
    # ─────────────────────────────────────────────
    print()
    print("=" * 70)
    print("BACKTEST REPORT — JBE TopSpin ATP 2025")
    print("=" * 70)

    # ── Overall Accuracy ──
    print()
    print("─── ACCURACY ───")
    print(f"  Total matches tested:  {total_matches}")
    print(f"  ELO-only accuracy:     {total_elo_correct/max(total_matches,1)*100:.2f}% "
          f"({total_elo_correct}/{total_matches})")
    print(f"  Ensemble accuracy:     {total_blend_correct/max(total_matches,1)*100:.2f}% "
          f"({total_blend_correct}/{total_matches})")
    improvement = (total_blend_correct - total_elo_correct) / max(total_matches, 1) * 100
    print(f"  XGBoost improvement:   {improvement:+.2f}pp")

    # ── Accuracy by Surface ──
    print()
    print("─── ACCURACY BY SURFACE ───")
    for surf in ["Hard", "Clay", "Grass", "Carpet"]:
        if surf in surface_stats:
            s = surface_stats[surf]
            elo_acc = s["elo_ok"] / s["total"] * 100
            blend_acc = s["blend_ok"] / s["total"] * 100
            print(f"  {surf:8s}: {s['total']:4d} matches | "
                  f"ELO {elo_acc:5.1f}% | Ensemble {blend_acc:5.1f}%"
                  f" ({'+' if blend_acc >= elo_acc else ''}{blend_acc-elo_acc:.1f}pp)")

    # ── Accuracy by Edge Bucket ──
    print()
    print("─── ACCURACY BY EDGE BUCKET (Match Winner bets) ───")
    for bucket_name in ["5-7%", "7-10%", "10-15%", "15-20%", "20%+"]:
        b = edge_buckets[bucket_name]
        if b["total"] > 0:
            acc = b["won"] / b["total"] * 100
            print(f"  {bucket_name:>8s}: {b['total']:4d} bets | {acc:5.1f}% ({b['won']}/{b['total']})")

    # ── Betting Summary ──
    print()
    print("─── BETTING SUMMARY ───")
    print(f"  Total value bets detected: {len(all_bets)}")

    print()
    print(f"  ┌─ KELLY 12.5% (capped 5% BR) ──────────────────────")
    kelly_roi_stake = kelly_total_profit / kelly_total_stake * 100 if kelly_total_stake > 0 else 0
    print(f"  │  Bets placed:           {kelly_bets}")
    print(f"  │  Won:                   {kelly_wins} ({kelly_wins/max(kelly_bets,1)*100:.1f}%)")
    print(f"  │  Total stake:           {kelly_total_stake:>10.2f} EUR")
    print(f"  │  Total profit:          {kelly_total_profit:>+10.2f} EUR")
    print(f"  │  ROI (on stake):        {kelly_roi_stake:+8.2f}%")
    print(f"  │  Initial bankroll:      {INITIAL_BANKROLL:>10.2f} EUR")
    print(f"  │  Final bankroll:        {bankroll.kelly_br:>10.2f} EUR")
    print(f"  │  ROI (total):           {bankroll.kelly_roi_total:+8.2f}%")
    print(f"  │  Max drawdown:          {bankroll.kelly_drawdown:>8.2f}%")
    print(f"  │  Sharpe ratio (ann.):   {bankroll.kelly_sharpe:>8.3f}")
    print(f"  └────────────────────────────────────────────────────")

    print()
    print(f"  ┌─ FLAT 2% BR ───────────────────────────────────────")
    flat_roi_stake = flat_total_profit / flat_total_stake * 100 if flat_total_stake > 0 else 0
    print(f"  │  Bets placed:           {flat_bets}")
    print(f"  │  Won:                   {flat_wins} ({flat_wins/max(flat_bets,1)*100:.1f}%)")
    print(f"  │  Total stake:           {flat_total_stake:>10.2f} EUR")
    print(f"  │  Total profit:          {flat_total_profit:>+10.2f} EUR")
    print(f"  │  ROI (on stake):        {flat_roi_stake:+8.2f}%")
    print(f"  │  Initial bankroll:      {INITIAL_BANKROLL:>10.2f} EUR")
    print(f"  │  Final bankroll:        {bankroll.flat_br:>10.2f} EUR")
    print(f"  │  ROI (total):           {bankroll.flat_roi_total:+8.2f}%")
    print(f"  │  Max drawdown:          {bankroll.flat_drawdown:>8.2f}%")
    print(f"  │  Sharpe ratio (ann.):   {bankroll.flat_sharpe:>8.3f}")
    print(f"  └────────────────────────────────────────────────────")

    # ── Per-Market Breakdown ──
    print()
    print("─── PER-MARKET BREAKDOWN ───")
    for mkt in ["match_winner", "game_handicap", "over_under"]:
        if mkt in market_stats:
            s = market_stats[mkt]
            winrate = s["wins"] / s["bets"] * 100 if s["bets"] > 0 else 0
            mkt_bets = [b for b in all_bets if b.market == mkt]
            mkt_profit = sum(b.profit_kelly for b in mkt_bets if b.stake_kelly >= 0.5)
            mkt_stake = sum(b.stake_kelly for b in mkt_bets if b.stake_kelly >= 0.5)
            mkt_roi = mkt_profit / mkt_stake * 100 if mkt_stake > 0 else 0
            print(f"  {mkt:20s}: {s['bets']:4d} bets | {winrate:5.1f}% WR | "
                  f"ROI (Kelly stake) {mkt_roi:+7.2f}% | "
                  f"P&L {mkt_profit:+8.2f} EUR")

    # ── Best / Worst 10 bets ──
    if all_bets:
        print()
        print("─── TOP 10 BEST BETS (by Kelly profit) ───")
        sorted_bets = sorted(all_bets, key=lambda b: b.profit_kelly, reverse=True)
        for idx, b in enumerate(sorted_bets[:10], 1):
            result = "WON" if b.actual_won else "LOST"
            print(f"  {idx:2d}. {b.match_date} | {b.player1_name} vs {b.player2_name} "
                  f"({b.surface})")
            print(f"       {b.market}: {b.selection} @{b.odds:.2f} "
                  f"| model={b.model_prob:.1%} edge={b.edge:.1%}")
            print(f"       {result} | Kelly stake={b.stake_kelly:.1f}EUR "
                  f"profit={b.profit_kelly:+.1f}EUR | "
                  f"Flat stake={b.stake_flat:.1f}EUR profit={b.profit_flat:+.1f}EUR")

        print()
        print("─── WORST 10 BETS (by Kelly loss) ───")
        for idx, b in enumerate(sorted_bets[-10:], 1):
            result = "WON" if b.actual_won else "LOST"
            print(f"  {idx:2d}. {b.match_date} | {b.player1_name} vs {b.player2_name} "
                  f"({b.surface})")
            print(f"       {b.market}: {b.selection} @{b.odds:.2f} "
                  f"| model={b.model_prob:.1%} edge={b.edge:.1%}")
            print(f"       {result} | Kelly stake={b.stake_kelly:.1f}EUR "
                  f"profit={b.profit_kelly:+.1f}EUR | "
                  f"Flat stake={b.stake_flat:.1f}EUR profit={b.profit_flat:+.1f}EUR")

    # ── P&L Curve Data ──
    print()
    print("─── P&L CURVE (cumulative, end-of-year) ───")
    print(f"  Kelly final P&L: {bankroll.kelly_br - INITIAL_BANKROLL:+.2f} EUR")
    print(f"  Flat final P&L:  {bankroll.flat_br - INITIAL_BANKROLL:+.2f} EUR")

    # ── Summary metrics ──
    print()
    print("─── SUMMARY METRICS ───")
    print(f"  Accuracy (ensemble):            {total_blend_correct/max(total_matches,1)*100:.2f}%")
    print(f"  Accuracy (ELO only):            {total_elo_correct/max(total_matches,1)*100:.2f}%")
    print(f"  Total bets (Kelly):             {kelly_bets}")
    print(f"  Win rate (Kelly):               {kelly_wins/max(kelly_bets,1)*100:.1f}%")
    print(f"  ROI on stake (Kelly):           {kelly_roi_stake:+.2f}%")
    print(f"  ROI total capital (Kelly):      {bankroll.kelly_roi_total:+.2f}%")
    print(f"  Sharpe ratio annual (Kelly):    {bankroll.kelly_sharpe:.3f}")
    print(f"  Max drawdown (Kelly):           {bankroll.kelly_drawdown:.2f}%")
    print(f"  Flat bets:                      {flat_bets}")
    print(f"  Flat win rate:                  {flat_wins/max(flat_bets,1)*100:.1f}%")
    print(f"  Flat ROI on stake:              {flat_roi_stake:+.2f}%")
    print(f"  Flat ROI total capital:         {bankroll.flat_roi_total:+.2f}%")
    print(f"  Flat Sharpe ratio annual:       {bankroll.flat_sharpe:.3f}")
    print(f"  Flat max drawdown:              {bankroll.flat_drawdown:.2f}%")

    # ── Save detailed results to JSON ──
    output = {
        "metadata": {
            "period": "2025-01-01 to 2025-12-31",
            "initial_bankroll": INITIAL_BANKROLL,
            "kelly_fraction": KELLY_FRACTION,
            "flat_stake_pct": FLAT_STAKE_PCT,
            "edge_thresholds": {
                "match_winner": EDGE_MW,
                "game_handicap": EDGE_GH,
                "over_under": EDGE_OU,
            },
            "models_used": [
                "topspin_winner.json",
                "topspin_games.json",
                "platt_calibration.json",
            ],
        },
        "accuracy": {
            "total_matches": total_matches,
            "elo_accuracy": round(total_elo_correct / max(total_matches, 1) * 100, 2),
            "ensemble_accuracy": round(total_blend_correct / max(total_matches, 1) * 100, 2),
            "by_surface": {
                surf: {
                    "matches": s["total"],
                    "elo_acc": round(s["elo_ok"] / s["total"] * 100, 2),
                    "ensemble_acc": round(s["blend_ok"] / s["total"] * 100, 2),
                }
                for surf, s in sorted(surface_stats.items())
            },
            "by_edge_bucket": {
                name: {
                    "bets": b["total"],
                    "wins": b["won"],
                    "accuracy": round(b["won"] / max(b["total"], 1) * 100, 2),
                }
                for name, b in edge_buckets.items() if b["total"] > 0
            },
        },
        "kelly": {
            "total_bets": kelly_bets,
            "wins": kelly_wins,
            "win_rate": round(kelly_wins / max(kelly_bets, 1) * 100, 1),
            "total_stake": round(kelly_total_stake, 2),
            "total_profit": round(kelly_total_profit, 2),
            "roi_on_stake": round(kelly_roi_stake, 2),
            "final_bankroll": round(bankroll.kelly_br, 2),
            "roi_total": round(bankroll.kelly_roi_total, 2),
            "max_drawdown": round(bankroll.kelly_drawdown, 2),
            "sharpe_ratio": round(bankroll.kelly_sharpe, 3),
        },
        "flat": {
            "total_bets": flat_bets,
            "wins": flat_wins,
            "win_rate": round(flat_wins / max(flat_bets, 1) * 100, 1),
            "total_stake": round(flat_total_stake, 2),
            "total_profit": round(flat_total_profit, 2),
            "roi_on_stake": round(flat_roi_stake, 2),
            "final_bankroll": round(bankroll.flat_br, 2),
            "roi_total": round(bankroll.flat_roi_total, 2),
            "max_drawdown": round(bankroll.flat_drawdown, 2),
            "sharpe_ratio": round(bankroll.flat_sharpe, 3),
        },
        "by_market": {
            mkt: {
                "bets": s["bets"],
                "wins": s["wins"],
                "win_rate": round(s["wins"] / max(s["bets"], 1) * 100, 1),
                "profit_kelly": round(sum(b.profit_kelly for b in all_bets
                                          if b.market == mkt and b.stake_kelly >= 0.5), 2),
                "stake_kelly": round(sum(b.stake_kelly for b in all_bets
                                          if b.market == mkt and b.stake_kelly >= 0.5), 2),
            }
            for mkt, s in market_stats.items()
        },
        "best_bets": [
            {
                "date": b.match_date, "p1": b.player1_name, "p2": b.player2_name,
                "surface": b.surface, "market": b.market, "selection": b.selection,
                "odds": b.odds, "model_prob": round(b.model_prob, 3),
                "edge": round(b.edge, 4), "won": b.actual_won,
                "stake_kelly": b.stake_kelly, "profit_kelly": b.profit_kelly,
            }
            for b in sorted(all_bets, key=lambda x: x.profit_kelly, reverse=True)[:10]
        ],
        "worst_bets": [
            {
                "date": b.match_date, "p1": b.player1_name, "p2": b.player2_name,
                "surface": b.surface, "market": b.market, "selection": b.selection,
                "odds": b.odds, "model_prob": round(b.model_prob, 3),
                "edge": round(b.edge, 4), "won": b.actual_won,
                "stake_kelly": b.stake_kelly, "profit_kelly": b.profit_kelly,
            }
            for b in sorted(all_bets, key=lambda x: x.profit_kelly)[:10]
        ],
    }

    json_path = os.path.join(OUTPUT_DIR, "backtest_2025_results.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Detailed results saved to: {json_path}")

    db.close()
    print()
    print("Backtest complete.")


if __name__ == "__main__":
    run_backtest()
