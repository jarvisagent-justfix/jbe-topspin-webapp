"""
JBE TopSpin — Strato 5: Value Detection + Kelly

Confronta le probabilita' del modello con le quote del bookmaker.
Trova edge su 4 mercati: match winner, game handicap, O/U games, set betting.
"""
from datetime import date
from typing import Optional, List, Dict
from dataclasses import dataclass

from config import (
    MIN_EDGE, CONSENSUS_THRESHOLD, MIN_CONFIDENCE,
    KELLY_FRACTION, MAX_STAKE_PCT, MAX_DAILY_EXPOSURE_PCT,
    MAX_TOURNAMENT_EXPOSURE_PCT, STOP_LOSS_CONSECUTIVE, DRAWDOWN_STOP
)
from database import TennisDatabase


def humanize_stake(stake: float) -> float:
    """
    Arrotonda Kelly a valore umano.
    Un algoritmo punta 4.23€. Un umano punta 4.00€ o 4.50€.
    """
    if stake < 0.50:
        return 0.0
    rounded = round(stake * 2) / 2
    if abs(rounded - round(stake)) < 0.3:
        return float(round(stake))
    return max(rounded, 0.5)


@dataclass
class ValueBet:
    """Una value bet identificata dal sistema."""
    match_id: int
    market: str                    # match_winner, game_handicap, over_under, set_betting
    selection: str                 # player_name, over, under, 2-0, ecc.
    odds: float                    # Quota del bookmaker
    model_prob: float              # Probabilita' del modello
    edge: float                    # Edge = model_prob - 1/odds
    stake: float                   # Stake suggerito (Kelly)
    confidence: str                # HIGH, MEDIUM, LOW
    reason: str                    # Perche' e' una value bet

    def to_discord_message(self, bankroll: float) -> str:
        """Formatta la bet per Discord con stake umanizzato."""
        pct = self.stake / bankroll * 100 if bankroll > 0 else 0
        return (
            f"**{self.selection}** @{self.odds:.2f}\n"
            f"  Modello: {self.model_prob:.1%} | Implicita: {1/self.odds:.1%} | Edge: {self.edge:.1%}\n"
            f"  Stake: {self.stake:.2f} EUR ({pct:.1f}% bankroll) | Confidenza: {self.confidence}\n"
            f"  {self.reason}"
        )


class KellyCalculator:
    """Calcola lo stake ottimale usando il Kelly Criterion."""

    def __init__(self, initial_bankroll: float = 200.0):
        self.bankroll = initial_bankroll
        self.peak_bankroll = initial_bankroll
        self.consecutive_losses = 0
        self.daily_exposure = 0.0
        self.current_date = None

    def calculate_stake(self, edge: float, odds: float, bankroll: float = None) -> float:
        """Calcola lo stake Kelly 12.5% con cap e arrotondamento umano."""
        bk = bankroll or self.bankroll
        if edge <= 0 or odds <= 1.0:
            return 0.0

        # Kelly frazionario: f = fraction * edge / (odds - 1)
        kelly_pct = KELLY_FRACTION * edge / (odds - 1)
        stake = bk * kelly_pct

        # Cap: max 5% del bankroll
        max_stake = bk * MAX_STAKE_PCT
        stake = min(stake, max_stake)

        # Cap: max esposizione giornaliera
        remaining_daily = bk * MAX_DAILY_EXPOSURE_PCT - self.daily_exposure
        stake = min(stake, remaining_daily)

        stake = max(stake, 0.0)
        return humanize_stake(stake)

    def record_result(self, stake: float, result: float, match_date: date):
        """
        Registra il risultato di una scommessa.
        
        Args:
            stake: Puntata
            result: Profitto (positivo) o perdita (negativo)
            match_date: Data del match
        """
        self.bankroll += result
        
        if result > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        
        # Aggiorna picco
        if self.bankroll > self.peak_bankroll:
            self.peak_bankroll = self.bankroll
        
        # Reset esposizione giornaliera se nuovo giorno
        if self.current_date != match_date:
            self.daily_exposure = 0.0
            self.current_date = match_date
        
        self.daily_exposure += stake

    def is_stopped(self) -> bool:
        """Verifica se il sistema e' in stop."""
        # No chasing: 3 perdite consecutive
        if self.consecutive_losses >= STOP_LOSS_CONSECUTIVE:
            return True
        
        # Drawdown stop: -25% dal picco
        drawdown = (self.peak_bankroll - self.bankroll) / self.peak_bankroll
        if drawdown >= DRAWDOWN_STOP:
            return True
        
        return False


class ValueDetector:
    """
    Rileva value bet su 4 mercati.
    Confronta probabilita' del modello con le quote del bookmaker.
    """

    def __init__(self, db: TennisDatabase):
        self.db = db
        self.kelly = KellyCalculator()

    def find_value_bets(self, match_id: int, player1_name: str, player2_name: str,
                       prob_p1: float, prob_p2: float,
                       match_date: date, tournament: str,
                       surface: str = None) -> List[ValueBet]:
        """
        Trova value bet per un match.
        
        Args:
            match_id: ID del match nel DB
            player1_name: Nome giocatore 1
            player2_name: Nome giocatore 2
            prob_p1: Probabilita' modello per giocatore 1
            prob_p2: Probabilita' modello per giocatore 2
            match_date: Data del match
            tournament: Nome torneo
        
        Returns:
            Lista di ValueBet trovate
        """
        bets = []

        # Ottieni le quote dal DB
        odds = self.db.conn.execute(
            """SELECT * FROM tennis_odds WHERE match_id=? AND bookmaker='Pinnacle'""",
            (match_id,),
        ).fetchone()

        if not odds:
            # Fallback a Bet365
            odds = self.db.conn.execute(
                """SELECT * FROM tennis_odds WHERE match_id=? AND bookmaker='Bet365'""",
                (match_id,),
            ).fetchone()

        if not odds:
            return bets  # Nessuna quota disponibile

        # === Mercato 1: Match Winner ===
        for player_name, prob, odd_key in [
            (player1_name, prob_p1, "odds_winner"),
            (player2_name, prob_p2, "odds_loser"),
        ]:
            odd = odds[odd_key]
            if not odd or odd <= 1.0:
                continue

            implied_prob = 1.0 / odd
            edge = prob - implied_prob

            if prob >= MIN_CONFIDENCE and edge >= MIN_EDGE:
                # Market consensus check
                consensus_odds = self._get_consensus_odds(match_id, odd_key)
                if consensus_odds and abs(odd - consensus_odds) / consensus_odds > CONSENSUS_THRESHOLD:
                    # Edge dimezzato se quota fuori dal consenso
                    edge *= 0.5

                stake = self.kelly.calculate_stake(edge, odd)
                confidence = self._get_confidence(edge, prob)

                if stake >= 0.5:  # Minimo 0.50 EUR
                    bets.append(ValueBet(
                        match_id=match_id,
                        market="match_winner",
                        selection=player_name,
                        odds=odd,
                        model_prob=prob,
                        edge=edge,
                        stake=stake,
                        confidence=confidence,
                        reason=f"Edge +{edge:.1%} su match winner (modello {prob:.0%} vs quota {1/odd:.0%})"
                    ))

        # === Mercato 2: Game Handicap (se disponibile) ===
        try:
            if odds["handicap_line"] and odds["handicap_odds_fav"]:
                pass
        except (IndexError, KeyError):
            pass

        # === Mercato 3: Over/Under Games (se disponibile) ===
        try:
            if odds["total_line"] and odds["over_odds"]:
                pass
        except (IndexError, KeyError):
            pass

        # === Mercato 4: Set Betting (se disponibile) ===
        for set_key, set_name in [
            ("odds_2_0_fav", "2-0"),
            ("odds_2_1_fav", "2-1"),
        ]:
            try:
                if set_key in dict(odds):
                    pass
            except (IndexError, KeyError):
                pass

        return bets

    def _get_consensus_odds(self, match_id: int, odd_column: str) -> Optional[float]:
        """Calcola la quota media di tutti i bookmaker per un match."""
        # Per ora semplice: ritorna la media delle quote Pinnacle e Bet365
        cur = self.db.conn.execute(
            f"""SELECT AVG({odd_column}) as avg_odds 
               FROM tennis_odds WHERE match_id=? AND {odd_column} IS NOT NULL""",
            (match_id,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    def _get_confidence(self, edge: float, prob: float) -> str:
        """Determina il livello di confidenza."""
        if edge >= 0.10 and prob >= 0.65:
            return "HIGH"
        elif edge >= 0.07 and prob >= 0.55:
            return "MEDIUM"
        else:
            return "LOW"

    def update_bankroll(self, stake: float, result: float, match_date: date):
        """Aggiorna il bankroll dopo una scommessa."""
        self.kelly.record_result(stake, result, match_date)
