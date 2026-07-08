"""
JBE TopSpin — Strato 5: Value Detection + Kelly
================================================
Confronta le probabilità del modello con le quote del bookmaker.
Trova edge su 4 mercati: match winner, game handicap, O/U games, set betting.

Perché Kelly 12.5%:
  Il Kelly Criterion puro (100%) è matematicamente ottimale per la crescita
  del bankroll nel lungo periodo, ma è troppo aggressivo per il betting reale.
  Una perdita del 50% richiede un guadagno del 100% per recuperare.
  Il 12.5% (1/8 di Kelly) bilancia crescita e protezione del bankroll.

Perché humanize_stake:
  Un algoritmo punta 4.23€. Un umano punta 4.00€ o 4.50€.
  Arrotondiamo a multipli di 0.50€ per coerenza e semplicità.
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
    Arrotonda Kelly a valore umano (multipli di 0.50€).
    
    Perché: stake tipo 4.23€ sono anti-intuitivi nella gestione
    del bankroll. 4.00€ o 4.50€ sono più facili da tracciare.
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
    market: str
    selection: str
    odds: float
    model_prob: float
    edge: float
    stake: float
    confidence: str
    reason: str

    def to_discord_message(self, bankroll: float = 200) -> str:
        """Formatta la bet per messaggio Discord."""
        market_icons = {"match_winner": "🎯", "over_under": "📈", "game_handicap": "⚖️"}
        icon = market_icons.get(self.market, "🎲")
        return (f"{icon} {self.selection[:30]:30s} | "
                f"quota {self.odds:.2f} | "
                f"edge {self.edge:.1%} | "
                f"stake {self.stake:.2f}€ | "
                f"{self.confidence}")


class KellyCalculator:
    """
    Calcola lo stake ottimale usando il Kelly Criterion frazionario.
    
    Perché stop loss a 3 consecutive:
      Il Kelly Criterion presuppone che le probabilità siano corrette.
      Se perdiamo 3 bet di fila, qualcosa potrebbe essere sbagliato
      nel modello o nei dati. Fermarsi 24h permette di investigare.
      
    Perché drawdown stop al 25%:
      Una perdita del 25% richiede un guadagno del 33% per recuperare.
      Oltre questo punto, la pressione psicologica e la riduzione del
      bankroll rendono difficile operare razionalmente.
    """

    def __init__(self, initial_bankroll: float = 200.0):
        self.bankroll = initial_bankroll
        self.peak_bankroll = initial_bankroll
        self.consecutive_losses = 0
        self.daily_exposure = 0.0
        self.current_date = None

    def calculate_stake(self, edge: float, odds: float, bankroll: float = None) -> float:
        """
        Calcola lo stake Kelly 12.5% con cap e arrotondamento umano.
        
        Formula: f = KELLY_FRACTION * edge / (odds - 1)
        
        Perché KELLY_FRACTION = 0.125:
          Kelly 100%: f = edge / (odds - 1)
          Kelly 12.5%: f = 0.125 * edge / (odds - 1)
          Il Kelly frazionario riduce la varianza senza sacrificare
          troppo la crescita nel lungo periodo.
        """
        bk = bankroll or self.bankroll
        if edge <= 0 or odds <= 1.0:
            return 0.0
        kelly_pct = KELLY_FRACTION * edge / (odds - 1)
        stake = bk * kelly_pct
        max_stake = bk * MAX_STAKE_PCT
        stake = min(stake, max_stake)
        remaining_daily = bk * MAX_DAILY_EXPOSURE_PCT - self.daily_exposure
        stake = min(stake, remaining_daily)
        stake = max(stake, 0.0)
        return humanize_stake(stake)

    def record_result(self, stake: float, result: float, match_date: date):
        """Registra il risultato di una scommessa."""
        self.bankroll += result
        if result > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        if self.bankroll > self.peak_bankroll:
            self.peak_bankroll = self.bankroll
        if self.current_date != match_date:
            self.daily_exposure = 0.0
            self.current_date = match_date
        self.daily_exposure += stake

    def is_stopped(self) -> bool:
        """Verifica se il sistema è in stop."""
        if self.consecutive_losses >= STOP_LOSS_CONSECUTIVE:
            return True
        drawdown = (self.peak_bankroll - self.bankroll) / self.peak_bankroll
        if drawdown >= DRAWDOWN_STOP:
            return True
        return False


class ValueDetector:
    """
    Rileva value bet su 4 mercati.
    
    Perché solo match_winner è implementato qui:
      Game Handicap, Over/Under e Set Betting sono implementati
      direttamente in odds_api.py::predict_and_find_value() perché
      richiedono l'output del modello Markov (MarkovMatchModel),
      mentre questo detector è pensato per logica standalone.
      
      Refactoring futuro: spostare TUTTA la logica di value detection
      qui dentro, rendendo odds_api.py solo un orchestratore.
    """

    def __init__(self, db: TennisDatabase):
        self.db = db
        self.kelly = KellyCalculator()

    def find_value_bets(self, match_id: int, player1_name: str, player2_name: str,
                       prob_p1: float, prob_p2: float,
                       match_date: date, tournament: str,
                       surface: str = None) -> List[ValueBet]:
        """Trova value bet per un match (solo match_winner)."""
        bets = []
        odds = self.db.conn.execute(
            """SELECT * FROM tennis_odds WHERE match_id=? AND bookmaker='Pinnacle'""",
            (match_id,),
        ).fetchone()
        if not odds:
            odds = self.db.conn.execute(
                """SELECT * FROM tennis_odds WHERE match_id=? AND bookmaker='Bet365'""",
                (match_id,),
            ).fetchone()
        if not odds:
            return bets

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
                consensus_odds = self._get_consensus_odds(match_id, odd_key)
                if consensus_odds and abs(odd - consensus_odds) / consensus_odds > CONSENSUS_THRESHOLD:
                    edge *= 0.5
                stake = self.kelly.calculate_stake(edge, odd)
                confidence = self._get_confidence(edge, prob)
                if stake >= 0.5:
                    bets.append(ValueBet(
                        match_id=match_id,
                        market="match_winner",
                        selection=player_name,
                        odds=odd,
                        model_prob=prob,
                        edge=edge,
                        stake=stake,
                        confidence=confidence,
                        reason=f"Edge +{edge:.1%} su match winner"
                    ))
        return bets

    def _get_consensus_odds(self, match_id: int, odd_column: str) -> Optional[float]:
        """
        Calcola la quota media di tutti i bookmaker per un match.
        
        Perché consensus check:
          Se un bookmaker offre una quota molto diversa dalla media
          degli altri, potrebbe essere un errore. Invece di scartare
          del tutto, dimezziamo l'edge per ridurre il rischio.
        """
        cur = self.db.conn.execute(
            f"SELECT AVG({odd_column}) as avg_odds FROM tennis_odds WHERE match_id=? AND {odd_column} IS NOT NULL",
            (match_id,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    def _get_confidence(self, edge: float, prob: float) -> str:
        """Determina il livello di confidenza in base a edge e probabilità."""
        if edge >= 0.10 and prob >= 0.65:
            return "HIGH"
        elif edge >= 0.07 and prob >= 0.55:
            return "MEDIUM"
        else:
            return "LOW"

    def update_bankroll(self, stake: float, result: float, match_date: date):
        """Aggiorna il bankroll dopo una scommessa."""
        self.kelly.record_result(stake, result, match_date)
