# JBE TopSpin — Project Context

> Sistema di **value betting automatico per tennis ATP** con paper trading.
> Basato su 5 strati di analisi (ELO, Markov, Fattori Contestuali, XGBoost, Value Detection).
> Genera una PWA live su GitHub Pages.

## Obiettivo

Trovare scommesse di valore (value bet) confrontando le probabilità stimate dal modello con le quote dei bookmaker, e simularle in un portafoglio virtuale (paper trading) per validare la strategia.

## Stato Attuale (8 Luglio 2026)

| Metrica | Valore |
|---|---|
| Match storici | 76.066 (2001–2026) |
| Giocatori | 2.675 |
| Modelli ML | 6 (winner/sets/games × 2 epoche) |
| Bet totali | 396 (Settled: 237 / Pending: 159) |
| P&L | **+2.002,60€** (ROI 138,4%) |
| Bankroll virtuale | 215,51€ (partenza 200€) |
| Periodo | 25 Giugno – 8 Luglio 2026 |
| Engine attivo | V2.0 — max 2 bet/match, stop loss 3 consecutive |

**Nota:** Il P&L include ~1.767€ di profitto da dati corrotti (linee O/U impossibili 6.5-7.5 games, edge >50%). Il P&L reale stimato è circa **+235€**.

## Stack Tecnologico

| Componente | Tecnologia |
|---|---|
| Backend | Python 3.13 (XGBoost, NumPy, SQLite) |
| Database | SQLite (WAL mode, 40MB) |
| Frontend | HTML + CSS + JS vanilla (PWA, installabile) |
| Hosting | GitHub Pages |
| Data source | OddsPapi (7 chiavi in rotazione) |
| Dati storici | Jeff Sackmann ATP + TML-Database |
| Pipelines | Hermes Cron (09:00 UTC daily) |

## Architettura — 5 Strati

```
┌─────────────────────────────────────────────┐
│  📱 PWA Webapp (GitHub Pages)              │
│  Match | Value | Storico | Report          │
└───────────────────┬─────────────────────────┘
                    │ data.json (generato ogni run)
┌───────────────────▼─────────────────────────┐
│  📋 Pipeline Scripts (Python)              │
│  odds_api.py → daily_report.py → generate  │
└───────────────────┬─────────────────────────┘
                    │ Chiama engine in cascata
┌───────────────────▼─────────────────────────┐
│  🧠 Engine Core (5 strati)                 │
│                                             │
│  Strato 1: ELO Tennis  (elo_tennis.py)      │
│   5 rating/player: overall, hard, clay,      │
│   grass, carpet + blended + decay 270gg      │
│                                             │
│  Strato 2: Markov  (markov_tennis.py)        │
│   Catene di Markov punto→game→set→match      │
│   MC simulation 20k iterazioni per mercati   │
│                                             │
│  Strato 3: Fattori Contestuali  (contextual) │
│   Età, fatica, H2H, momentum, infortuni      │
│                                             │
│  Strato 4: XGBoost  (xgboost_tennis.py)      │
│   38 features, 7.400+ match training         │
│   3 modelli: winner, games, sets             │
│                                             │
│  Strato 5: Value Detection  (value_detector) │
│   Confronto prob. modello vs quote mercato   │
│   Kelly Criterion 12.5% + stop loss          │
└───────────────────┬─────────────────────────┘
                    │
┌───────────────────▼─────────────────────────┐
│  💾 Database SQLite  (tennis.db)           │
│  players | tennis_matches | tennis_odds     │
│  elo_ratings | serve_return_params          │
│  prediction_errors | paper_portfolio        │
└─────────────────────────────────────────────┘
```

## Flusso Giornaliero (Cron: 09:00 UTC)

1. **Settle pending** — odds_api.py --settle (risolve bet da risultati reali)
2. **Fetch odds** — odds_api.py --report (scarica quote ATP live da OddsPapi)
3. **Daily report** — daily_report.py (importa match → aggiorna ELO → predici XGBoost → trova value bet → salva portfolio)
4. **Genera webapp** — generate_webapp_data.py (produce data.json per frontend)

## Squadra di Sviluppo

- **Toni (CEO/PO)** — decide priorità, approva strategie
- **CTO/Tech Lead** — pianifica, analizza, produce task (skill Hermes dedicata)
- **Sviluppatore Senior** — esegue le correzioni codice
- **QA Engineer** — verifica e testa

## File di Tracciamento

- `PROJECT_CONTEXT.md` — questo file (contesto, stato, architettura)
- `DECISIONS_LOG.md` — log delle decisioni prese
- `PARKING_LOT.md` — idee rimandate o in sospeso
- `CHANGELOG.md` — storico versioni

## Criticità Note

1. **Dati API corrotti:** O/U con linee 6.5-7.5 games (impossibili in tennis singolare). 33 bet a odds >10.0, +747€ falso.
2. **Edge impossibili:** 50-93% (max realistico ~10%). 94 bet con edge >50%, +1.767€ falso.
3. **Match doppio non filtrati:** 44 bet su DOUBLES mescolati con singolari, +389€.
4. **Kelly non persistente:** stop loss resettato a ogni run cron.
5. **Logiche settlement divergenti:** 3 modi diversi di risolvere bet.
6. **Filtri basati su campione debole:** blocco match_winner odds ≥2.0 su solo 32 bet.

## Link

- **Webapp live:** https://jarvisagent-justfix.github.io/jbe-topspin-webapp/
- **GitHub:** https://github.com/jarvisagent-justfix/jbe-topspin-webapp
- **Path locale:** `/opt/data/jbe-topspin-webapp/`
