"""
JBE TopSpin — Strato 1: Surface-Specific ELO Dinamico

Ogni giocatore ha 4 rating superficie-specifici + 1 rating overall.
Il blended rating usa un peso dinamico basato sulla confidenza (n. match su superficie).
Include decay temporale e K-factor dinamico per assenze.
"""
import math
from datetime import datetime, date
from typing import Optional

from config import (
    ELO_DEFAULT_RATING, ELO_K_FACTOR, ELO_K_INJURY_MULTIPLIER,
    ELO_DECAY_DAYS, ELO_BO5_FACTOR, ELO_SURFACE_MIN_CONF, SURFACES
)


class ELORating:
    """Rating ELO per un giocatore su tutte le superfici."""

    def __init__(self, overall=ELO_DEFAULT_RATING, hard=ELO_DEFAULT_RATING,
                 clay=ELO_DEFAULT_RATING, grass=ELO_DEFAULT_RATING,
                 carpet=ELO_DEFAULT_RATING, mov=ELO_DEFAULT_RATING,
                 matches_played=0, matches_hard=0, matches_clay=0,
                 matches_grass=0, matches_carpet=0, last_date=None):
        self.overall = overall
        self.hard = hard
        self.clay = clay
        self.grass = grass
        self.carpet = carpet
        self.mov = mov
        self.matches_played = matches_played
        self.matches_hard = matches_hard
        self.matches_clay = matches_clay
        self.matches_grass = matches_grass
        self.matches_carpet = matches_carpet
        # Normalizza last_date: stringa -> date object
        if isinstance(last_date, str):
            try:
                self.last_date = date.fromisoformat(last_date)
            except (ValueError, TypeError):
                self.last_date = None
        else:
            self.last_date = last_date  # Data ultimo match

    def get_surface_rating(self, surface: str) -> float:
        """Ritorna il rating per una specifica superficie."""
        surface_map = {
            "Hard": self.hard,
            "Clay": self.clay,
            "Grass": self.grass,
            "Carpet": self.carpet,
        }
        return surface_map.get(surface, self.overall)

    def get_surface_matches(self, surface: str) -> int:
        """Ritorna il numero di match su una superficie."""
        surface_map = {
            "Hard": self.matches_hard,
            "Clay": self.matches_clay,
            "Grass": self.matches_grass,
            "Carpet": self.matches_carpet,
        }
        return surface_map.get(surface, 0)

    def get_blended_rating(self, surface: str) -> float:
        """
        Rating blended: combina overall + superficie con peso dinamico.
        
        Se un giocatore ha pochi match su una superficie, il blend 
        si sposta verso l'overall. Con match a sufficienza, diventa 50/50.
        """
        surface_rating = self.get_surface_rating(surface)
        n_matches = self.get_surface_matches(surface)

        # Confidence: da 0 a 0.5 in base ai match su superficie
        confidence = min(n_matches / (2 * ELO_SURFACE_MIN_CONF), 0.5)
        
        # Peso dell'overall: 1 - confidence (invece del classico 0.5)
        # Se confidence=0 (nessun match), blend = overall puro
        # Se confidence=0.5 (50+ match), blend = 50/50
        weight_overall = 1.0 - confidence
        
        return weight_overall * self.overall + confidence * surface_rating

    def get_k_factor(self, months_since_last_match: int = 0) -> float:
        """
        K-factor dinamico: aumenta dopo assenze prolungate.
        Base = ELO_K_FACTOR (default 32).
        +10% per ogni mese di assenza oltre il primo.
        """
        k = ELO_K_FACTOR
        if months_since_last_match > 3:
            extra_months = months_since_last_match - 3
            k += k * ELO_K_INJURY_MULTIPLIER * extra_months
        return k

    def expected_score(self, opponent_rating: float) -> float:
        """
        Probabilita' di vittoria basata sulla differenza ELO.
        Formula standard: 1 / (1 + 10^((R_opp - R_self) / 400))
        """
        return 1.0 / (1.0 + math.pow(10, (opponent_rating - self.overall) / 400))

    def update(self, opponent_rating: float, score: float, 
               surface: str, match_date: date, is_best_of_5: bool = False,
               games_won: int = 0, games_lost: int = 0):
        """
        Aggiorna il rating dopo un match.
        
        Args:
            opponent_rating: Rating ELO dell'avversario (blended)
            score: 1.0 per vittoria, 0.0 per sconfitta
            surface: Superficie del match
            match_date: Data del match
            is_best_of_5: Flag per match Bo5
            games_won: Game vinti (per MoV)
            games_lost: Game persi (per MoV)
        """
        # Calcola mesi dall'ultimo match
        months_since = 0
        if self.last_date and match_date:
            delta = (match_date - self.last_date).days
            months_since = delta // 30

        k = self.get_k_factor(months_since)

        # Expected score con blended rating
        expected = self.expected_score(opponent_rating)

        # MoV (Margin of Victory) adjustment
        # Una vittoria 6-0 6-0 conta di piu' di 7-6 7-6
        mov_factor = 1.0
        if score == 1.0 and games_lost > 0:
            mov_factor = math.log(max(games_won, 1) / max(games_lost, 1) + 1)
            k_mov = k * mov_factor
        elif score == 0.0 and games_won > 0:
            mov_factor = math.log(max(games_lost, 1) / max(games_won, 1) + 1)
            k_mov = k * mov_factor * 1.5  # Penalta' maggiore per sconfitte nette
        else:
            k_mov = k

        # Bo5 adjustment: i favoriti vincono piu' spesso in Bo5
        if is_best_of_5 and score == 1.0:
            # Il favorito vince piu' spesso in Bo5
            # Aumentiamo leggermente l'aggiornamento per vittorie in Bo5
            k_mov *= 1.1

        # Aggiornamento standard ELO
        delta = k_mov * (score - expected)

        self.overall += delta
        # Mov rating update (Margin of Victory adjusted)
        self.mov += delta * min(max(mov_factor, 1.0), 2.0)

        # Aggiornamento superficie-specifico
        surface_attr_map = {
            "Hard": "hard",
            "Clay": "clay", 
            "Grass": "grass",
            "Carpet": "carpet",
        }
        surface_attr = surface_attr_map.get(surface)
        if surface_attr:
            setattr(self, surface_attr, getattr(self, surface_attr) + delta)
            # Aggiorna contatore match per superficie
            match_attr = f"matches_{surface.lower()}"
            setattr(self, match_attr, getattr(self, match_attr) + 1)

        self.matches_played += 1
        self.last_date = match_date

    def apply_decay(self, current_date: date):
        """
        Decay temporale: riduce rating per giocatori inattivi.
        Dopo ELO_DECAY_DAYS giorni, il rating decade verso ELO_DEFAULT_RATING.
        """
        if not self.last_date or not current_date:
            return
        days_inactive = (current_date - self.last_date).days
        if days_inactive <= ELO_DECAY_DAYS:
            return
        
        # Decay: dopo 2x giorni di inattività, perde fino al 50% verso default
        decay_ratio = min(days_inactive / (ELO_DECAY_DAYS * 2), 0.5)
        
        for attr in ['overall', 'hard', 'clay', 'grass', 'carpet', 'mov']:
            current = getattr(self, attr)
            decayed = current - (current - ELO_DEFAULT_RATING) * decay_ratio
            setattr(self, attr, decayed)


class SurfaceELOEngine:
    """
    Motore ELO superficie-specifico.
    Gestisce i rating di tutti i giocatori e le predizioni.
    """

    def __init__(self, db):
        self.db = db
        self.ratings = {}  # {player_id: ELORating}

    def predict_winner(self, player1_id: int, player2_id: int, 
                       surface: str, is_best_of_5: bool = False) -> dict:
        """
        Predice il vincitore e calcola le probabilita'.
        
        Returns:
            dict con: prob_player1, prob_player2, elo_diff, blended_diff
        """
        r1 = self._get_or_create_rating(player1_id)
        r2 = self._get_or_create_rating(player2_id)

        blended1 = r1.get_blended_rating(surface)
        blended2 = r2.get_blended_rating(surface)

        # Probabilita' base (ELO standard)
        prob1 = 1.0 / (1.0 + math.pow(10, (blended2 - blended1) / 400))

        # Bo5 adjustment
        if is_best_of_5:
            # Il favorito (prob > 0.5) guadagna ~5% in Bo5
            if prob1 > 0.5:
                prob1 = min(prob1 + ELO_BO5_FACTOR, 0.95)
            else:
                prob1 = max(prob1 - ELO_BO5_FACTOR, 0.05)

        return {
            "prob_player1": prob1,
            "prob_player2": 1.0 - prob1,
            "elo_diff": r1.overall - r2.overall,
            "blended_diff": blended1 - blended2,
        }

    def record_match(self, winner_id: int, loser_id: int, surface: str,
                     match_date: date, is_best_of_5: bool = False,
                     winner_games: int = 0, loser_games: int = 0):
        """
        Registra un match e aggiorna i rating.
        """
        r_winner = self._get_or_create_rating(winner_id)
        r_loser = self._get_or_create_rating(loser_id)

        r_winner.apply_decay(match_date)
        r_loser.apply_decay(match_date)

        # Rating blended dell'avversario per l'update
        loser_blended = r_loser.get_blended_rating(surface)
        winner_blended = r_winner.get_blended_rating(surface)

        r_winner.update(
            opponent_rating=loser_blended, score=1.0,
            surface=surface, match_date=match_date,
            is_best_of_5=is_best_of_5,
            games_won=winner_games, games_lost=loser_games
        )
        r_loser.update(
            opponent_rating=winner_blended, score=0.0,
            surface=surface, match_date=match_date,
            is_best_of_5=is_best_of_5,
            games_won=loser_games, games_lost=winner_games
        )

    def _get_or_create_rating(self, player_id: int) -> ELORating:
        """Recupera o crea un rating per un giocatore."""
        if player_id not in self.ratings:
            # Prova a caricare dal DB
            row = self.db.get_latest_elo(player_id)
            if row:
                self.ratings[player_id] = ELORating(
                    overall=row["rating_overall"],
                    hard=row["rating_hard"],
                    clay=row["rating_clay"],
                    grass=row["rating_grass"],
                    carpet=row["rating_carpet"],
                    mov=row["rating_mov"],
                    matches_played=row["matches_played"],
                    matches_hard=row["matches_hard"],
                    matches_clay=row["matches_clay"],
                    matches_grass=row["matches_grass"],
                    matches_carpet=row["matches_carpet"],
                    last_date=row["rating_date"] if row["rating_date"] else None,
                )
            else:
                self.ratings[player_id] = ELORating()

        return self.ratings[player_id]

    def save_ratings(self, match_id: int, match_date: date):
        """Salva tutti i rating correnti nel DB."""
        for player_id, rating in self.ratings.items():
            self.db.conn.execute(
                """INSERT INTO elo_ratings 
                   (player_id, match_id, rating_date, 
                    rating_overall, rating_hard, rating_clay, rating_grass, rating_carpet, rating_mov,
                    matches_played, matches_hard, matches_clay, matches_grass, matches_carpet)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (player_id, match_id, match_date.isoformat() if hasattr(match_date, 'isoformat') else match_date,
                 rating.overall, rating.hard, rating.clay, rating.grass, rating.carpet, rating.mov,
                 rating.matches_played, rating.matches_hard, rating.matches_clay, rating.matches_grass, rating.matches_carpet),
            )
        self.db.conn.commit()

    def load_all_ratings(self):
        """Carica i rating di TUTTI i giocatori dal DB."""
        rows = self.db.conn.execute(
            """SELECT e.*, p.name FROM elo_ratings e
               JOIN players p ON p.id = e.player_id
               WHERE e.id IN (SELECT MAX(id) FROM elo_ratings GROUP BY player_id)"""
        ).fetchall()
        for row in rows:
            self.ratings[row["player_id"]] = ELORating(
                overall=row["rating_overall"],
                hard=row["rating_hard"],
                clay=row["rating_clay"],
                grass=row["rating_grass"],
                carpet=row["rating_carpet"],
                mov=row["rating_mov"],
                matches_played=row["matches_played"],
                matches_hard=row["matches_hard"],
                matches_clay=row["matches_clay"],
                matches_grass=row["matches_grass"],
                matches_carpet=row["matches_carpet"],
                last_date=row["rating_date"] if row["rating_date"] else None,
            )
