"""
JBE TopSpin — Strato 4: XGBoost Multi-Target Meta-Modello

Combina gli output degli strati precedenti (ELO, Markov, contestuali)
con XGBoost per ottenere probabilita' calibrate.

Tre modelli separati:
1. Winner Classifier: match winner (binary)
2. Game Total Regressor: game totali del match
3. Set Spread Classifier: 4 classi (2-0, 2-1, 1-2, 0-2)
"""
import os
import json
import numpy as np
from datetime import date, timedelta
from typing import Optional, List, Dict

import xgboost as xgb
from sklearn.model_selection import train_test_split

from config import (
    XGB_LEARNING_RATE, XGB_MAX_DEPTH, XGB_N_ESTIMATORS,
    XGB_EARLY_STOPPING, XGB_RETRAIN_EVERY, MODEL_DIR, SURFACES
)
from database import TennisDatabase
from engine.elo_tennis import SurfaceELOEngine
from engine.contextual_factors import ContextualFactors
from engine.markov_tennis import MarkovMatchModel


class FeatureExtractor:
    """Costruisce il feature vector unendo tutti gli strati."""

    def __init__(self, db: TennisDatabase, elo_engine: SurfaceELOEngine):
        self.db = db
        self.elo = elo_engine
        self.context = ContextualFactors(db)

    def extract(self, match_id: int, match_date: date,
                winner_id: int, loser_id: int,
                surface: str, best_of: int,
                round_val: str = None, tour_level: str = None) -> dict:
        """
        Estrae TUTTE le feature per un match.
        
        Returns:
            dict con ~30 feature
        """
        features = {}

        # === Strato 1 — ELO features ===
        pred = self.elo.predict_winner(winner_id, loser_id, surface, best_of == 5)
        r1 = self.elo._get_or_create_rating(winner_id)
        r2 = self.elo._get_or_create_rating(loser_id)

        features["elo_diff_overall"] = r1.overall - r2.overall
        features["elo_diff_surface"] = r1.get_surface_rating(surface) - r2.get_surface_rating(surface)
        features["elo_blended_diff"] = r1.get_blended_rating(surface) - r2.get_blended_rating(surface)
        features["elo_mov_diff"] = r1.mov - r2.mov
        features["elo_prob"] = pred["prob_player1"]

        # Confidence in surface rating
        features["elo_surf_conf_p1"] = min(r1.get_surface_matches(surface) / 50, 1.0)
        features["elo_surf_conf_p2"] = min(r2.get_surface_matches(surface) / 50, 1.0)

        # === Ranking features (dal match record) ===
        features["rank_p1"] = None
        features["rank_p2"] = None

        # === Strato 3 — Contextual features ===
        ctx = self.context.compute_all(
            winner_id, loser_id, surface, match_date,
            round_val, tour_level
        )
        features.update(ctx)

        # === Surface encoding ===
        for s in SURFACES:
            features[f"surface_{s.lower()}"] = 1 if surface == s else 0

        # === Best of ===
        features["best_of_5"] = 1 if best_of == 5 else 0

        return features


class XGBoostTrainer:
    """Addestra e gestisce i modelli XGBoost."""

    def __init__(self, db: TennisDatabase):
        self.db = db
        self.elo_engine = SurfaceELOEngine(db)
        self.feature_extractor = FeatureExtractor(db, self.elo_engine)
        self.winner_model: Optional[xgb.XGBClassifier] = None
        self.games_model: Optional[xgb.XGBRegressor] = None
        self.sets_model: Optional[xgb.XGBClassifier] = None

    def build_training_data(self, start_date: str, end_date: str) -> tuple:
        """
        Costruisce il dataset di training.
        
        Per ogni match, calcola le feature e il target.
        
        Returns:
            (X, y_winner, y_games, y_sets)
        """
        matches = self.db.conn.execute(
            """SELECT m.*, w.name as wname, l.name as lname 
               FROM tennis_matches m
               JOIN players w ON w.id=m.winner_id
               JOIN players l ON l.id=m.loser_id
               WHERE m.match_date >= ? AND m.match_date < ?
               AND m.surface IS NOT NULL AND m.w_sets > 0
               ORDER BY m.match_date""",
            (start_date, end_date),
        ).fetchall()

        X_list = []
        y_winner = []
        y_games = []
        y_sets = []
        errors = 0

        import random
        for i, m in enumerate(matches):
            try:
                match_date = date.fromisoformat(m["match_date"])

                # Train ELO sequentially (match by match)
                self.elo_engine.record_match(
                    m["winner_id"], m["loser_id"], m["surface"],
                    match_date, m["best_of"] == 5,
                    m["w_games"] or 0, m["l_games"] or 0
                )

                # Randomly flip perspective so we have both classes
                if random.random() < 0.5:
                    # winner is player1 (y=1)
                    p1_id, p2_id = m["winner_id"], m["loser_id"]
                    p1_rank, p2_rank = m["winner_rank"], m["loser_rank"]
                    p1_rank_pts, p2_rank_pts = m["winner_rank_points"], m["loser_rank_points"]
                    y = 1
                    actual_games = (m["w_games"] or 0) + (m["l_games"] or 0)
                    actual_sets = f"{m['w_sets']}-{m['l_sets']}"
                else:
                    # loser is player1 (y=0)
                    p1_id, p2_id = m["loser_id"], m["winner_id"]
                    p1_rank, p2_rank = m["loser_rank"], m["winner_rank"]
                    p1_rank_pts, p2_rank_pts = m["loser_rank_points"], m["winner_rank_points"]
                    y = 0
                    actual_games = (m["w_games"] or 0) + (m["l_games"] or 0)
                    actual_sets = f"{m['l_sets']}-{m['w_sets']}"

                # Extract features
                feats = self.feature_extractor.extract(
                    m["id"], match_date, p1_id, p2_id,
                    m["surface"], m["best_of"], m["round"], m["tour_level"]
                )

                # Add ranking data
                feats["rank_p1"] = p1_rank or 0
                feats["rank_p2"] = p2_rank or 0
                feats["rank_diff"] = (p2_rank or 0) - (p1_rank or 0)
                feats["rank_pts_diff"] = (p1_rank_pts or 0) - (p2_rank_pts or 0)

                X_list.append(feats)
                y_winner.append(y)
                y_games.append(actual_games)
                y_sets.append(actual_sets)

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  [ERRORE] match {m['id']}: {e}")

        # Convert features dicts to matrix
        feature_names = list(X_list[0].keys()) if X_list else []
        X = np.array([[v for v in feat.values()] for feat in X_list])

        print(f"Feature shape: {X.shape}")
        print(f"Feature names ({len(feature_names)}): {feature_names}")

        return X, np.array(y_winner), np.array(y_games), np.array(y_sets), feature_names

    def train_winner_model(self, X_train, y_train, X_val, y_val):
        """Addestra il modello winner classifier."""
        print("Training winner classifier...")
        
        # Calcola scale_pos_weight per bilanciare
        n_pos = y_train.sum()
        n_neg = len(y_train) - n_pos
        scale = n_neg / n_pos if n_pos > 0 else 1.0

        self.winner_model = xgb.XGBClassifier(
            n_estimators=XGB_N_ESTIMATORS,
            max_depth=XGB_MAX_DEPTH,
            learning_rate=XGB_LEARNING_RATE,
            objective='binary:logistic',
            eval_metric=['logloss', 'error'],
            scale_pos_weight=scale,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
        )

        self.winner_model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )
        
        # Evaluate
        train_acc = (self.winner_model.predict(X_train) == y_train).mean()
        val_acc = (self.winner_model.predict(X_val) == y_val).mean()
        print(f"  Train accuracy: {train_acc:.4f}")
        print(f"  Val accuracy: {val_acc:.4f}")
        
        return val_acc

    def train_games_model(self, X_train, y_train, X_val, y_val):
        """Addestra il modello game total regressor."""
        print("Training game total regressor...")

        self.games_model = xgb.XGBRegressor(
            n_estimators=XGB_N_ESTIMATORS,
            max_depth=XGB_MAX_DEPTH,
            learning_rate=XGB_LEARNING_RATE,
            objective='reg:squarederror',
            eval_metric=['mae', 'rmse'],
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
        )

        self.games_model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        train_mae = np.abs(self.games_model.predict(X_train) - y_train).mean()
        val_mae = np.abs(self.games_model.predict(X_val) - y_val).mean()
        print(f"  Train MAE: {train_mae:.2f} games")
        print(f"  Val MAE: {val_mae:.2f} games")

        return val_mae

    def train_sets_model(self, X_train, y_train, X_val, y_val):
        """Addestra il modello set spread classifier (4 classi)."""
        from sklearn.preprocessing import LabelEncoder
        
        le = LabelEncoder()
        y_train_enc = le.fit_transform(y_train)
        y_val_enc = le.transform(y_val)
        
        print(f"Set classes: {list(le.classes_)}")
        print(f"Training set spread classifier...")

        self.sets_model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            objective='multi:softprob',
            num_class=len(le.classes_),
            eval_metric=['mlogloss', 'merror'],
            random_state=42,
            n_jobs=-1,
        )

        self.sets_model.fit(
            X_train, y_train_enc,
            eval_set=[(X_val, y_val_enc)],
            verbose=False,
        )

        train_acc = (self.sets_model.predict(X_train) == y_train_enc).mean()
        val_acc = (self.sets_model.predict(X_val) == y_val_enc).mean()
        print(f"  Train accuracy: {train_acc:.4f}")
        print(f"  Val accuracy: {val_acc:.4f}")

        return val_acc

    def save_models(self, prefix="topspin"):
        """Salva i modelli su disco."""
        os.makedirs(MODEL_DIR, exist_ok=True)

        if self.winner_model:
            path = os.path.join(MODEL_DIR, f"{prefix}_winner.json")
            self.winner_model.save_model(path)
            print(f"Winner model saved: {path}")

        if self.games_model:
            path = os.path.join(MODEL_DIR, f"{prefix}_games.json")
            self.games_model.save_model(path)
            print(f"Games model saved: {path}")

        if self.sets_model:
            path = os.path.join(MODEL_DIR, f"{prefix}_sets.json")
            self.sets_model.save_model(path)
            print(f"Sets model saved: {path}")

    def load_models(self, prefix="topspin"):
        """Carica i modelli dal disco."""
        winner_path = os.path.join(MODEL_DIR, f"{prefix}_winner.json")
        if os.path.exists(winner_path):
            self.winner_model = xgb.XGBClassifier()
            self.winner_model.load_model(winner_path)
            print(f"Winner model loaded")

        games_path = os.path.join(MODEL_DIR, f"{prefix}_games.json")
        if os.path.exists(games_path):
            self.games_model = xgb.XGBRegressor()
            self.games_model.load_model(games_path)
            print(f"Games model loaded")

        sets_path = os.path.join(MODEL_DIR, f"{prefix}_sets.json")
        if os.path.exists(sets_path):
            self.sets_model = xgb.XGBClassifier()
            self.sets_model.load_model(sets_path)
            print(f"Sets model loaded")


class TopSpinEngine:
    """
    Motore completo JBE TopSpin.
    Combina ELO + Markov + Contestuali + XGBoost + Platt Calibration.
    """

    def __init__(self, db: TennisDatabase, load_models: bool = True):
        self.db = db
        self.elo_engine = SurfaceELOEngine(db)
        self.feature_extractor = FeatureExtractor(db, self.elo_engine)
        self.xgb = XGBoostTrainer(db)
        self.calibration = None
        self.bias_cache = {}
        
        if load_models:
            self.xgb.load_models()
            self._load_calibration()
            self._load_bias()

    def _load_bias(self):
        """Carica bias_corrections dal DB in cache."""
        try:
            rows = self.db.conn.execute(
                "SELECT slice_type, slice_value, bias FROM bias_corrections"
            ).fetchall()
            for row in rows:
                key = f"{row['slice_type']}:{row['slice_value']}"
                self.bias_cache[key] = row["bias"]
            if rows:
                print(f"   Bias corrections loaded: {len(rows)}")
        except Exception as e:
            print(f"   [WARN] Cannot load bias corrections: {e}")

    def _load_calibration(self):
        """Carica parametri Platt scaling dal file JSON."""
        cal_path = os.path.join(MODEL_DIR, "platt_calibration.json")
        if os.path.exists(cal_path):
            try:
                with open(cal_path) as f:
                    self.calibration = json.load(f)
                print(f"   Platt calibration loaded (slope={self.calibration['slope']:.4f}, intercept={self.calibration['intercept']:.4f})")
            except Exception as e:
                print(f"   [WARN] Cannot load calibration: {e}")
                self.calibration = None
        else:
            print("   [WARN] No calibration file found")

    def _apply_calibration(self, prob_xgb: float) -> float:
        """Applica Platt scaling a una probabilita' XGBoost."""
        if self.calibration is None:
            return prob_xgb
        eps = 1e-7
        p = max(min(prob_xgb, 1 - eps), eps)
        logit = np.log(p / (1 - p))
        calibrated_logit = self.calibration["slope"] * logit + self.calibration["intercept"]
        cal_prob = 1.0 / (1.0 + np.exp(-calibrated_logit))
        return float(cal_prob)

    def _apply_bias_correction(self, prob: float, surface: str,
                                tour_level: str = None,
                                round_val: str = None,
                                odds: float = None) -> float:
        """Applica bias correction per slice alla probabilita' su scala LOGIT.
        
        Invece di aggiungere bias alla probabilita' lineare (che puo' sforare [0,1]),
        lo applichiamo su scala logit: logit(p) = ln(p/(1-p)).
        
        Args:
            prob: Probabilita' del modello [0, 1]
            surface: Superficie del match
            tour_level: Livello torneo (A=ATP500, M=Masters, G=GrandSlam)
            round_val: Round del torneo
            odds: Quota del bookmaker (per odds_range bias)
        """
        import math
        bias_adj = 0.0
        
        # Accumula bias per ogni slice rilevante
        slice_keys = [
            f"surface:{surface}",
        ]
        if tour_level:
            slice_keys.append(f"tour_level:{tour_level}")
        if round_val:
            slice_keys.append(f"round:{round_val}")
        
        for key in slice_keys:
            bias = self.bias_cache.get(key, 0.0)
            bias_adj += bias * 0.3  # Attenuazione per non sovracorreggere
        
        # Odds range bias (applicato solo se conosciamo la quota)
        if odds is not None and odds > 1.0:
            implied = 1.0 / odds
            if implied < 0.1:
                odds_key = "odds_range:5.0-999"
            elif implied < 0.2:
                odds_key = "odds_range:3.0-5.0"
            elif implied < 0.333:
                odds_key = "odds_range:2.0-3.0"
            elif implied < 0.5:
                odds_key = "odds_range:1.5-2.0"
            else:
                odds_key = "odds_range:0-1.5"
            odds_bias = self.bias_cache.get(odds_key, 0.0)
            bias_adj += odds_bias * 0.3
        
        if abs(bias_adj) < 0.001:
            return prob
        
        # Applica bias su scala LOGIT invece di lineare
        eps = 1e-7
        p = max(min(prob, 1 - eps), eps)
        logit = math.log(p / (1 - p))
        logit_adj = logit + bias_adj
        p_adj = 1.0 / (1.0 + math.exp(-logit_adj))
        return max(0.01, min(0.99, p_adj))

    def predict(self, match_id: int, player1_id: int, player2_id: int,
                surface: str, match_date: date, best_of: int = 3,
                round_val: str = None, tour_level: str = None,
                rank_p1: int = None, rank_p2: int = None,
                rank_pts_p1: int = None, rank_pts_p2: int = None,
                odds_p1: float = None, odds_p2: float = None) -> dict:
        """
        Predice l'esito di un match usando l'ensemble completo.
        
        Returns:
            dict con tutte le probabilita'
        """
        # Base ELO prediction
        elo_pred = self.elo_engine.predict_winner(player1_id, player2_id, surface, best_of == 5)

        # Extract features
        feats = self.feature_extractor.extract(
            match_id, match_date, player1_id, player2_id,
            surface, best_of, round_val, tour_level
        )

        # Ranking data
        feats["rank_p1"] = rank_p1 or 0
        feats["rank_p2"] = rank_p2 or 0
        feats["rank_diff"] = (rank_p2 or 0) - (rank_p1 or 0)
        feats["rank_pts_diff"] = (rank_pts_p1 or 0) - (rank_pts_p2 or 0)
        # XGBoost prediction (se modello caricato)
        if self.xgb.winner_model:
            X = np.array([list(feats.values())])
            
            # Winner probability from XGBoost
            prob_winner_xgb = self.xgb.winner_model.predict_proba(X)[0][1]
            
            # APPLY Platt calibration
            prob_winner_xgb_cal = self._apply_calibration(prob_winner_xgb)
            
            # Blend ELO + XGBoost calibrato (media ponderata)
            prob_final = 0.3 * elo_pred["prob_player1"] + 0.7 * prob_winner_xgb_cal
            
            # APPLY bias correction per slice (con odds_p1 per odds_range)
            prob_final = self._apply_bias_correction(
                prob_final, surface, tour_level, round_val, odds_p1
            )
            
            # Game total prediction
            games_pred = None
            if self.xgb.games_model:
                games_pred = self.xgb.games_model.predict(X)[0]
            
            # Set spread prediction
            set_pred = None
            if self.xgb.sets_model:
                set_pred = self.xgb.sets_model.predict(X)[0]
        else:
            prob_final = elo_pred["prob_player1"]
            prob_winner_xgb = None
            prob_winner_xgb_cal = None
            games_pred = None
            set_pred = None

        return {
            "prob_player1": prob_final,
            "prob_player2": 1.0 - prob_final,
            "prob_elo": elo_pred["prob_player1"],
            "prob_xgb": prob_winner_xgb,
            "prob_xgb_calibrated": prob_winner_xgb_cal if self.xgb.winner_model else None,
            "elo_diff": elo_pred["elo_diff"],
            "blended_diff": elo_pred["blended_diff"],
            "predicted_games": games_pred,
            "predicted_sets": set_pred,
        }

    def _get_serve_params(self, player_id: int, surface: str) -> dict:
        """Recupera p_serve e q_return per un giocatore su una superficie."""
        row = self.db.conn.execute(
            "SELECT p_serve, q_return, confidence FROM serve_return_params WHERE player_id=? AND surface=?",
            (player_id, surface)
        ).fetchone()
        if row:
            return {
                "p_serve": row["p_serve"],
                "q_return": row["q_return"],
                "confidence": row["confidence"],
            }
        # Defaults per superficie
        defaults = {"Hard": (0.63, 0.37), "Clay": (0.60, 0.40),
                    "Grass": (0.64, 0.36), "Carpet": (0.62, 0.38)}
        p, q = defaults.get(surface, (0.63, 0.37))
        return {"p_serve": p, "q_return": q, "confidence": 0.0}

    def predict_markov(self, player1_id: int, player2_id: int,
                       surface: str, best_of: int = 3) -> dict:
        """
        Usa il modello Markoviano per calcolare probabilita' dettagliate.
        Returns: dict con probabilita' set, game totali attesi, distribuzione game.
        """
        sp1 = self._get_serve_params(player1_id, surface)
        sp2 = self._get_serve_params(player2_id, surface)

        model = MarkovMatchModel(sp1["p_serve"], sp2["p_serve"], surface, best_of)
        markov_pred = model.predict_match()

        return {
            "markov_p_win": markov_pred["p_win_match"],
            "markov_set_betting_A": markov_pred["set_betting_winner"],
            "markov_set_betting_B": markov_pred["set_betting_loser"],
            "markov_expected_games": markov_pred["expected_total_games"],
            "markov_game_diff_dist": markov_pred["game_diff_dist"],
            "markov_total_games_dist": markov_pred["total_games_dist"],
            "markov_p_cover_handicap": markov_pred["p_cover_handicap"],
            "markov_p_over_threshold": markov_pred["p_over_threshold"],
            "markov_p_under_threshold": markov_pred["p_under_threshold"],
            "markov_p_serve_A": sp1["p_serve"],
            "markov_p_serve_B": sp2["p_serve"],
            "markov_confidence_A": sp1["confidence"],
            "markov_confidence_B": sp2["confidence"],
        }

    def record_match_result(self, match_id: int, winner_id: int, loser_id: int,
                           surface: str, match_date: date, best_of: int,
                           w_games: int, l_games: int,
                           prob: float, edge: float = None):
        """Registra il risultato e persiste ELO nel DB."""
        # Update ELO in-memory
        self.elo_engine.record_match(
            winner_id, loser_id, surface, match_date,
            best_of == 5, w_games, l_games
        )
        # Persiste ELO su DB
        self.elo_engine.save_ratings(match_id, match_date)
        self.db.conn.commit()

    def get_retrain_needed(self) -> bool:
        """Verifica se serve un retrain (ogni XGB_RETRAIN_EVERY errori)."""
        error_count = self.db.get_error_count()
        return error_count > 0 and error_count % XGB_RETRAIN_EVERY == 0
