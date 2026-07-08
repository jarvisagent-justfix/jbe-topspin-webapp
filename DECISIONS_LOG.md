# Decisioni di Progetto — JBE TopSpin

## 2026-07-02 — Strategia V2.0: Nuova selezione bet
- **Scelta:** Max 2 bet per match, blocco match_winner su odds ≥ 2.0, priorità a over_under
- **Perché:** Backtest storico su 1.135 match ha mostrato WR 68.8% su O/U vs 0% su match_winner odds alti
- **Approvato da:** Toni

## 2026-07-02 — Migrazione a OddsPapi
- **Scelta:** Sostituzione The Odds API → OddsPapi (350+ bookmaker, 6 chiavi rotazione)
- **Perché:** OddsPapi ha più copertura, settlement in tempo reale, costo zero
- **Approvato da:** Toni

## 2026-07-02 — Kelly 12.5% con stop loss
- **Scelta:** Kelly frazionario 12.5%, stop loss 3 consecutive, drawdown 25%
- **Perché:** Bilanciare crescita aggressiva con protezione del bankroll
- **Approvato da:** Toni

## 2026-07-08 — Struttura gestione progetto
- **Scelta:** Tenere progetto in `/opt/data/jbe-topspin-webapp/`, aggiungere overlay gestione (PROJECT_CONTEXT.md, DECISIONS_LOG.md, PARKING_LOT.md, .hermes/skills)
- **Perché:** Path assoluti in 25 script, cron job, DB 40MB e GitHub Pages già configurati. Spostare rompe tutto.
- **Approvato da:** Toni

## 2026-07-08 — Correzioni prioritarie
- **Scelta:** Correggere, non riscrivere da capo. Il codice buono (ELO, Markov, XGBoost, DB) è solido. Le criticità sono in ~200 righe di parsing API e validazione.
- **Approvato da:** Toni
