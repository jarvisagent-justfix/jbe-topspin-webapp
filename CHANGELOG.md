# JBE TopSpin — Changelog

## JBE Decision Engine v2.0

### v2.0 — Nuova strategia di selezione (2026-07-02)
- **Massimo 2 bet per match** (prima: nessun limite)
- **Blocco match_winner su odds ≥ 2.0** (WR 0% nei dati reali)
- **Over/Under prioritario** con soglia ±2.0 games (WR 68.8% su 1.135 match storici)
- **Game handicap filtrato**: solo se edge > 12% E odds < 2.5
- **HIGH confidence declassata a MEDIUM** su match_winner (dati: HIGH 31% vs MEDIUM 57%)
- **Kelly stake 12.5%** con stop loss (3 consecutive) e drawdown (25%)
- Priorità selezione: over_under > match_winner > game_handicap

### v1.1 — Backtest storico (2026-07-02)
- Script `fetch_historical_odds.py`: recupera quote storiche OddsPapi (Gen-Giu 2026)
- 850 fixtureId matched, 1.135 match con Bet365 odds storiche
- Script `backtest_historical.py`: backtest modello su 1.099 match ATP
- Accuratezza modello: 95.3% (Brier 0.05)
- Scoperto: edge medio su underdog +4.51% (modello più ottimista del mercato)

### v1.0 — Migrazione a OddsPapi (2026-07-02)
- Sostituzione The Odds API → OddsPapi (350+ bookmaker)
- 6 chiavi API con rotazione automatica
- Bet365 prioritario (accessibile Italia), Pinnacle fallback
- Risultati in tempo reale via `/v4/settlements` e `/v4/scores`
- Singola run giornaliera alle 11:00 Italiane
- Backtest Over/Under su 1.135 match: configurazione ±2.0 (320 bet, WR 68.8%, ROI +30.6%)
