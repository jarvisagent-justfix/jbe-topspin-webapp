"""
JBE TopSpin — Strato 2: Serve/Return Markov Model

Modella il match a livello di punto usando catene di Markov.
Da p_serve (prob. vincere punto al servizio), calcola:
- P_win_game > P_win_set > P_win_match
- Distribuzione completa degli score (game totali, set spread)
- Game handicap probabilites
- Over/Under games probabilites

Punti di forza:
- Stima precisa di mercati secondari (game handicap, O/U)
- Parametri superficie-specifici
- Supporto Bo3 e Bo5
"""
import math
import random
from typing import Optional, List, Tuple, Dict


def p_win_game(p: float) -> float:
    """
    Probabilita' di vincere un game al servizio.
    
    Usa la formula Markoviana a 4 punti:
    - 4-0: p^4
    - 4-1: 4*p^4*(1-p)
    - 4-2: 10*p^4*(1-p)^2
    - Deuce: 20*p^3*(1-p)^3 * p^2 / (1 - 2*p*(1-p))
    
    Args:
        p: Probabilita' di vincere un singolo punto al servizio
    
    Returns:
        Probabilita' di vincere il game
    """
    if p <= 0 or p >= 1:
        return p
    
    # 4-0, 4-1, 4-2 (vittoria senza deuce)
    p_game = p**4 + 4 * p**4 * (1-p) + 10 * p**4 * (1-p)**2
    
    # Da 40-40 (deuce): probabilita' di vincere da deuce
    p_deuce = p**2 / (1 - 2*p*(1-p))
    
    # Probabilita' di arrivare a 40-40
    p_40_40 = 20 * p**3 * (1-p)**3
    
    p_game += p_40_40 * p_deuce
    
    return p_game


def p_win_tiebreak(p_serve: float, p_serve_opp: float = None) -> float:
    """
    Probabilita' di vincere un tiebreak (al meglio dei 7 punti).
    
    Il tiebreak alterna il servizio ogni 2 punti. La formula considera
    entrambi i giocatori: chi serve meglio ha vantaggio ridotto perche'
    nel tiebreak si alternano.
    
    Args:
        p_serve: Probabilita' del giocatore A di vincere un punto al servizio
        p_serve_opp: Probabilita' del giocatore B (opponente) di vincere un
                     punto al servizio. Se None, assume simmetria (p_b=1-p_a).
    
    Returns:
        Probabilita' che A vinca il tiebreak
    """
    if p_serve_opp is None:
        p_serve_opp = 1 - p_serve
    
    if p_serve <= 0 or p_serve >= 1:
        return float(p_serve)
    
    # Il tiebreak alterna: A serve 1 punto, B serve 2, A serve 2, ...
    # Vantaggio serve ridotto rispetto a un game normale (~40% invece di 100%)
    # Formula: P = 0.5 + (p_A - p_B) * 0.4 (approssimazione O'Malley)
    p_tb = 0.5 + (p_serve - p_serve_opp) * 0.4
    return max(0.01, min(0.99, p_tb))


def p_win_set(p_serve_A: float, p_serve_B: float, 
              return_p_A: Optional[float] = None,
              return_p_B: Optional[float] = None) -> float:
    """
    Probabilita' che il giocatore A vinca un set.
    
    Considera i game di servizio alternati:
    - A serve (P_win_game con p_serve_A)
    - B serve (P_win_game con p_serve_B)
    - ... alternati
    
    Args:
        p_serve_A: Prob. di A di vincere un punto al servizio
        p_serve_B: Prob. di B di vincere un punto al servizio
        return_p_A: (opzionale) Prob. di A di vincere in risposta
        return_p_B: (opzionale) Prob. di B di vincere in risposta
    
    Returns:
        Probabilita' che A vinca il set (senza considerare tiebreak)
    """
    # Probabilita' di vincere un game al servizio
    p_game_A = p_win_game(p_serve_A)
    p_game_B = p_win_game(p_serve_B)
    
    # Probabilita' di vincere un game in risposta
    if return_p_A is not None:
        p_return_A = return_p_A
    else:
        p_return_A = 1 - p_game_B
    
    if return_p_B is not None:
        p_return_B = return_p_B
    else:
        p_return_B = 1 - p_game_A
    
    # Catena di Markov per il set: 12 stati (0-0 a 6-6 + vantaggi)
    # Usiamo la formula ricorsiva
    
    # Matrice di transizione approssimata
    # q_A = probabilita' che A vinca un game (al suo servizio)
    # q_B = probabilita' che A vinca un game (al servizio di B) = 1 - P_game_B
    
    qA = p_game_A      # A vince un game al suo servizio
    qB = p_return_A    # A vince un game al servizio di B (break)
    
    # Probabilita' di vincere un game qualsiasi (alternando servizio)
    # A serve game 1, 3, 5, ...; B serve game 2, 4, 6, ...
    
    # Per calcolare P_set, usiamo la formula completa:
    # Dobbiamo simulare tutti i percorsi possibili
    
    def _p_score_to(to, from_g):
        """Prob. di passare da (gA, gB) = gA a (gA + to) in N game."""
        pass
    
    # Implementazione pratica: simulazione Markov 12x12
    # Stati: (game_vinti_A, game_vinti_B) per 0..6 ciascuno
    # La matrice di transizione ha dimensione 7x7 = 49 stati
    
    # Per efficienza, usiamo la formula chiusa di Whitaker (1973)
    # P_set = sum_{k=0}^{5} P(vincere 6 - k game quando l'avversario ne vince k)
    #        + P(5-5) * P(vincere 2 game consecutivi)
    #        + P(6-6) * P(vincere il tiebreak)
    
    # Costruiamo la distribuzione dei game usando Bernoulli trials
    
    # Prob. di vincere esattamente k game su N in alternanza
    # Con N = 10 (game totali per arrivare a 6-4 o 4-6)
    
    # APPORSSIMAZIONE PRATICA per performance:
    # Simula i game uno per uno con alternanza
    
    def _sim_set():
        """Simula un set game per game fino a 6-6 + tiebreak.
        DP sulle coppie (gA, gB).
        """
        dp = {(0, 0): 1.0}
        game_num = 0
        a_wins = 0.0
        b_wins = 0.0

        while True:
            game_num += 1
            is_A_serving = game_num % 2 == 1
            p_this_game = qA if is_A_serving else qB

            new_dp = {}
            for (gA, gB), prob in dp.items():
                # Check if this state is finished
                a_finished = (gA >= 6 and gA - gB >= 2) or (gA >= 7 and gB == 6)
                b_finished = (gB >= 6 and gB - gA >= 2) or (gB >= 7 and gA == 6)
                if a_finished:
                    a_wins += prob
                    continue
                if b_finished:
                    b_wins += prob
                    continue

                # Tiebreak at 6-6: use p_win_tiebreak, not game probability
                if gA == 6 and gB == 6:
                    tb_prob = p_win_tiebreak(p_serve_A, p_serve_B)
                    new_dp[(7, 6)] = new_dp.get((7, 6), 0) + prob * tb_prob
                    new_dp[(6, 7)] = new_dp.get((6, 7), 0) + prob * (1 - tb_prob)
                else:
                    new_dp[(gA + 1, gB)] = new_dp.get((gA + 1, gB), 0) + prob * p_this_game
                    new_dp[(gA, gB + 1)] = new_dp.get((gA, gB + 1), 0) + prob * (1 - p_this_game)

            dp = new_dp

            total = a_wins + b_wins
            if total > 0.9999:
                return a_wins / total

            if game_num > 50:
                return a_wins / total if total > 0 else 0.5

    return _sim_set()


class MarkovMatchModel:
    """
    Modello Markoviano completo per un match di tennis.
    Calcola probabilita' per tutti i mercati.
    """

    def __init__(self, p_serve_A: float, p_serve_B: float, surface: str,
                 best_of: int = 3):
        """
        Args:
            p_serve_A: Prob. giocatore A di vincere un punto al servizio
            p_serve_B: Prob. giocatore B di vincere un punto al servizio
            surface: Superficie del match
            best_of: 3 (Bo3) o 5 (Bo5)
        """
        self.p_serve_A = p_serve_A
        self.p_serve_B = p_serve_B
        self.surface = surface
        self.best_of = best_of

    def predict_match(self) -> dict:
        """
        Calcola tutte le probabilita' per il match.
        
        Returns:
            dict con:
            - p_win_match_A: Prob. A di vincere il match
            - p_win_set_A: Prob. A di vincere un set
            - p_win_game_A_serve: Prob. A di vincere un game al servizio
            - p_win_game_B_serve: Prob. B di vincere un game al servizio
            - p_2_0_A, p_2_1_A: Set betting probabilites (Bo3)
            - p_3_0_A, p_3_1_A, p_3_2_A: Set betting (Bo5)
            - expected_games_A, expected_games_B: Game attesi
            - expected_total_games: Game totali attesi
            - p_over_x_game[x]: Prob. over x.5 game
            - p_handicap_A[x]: Prob. A copre handicap x.5
        """
        p_game_A = p_win_game(self.p_serve_A)
        p_game_B = p_win_game(self.p_serve_B)
        p_return_A = 1 - p_game_B  # A vince game in risposta
        p_return_B = 1 - p_game_A  # B vince game in risposta

        p_set_A = p_win_set(self.p_serve_A, self.p_serve_B, p_return_A, p_return_B)
        p_set_B = 1 - p_set_A

        # Match winner
        if self.best_of == 3:
            p_2_0_A = p_set_A ** 2
            p_2_1_A = 2 * p_set_A ** 2 * p_set_B
            p_match_A = p_2_0_A + p_2_1_A
            
            p_2_0_B = p_set_B ** 2
            p_2_1_B = 2 * p_set_B ** 2 * p_set_A
            p_match_B = p_2_0_B + p_2_1_B
            
            set_betting_A = {"2-0": p_2_0_A, "2-1": p_2_1_A}
            set_betting_B = {"2-0": p_2_0_B, "2-1": p_2_1_B}
            
        else:  # Bo5
            p_3_0_A = p_set_A ** 3
            p_3_1_A = 3 * p_set_A ** 3 * p_set_B
            p_3_2_A = 6 * p_set_A ** 3 * p_set_B ** 2
            p_match_A = p_3_0_A + p_3_1_A + p_3_2_A
            
            p_3_0_B = p_set_B ** 3
            p_3_1_B = 3 * p_set_B ** 3 * p_set_A
            p_3_2_B = 6 * p_set_B ** 3 * p_set_A ** 2
            p_match_B = p_3_0_B + p_3_1_B + p_3_2_B
            
            set_betting_A = {"3-0": p_3_0_A, "3-1": p_3_1_A, "3-2": p_3_2_A}
            set_betting_B = {"3-0": p_3_0_B, "3-1": p_3_1_B, "3-2": p_3_2_B}


        # Game distribution via Monte Carlo
        game_dist = self._simulate_game_distribution(n_simulations=20000)

        return {
            "p_win_match": p_match_A,
            "p_loss_match": p_match_B,
            "p_win_set": p_set_A,
            "p_win_game_serve": p_game_A,
            "p_win_game_return": p_return_A,
            "set_betting_winner": set_betting_A,
            "set_betting_loser": set_betting_B,
            "expected_total_games": game_dist["expected_total_games"],
            "game_diff_dist": game_dist["game_diff_dist"],
            "total_games_dist": game_dist["total_games_dist"],
            "p_cover_handicap": game_dist["p_cover_handicap"],
            "p_over_threshold": game_dist["p_over_threshold"],
            "p_under_threshold": game_dist["p_under_threshold"],
        }

    def _simulate_game_distribution(self, n_simulations: int = 20000) -> dict:
        """
        Monte Carlo simulation del match per distribuzione game-level.
        Usata per calcolare probabilita' di handicap game e over/under.

        Returns:
            dict con distribuzioni empiriche di game diff e total games,
            e funzioni per calcolare handicap/O/U probabilities.
        """
        p_game_A = p_win_game(self.p_serve_A)
        p_game_B = p_win_game(self.p_serve_B)
        qA = p_game_A          # A vince game al servizio
        qB = 1 - p_game_B      # A vince game in risposta (break)

        needed_sets = 2 if self.best_of == 3 else 3

        game_diff_list = []
        total_games_list = []

        for _ in range(n_simulations):
            sets_A, sets_B = 0, 0
            total_gA, total_gB = 0, 0

            while sets_A < needed_sets and sets_B < needed_sets:
                gA, gB = 0, 0
                game_num = 0
                while True:
                    game_num += 1
                    is_A_serving = game_num % 2 == 1
                    p_this_game = qA if is_A_serving else qB

                    if random.random() < p_this_game:
                        gA += 1
                    else:
                        gB += 1

                    if max(gA, gB) >= 6:
                        if abs(gA - gB) >= 2:
                            break
                        if gA == 6 and gB == 6:
                            # Tiebreak: alternato serve ogni 2 punti
                            tb_prob = p_win_tiebreak(self.p_serve_A, self.p_serve_B)
                            if random.random() < tb_prob:
                                gA += 1
                            else:
                                gB += 1
                            break

                if gA > gB:
                    sets_A += 1
                else:
                    sets_B += 1
                total_gA += gA
                total_gB += gB

            game_diff_list.append(total_gA - total_gB)
            total_games_list.append(total_gA + total_gB)

        # Distribuzione game_diff
        diff_dist = {}
        for d in game_diff_list:
            diff_dist[d] = diff_dist.get(d, 0) + 1
        diff_dist = {k: v / n_simulations for k, v in diff_dist.items()}

        # Distribuzione total_games
        total_dist = {}
        for t in total_games_list:
            total_dist[t] = total_dist.get(t, 0) + 1
        total_dist = {k: v / n_simulations for k, v in total_dist.items()}

        expected_total = sum(k * v for k, v in total_dist.items())

        def p_cover_handicap(point: float) -> float:
            """
            Probabilita' che A copra l'handicap game.
            point > 0: A da' vantaggio (A -point). P(A_diff > point).
            point < 0: A riceve vantaggio (A +point). P(A_diff > point).
            """
            return sum(v for d, v in diff_dist.items() if d > point)

        def p_over_threshold(threshold: float) -> float:
            """Probabilita' che total games > threshold."""
            return sum(v for t, v in total_dist.items() if t > threshold)

        def p_under_threshold(threshold: float) -> float:
            """Probabilita' che total games < threshold."""
            return sum(v for t, v in total_dist.items() if t < threshold)

        return {
            "expected_total_games": round(expected_total, 1),
            "game_diff_dist": diff_dist,
            "total_games_dist": total_dist,
            "p_cover_handicap": p_cover_handicap,
            "p_over_threshold": p_over_threshold,
            "p_under_threshold": p_under_threshold,
        }


def estimate_p_serve(w_svpt: int, w_1st_won: int, w_2nd_won: int,
                     w_ace: int = 0, w_df: int = 0) -> Optional[float]:
    """
    Stima p_serve dai dati storici di un giocatore.
    
    p_serve = (punti vinti al servizio) / (punti serviti)
    
    Args:
        w_svpt: Punti servizio totali
        w_1st_won: Punti vinti al 1° servizio
        w_2nd_won: Punti vinti al 2° servizio
        w_ace: Ace
        w_df: Doppi falli
    
    Returns:
        p_serve stimato, o None se dati insufficienti
    """
    if not w_svpt or w_svpt < 20:  # Minimo 20 punti per stima affidabile
        return None
    
    # Punti vinti totali al servizio = 1st_won + 2nd_won
    # (senza contare i punti persi al 1° o 2° servizio separatamente)
    # Dati TML/Sackmann spesso danno 1stWon (punti vinti con 1° in) 
    # ma non i punti persi al 1° servizio
    
    # Approssimazione: 
    # Punti vinti al servizio = 1stWon + 2ndWon
    # Punti totali al servizio = svpt (total serve points)
    # w_1st_in = percentuale 1° servizio in campo (se disponibile)
    
    # Se abbiamo solo w_svpt, w_1st_won, w_2nd_won:
    points_won = (w_1st_won or 0) + (w_2nd_won or 0)
    points_total = w_svpt
    
    if points_total == 0:
        return None
    
    return points_won / points_total
