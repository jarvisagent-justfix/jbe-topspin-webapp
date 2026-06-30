"""
JBE TopSpin — Strato 3: Fattori Contestuali

Aggiunge al modello tutte le variabili che l'ELO e il Markov non catturano:
- Fatica: match giocati nei giorni precedenti
- Recupero: ore tra un match e l'altro
- H2H superficie: record testa a testa sulla stessa superficie
- Differenza d'eta'
- Injury score: match saltati per infortunio
- Momentum: risultati recenti
- Break point saved %
- Ranking gap
"""
from datetime import date, timedelta
from typing import Optional, Dict, List
from database import TennisDatabase


class ContextualFactors:
    """Calcola i fattori contestuali per un match di tennis."""

    def __init__(self, db: TennisDatabase):
        self.db = db

    def get_fatigue(self, player_id: int, match_date: date) -> dict:
        """
        Calcola la fatica del giocatore: match giocati nei giorni precedenti.
        
        Returns:
            dict con: matches_last_7d, matches_last_14d, matches_last_30d,
                     consecutive_days_played, is_back_to_back
        """
        results = {
            "matches_last_7d": 0,
            "matches_last_14d": 0,
            "matches_last_30d": 0,
            "consecutive_days": 0,
        }

        d7 = (match_date - timedelta(days=7)).isoformat()
        d14 = (match_date - timedelta(days=14)).isoformat()
        d30 = (match_date - timedelta(days=30)).isoformat()

        # Match negli ultimi 7 giorni
        cur = self.db.conn.execute(
            """SELECT COUNT(*) as cnt, MAX(match_date) as last_match
               FROM tennis_matches 
               WHERE (winner_id=? OR loser_id=?)
               AND match_date >= ? AND match_date < ?""",
            (player_id, player_id, d7, match_date.isoformat()),
        )
        row = cur.fetchone()
        results["matches_last_7d"] = row["cnt"] if row else 0
        last_match = row["last_match"] if row else None

        # Match negli ultimi 14 e 30 giorni
        cur = self.db.conn.execute(
            "SELECT COUNT(*) FROM tennis_matches WHERE (winner_id=? OR loser_id=?) AND match_date >= ? AND match_date < ?",
            (player_id, player_id, d14, match_date.isoformat()),
        )
        results["matches_last_14d"] = cur.fetchone()[0]

        cur = self.db.conn.execute(
            "SELECT COUNT(*) FROM tennis_matches WHERE (winner_id=? OR loser_id=?) AND match_date >= ? AND match_date < ?",
            (player_id, player_id, d30, match_date.isoformat()),
        )
        results["matches_last_30d"] = cur.fetchone()[0]

        # Back-to-back: match il giorno prima
        if last_match:
            yesterday = (match_date - timedelta(days=1)).isoformat()
            results["is_back_to_back"] = 1 if last_match == yesterday else 0
        else:
            results["is_back_to_back"] = 0

        return results

    def get_h2h(self, player1_id: int, player2_id: int, surface: Optional[str] = None) -> dict:
        """
        Calcola il record H2H tra due giocatori.
        
        Returns:
            dict con: total_wins_p1, total_wins_p2, total_matches,
                     surface_wins_p1, surface_wins_p2, surface_matches,
                     recent_wins_p1, recent_wins_p2, recent_matches
        """
        results = {
            "h2h_total_wins_p1": 0,
            "h2h_total_wins_p2": 0,
            "h2h_total_matches": 0,
            "h2h_surface_wins_p1": 0,
            "h2h_surface_wins_p2": 0,
            "h2h_recent_wins_p1": 0,
            "h2h_recent_wins_p2": 0,
        }

        # H2H totale (tutte le superfici)
        cur = self.db.conn.execute(
            """SELECT winner_id, COUNT(*) as cnt 
               FROM tennis_matches 
               WHERE (winner_id=? AND loser_id=?) OR (winner_id=? AND loser_id=?)
               GROUP BY winner_id""",
            (player1_id, player2_id, player2_id, player1_id),
        )
        for row in cur.fetchall():
            if row["winner_id"] == player1_id:
                results["h2h_total_wins_p1"] = row["cnt"]
            elif row["winner_id"] == player2_id:
                results["h2h_total_wins_p2"] = row["cnt"]
        results["h2h_total_matches"] = results["h2h_total_wins_p1"] + results["h2h_total_wins_p2"]

        # H2H sulla stessa superficie
        if surface:
            cur = self.db.conn.execute(
                """SELECT winner_id, COUNT(*) as cnt 
                   FROM tennis_matches 
                   WHERE ((winner_id=? AND loser_id=?) OR (winner_id=? AND loser_id=?))
                   AND surface=?
                   GROUP BY winner_id""",
                (player1_id, player2_id, player2_id, player1_id, surface),
            )
            for row in cur.fetchall():
                if row["winner_id"] == player1_id:
                    results["h2h_surface_wins_p1"] = row["cnt"]
                elif row["winner_id"] == player2_id:
                    results["h2h_surface_wins_p2"] = row["cnt"]
            results["h2h_surface_matches"] = results["h2h_surface_wins_p1"] + results["h2h_surface_wins_p2"]

        return results

    def get_momentum(self, player_id: int, match_date: date, surface: Optional[str] = None) -> dict:
        """
        Calcola il momento di forma del giocatore.
        
        Returns:
            dict con: last_5_wins, last_5_total, last_10_wins, last_10_total,
                     win_streak, surface_last_5_wins, surface_last_5_total
        """
        results = {
            "last_5_wins": 0,
            "last_5_total": 0,
            "last_10_wins": 0,
            "last_10_total": 0,
            "win_streak": 0,
            "surface_last_5_wins": 0,
            "surface_last_5_total": 0,
        }

        # Ultimi 5 match
        cur = self.db.conn.execute(
            """SELECT winner_id FROM tennis_matches 
               WHERE (winner_id=? OR loser_id=?) AND match_date < ?
               ORDER BY match_date DESC LIMIT 5""",
            (player_id, player_id, match_date.isoformat()),
        )
        matches = cur.fetchall()
        results["last_5_total"] = len(matches)
        results["last_5_wins"] = sum(1 for m in matches if m["winner_id"] == player_id)

        # Win streak
        streak = 0
        cur = self.db.conn.execute(
            """SELECT winner_id FROM tennis_matches 
               WHERE (winner_id=? OR loser_id=?) AND match_date < ?
               ORDER BY match_date DESC""",
            (player_id, player_id, match_date.isoformat()),
        )
        for m in cur.fetchall():
            if m["winner_id"] == player_id:
                streak += 1
            else:
                break
        results["win_streak"] = streak

        # Ultimi 10 match
        if results["last_5_total"] == 5:
            cur = self.db.conn.execute(
                """SELECT winner_id FROM tennis_matches 
                   WHERE (winner_id=? OR loser_id=?) AND match_date < ?
                   ORDER BY match_date DESC LIMIT 10""",
                (player_id, player_id, match_date.isoformat()),
            )
            matches = cur.fetchall()
            results["last_10_total"] = len(matches)
            results["last_10_wins"] = sum(1 for m in matches if m["winner_id"] == player_id)

        return results

    def get_age(self, player_id: int, match_date: date) -> Optional[float]:
        """Calcola l'eta' del giocatore in anni."""
        try:
            row = self.db.conn.execute(
                "SELECT birth_date FROM players WHERE id=?",
                (player_id,),
            ).fetchone()
            if not row or not row["birth_date"]:
                return None
            
            birth = date.fromisoformat(str(row["birth_date"]))
            return (match_date - birth).days / 365.25
        except Exception:
            return None

    def get_injury_score(self, player_id: int, match_date: date) -> int:
        """
        Calcola un punteggio di rischio infortunio basato su:
        - Ritiri recenti (ultimi 12 mesi)
        - Match saltati
        """
        d365 = (match_date - timedelta(days=365)).isoformat()
        
        # Ritiri come vincitore (inusuale) o perdente
        cur = self.db.conn.execute(
            """SELECT COUNT(*) as cnt FROM tennis_matches 
               WHERE (winner_id=? OR loser_id=?) 
               AND match_date >= ? AND match_date < ?
               AND (retirement=1 OR walkover=1)""",
            (player_id, player_id, d365, match_date.isoformat()),
        )
        retirements = cur.fetchone()[0] or 0

        return min(retirements, 10)  # Cap a 10

    def get_tournament_stage(self, round_val: Optional[str]) -> float:
        """
        Converte il round in un valore numerico (0-1).
        Piu' alto = fasi finali del torneo.
        """
        if not round_val:
            return 0.0
        round_map = {
            "R128": 0.0, "R64": 0.1, "R32": 0.2, "R16": 0.35,
            "QF": 0.5, "SF": 0.7, "F": 0.9, "RR": 0.3,
        }
        return round_map.get(round_val, 0.3)

    def compute_all(self, player1_id: int, player2_id: int, 
                    surface: str, match_date: date,
                    round_val: Optional[str] = None,
                    tour_level: Optional[str] = None) -> dict:
        """
        Calcola TUTTI i fattori contestuali per un match.
        
        Returns:
            dict con tutte le feature contestuali
        """
        features = {}

        # Fatigue
        f_p1 = self.get_fatigue(player1_id, match_date)
        f_p2 = self.get_fatigue(player2_id, match_date)
        features["fatigue_p1_7d"] = f_p1["matches_last_7d"]
        features["fatigue_p2_7d"] = f_p2["matches_last_7d"]
        features["fatigue_diff_7d"] = f_p1["matches_last_7d"] - f_p2["matches_last_7d"]
        features["b2b_p1"] = f_p1["is_back_to_back"]
        features["b2b_p2"] = f_p2["is_back_to_back"]

        # H2H
        h2h = self.get_h2h(player1_id, player2_id, surface)
        features["h2h_total_p1"] = h2h["h2h_total_wins_p1"]
        features["h2h_total_p2"] = h2h["h2h_total_wins_p2"]
        features["h2h_surface_p1"] = h2h.get("h2h_surface_wins_p1", 0)
        features["h2h_surface_p2"] = h2h.get("h2h_surface_wins_p2", 0)
        features["h2h_total_matches"] = h2h["h2h_total_matches"]

        # Momentum
        m_p1 = self.get_momentum(player1_id, match_date, surface)
        m_p2 = self.get_momentum(player2_id, match_date, surface)
        features["momentum_p1_5"] = m_p1["last_5_wins"] / max(m_p1["last_5_total"], 1)
        features["momentum_p2_5"] = m_p2["last_5_wins"] / max(m_p2["last_5_total"], 1)
        features["win_streak_p1"] = m_p1["win_streak"]
        features["win_streak_p2"] = m_p2["win_streak"]

        # Age
        age1 = self.get_age(player1_id, match_date)
        age2 = self.get_age(player2_id, match_date)
        if age1 and age2:
            features["age_p1"] = round(age1, 1)
            features["age_p2"] = round(age2, 1)
            features["age_diff"] = round(age1 - age2, 1)

        # Injury
        features["injury_p1"] = self.get_injury_score(player1_id, match_date)
        features["injury_p2"] = self.get_injury_score(player2_id, match_date)

        # Tournament stage
        features["tournament_stage"] = self.get_tournament_stage(round_val)

        # Tournament level (encoded)
        level_map = {"G": 3, "M": 2, "A": 1, "C": 0, "F": 2}
        features["tour_level_num"] = level_map.get(tour_level or "A", 1)

        # Surface fixed effects
        features["is_hard"] = 1 if surface == "Hard" else 0
        features["is_clay"] = 1 if surface == "Clay" else 0
        features["is_grass"] = 1 if surface == "Grass" else 0
        features["is_carpet"] = 1 if surface == "Carpet" else 0

        return features
