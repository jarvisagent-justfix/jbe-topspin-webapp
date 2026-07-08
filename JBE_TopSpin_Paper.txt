# JBE TopSpin — Documento Completo del Sistema

> **Versione:** v2.0 | **Data:** 8 Luglio 2026
> **Autore:** Jarvis (Hermes Agent)
> **Repository:** [jarvisagent-justfix/jbe-topspin-webapp](https://github.com/jarvisagent-justfix/jbe-topspin-webapp)
> **Webapp:** [jarvisagent-justfix.github.io/jbe-topspin-webapp](https://jarvisagent-justfix.github.io/jbe-topspin-webapp/)

---

## Indice

1. [Cos'è JBE TopSpin](#1-cosè-jbe-topspin)
2. [Architettura Generale](#2-architettura-generale)
3. [Strato 1 — ELO Tennis Dinamico](#3-strato-1--elo-tennis-dinamico)
4. [Strato 2 — Markov Serve/Return](#4-strato-2--markov-servereturn)
5. [Strato 3 — Fattori Contestuali](#5-strato-3--fattori-contestuali)
6. [Strato 4 — XGBoost Meta-Modello](#6-strato-4--xgboost-meta-modello)
7. [Strato 5 — Value Detection & Kelly](#7-strato-5--value-detection--kelly)
8. [Database SQLite](#8-database-sqlite)
9. [Scripts e Pipeline](#9-scripts-e-pipeline)
10. [Frontend PWA](#10-frontend-pwa)
11. [Portfolio e Performance](#11-portfolio-e-performance)
12. [Criticità Identificate](#12-criticità-identificate)
13. [Infrastruttura](#13-infrastruttura)
14. [Struttura File Completa](#14-struttura-file-completa)

---

## 1. Cos'è JBE TopSpin

JBE TopSpin è un sistema automatico di **value betting per tennis ATP**. Funziona su **paper trading** (soldi virtuali) e combina 5 strati di analisi per trovare scommesse di valore:

1. **ELO Dinamico** — rating aggiornato match per match, con 5 varianti per superficie
2. **Markov Serve/Return** — catene di Markov che modellano punto→game→set→match
3. **Fattori Contestuali** — età, fatica, H2H, momentum, infortuni
4. **XGBoost** — machine learning che combina tutto in 38 features
5. **Value Detection** — confronta modello vs bookmaker, applica Kelly Criterion

Il sistema:
- Scarica quote live da **OddsPapi** (350+ bookmaker, Bet365 prioritario)
- Confronta le sue probabilità con quelle del mercato
- Trova value bet su 3 mercati: **Match Winner**, **Over/Under Games**, **Game Handicap**
- Calcola lo stake ottimale con **Kelly Criterion frazionario (12.5%)**
- Pubblica tutto su una **PWA installabile** su GitHub Pages

---

## 2. Architettura Generale

```
/opt/data/jbe-topspin-webapp/
├── .hermes/                        # Overlay gestione Hermes
├── PROJECT_CONTEXT.md              # Contesto progetto (appena creato)
├── DECISIONS_LOG.md                # Log decisioni (appena creato)
├── PARKING_LOT.md                  # Idee rimandate (appena creato)
├── CHANGELOG.md                    # Storico versioni
├── README.md                       # README pubblico
├── docs/                           # Frontend PWA (GitHub Pages)
│   ├── index.html                  # 1.276 righe — SPA completa
│   ├── manifest.json               # Config PWA
│   ├── sw.js                       # Service Worker offline
│   └── api/
│       ├── data.py                 # 625 righe — generatore dati
│       └── data.json               # Output JSON per frontend
├── src/                            # Il "cervello" (~1.700 righe)
│   ├── config.py                   # 56 righe — costanti globali
│   ├── database.py                 # 316 righe — interfaccia SQLite
│   └── engine/
│       ├── elo_tennis.py           # 325 righe
│       ├── markov_tennis.py        # 429 righe
│       ├── xgboost_tennis.py       # 578 righe
│       ├── contextual_factors.py   # 299 righe
│       └── value_detector.py       # 259 righe
├── scripts/                        # 33 script (~12.272 righe Python totali)
│   ├── odds_api.py                 # 1.041 righe — ⭐ API + parsing + value detection
│   ├── daily_report.py             # 549 righe — run giornaliera
│   ├── paper_portfolio.py          # 369 righe — gestione portfolio
│   ├── pipeline.sh                 # 33 righe — orchestratore bash
│   ├── generate_webapp_data.py     # 34 righe — produce data.json
│   ├── backtest_historical.py      # Backtest storico
│   ├── train_topspin.py            # Addestramento modelli XGBoost
│   ├── resolve_pending.py          # Risoluzione bet pending
│   └── ... (altri 25 script)
└── data/
    ├── tennis.db                   # 40MB — 76.066 match, 2.675 giocatori
    ├── paper_portfolio.db          # 0B (vuoto — usa tennis.db internamente)
    ├── models/                     # 8.3MB — 6 modelli ML
    │   ├── topspin_winner.json     # Modello Winner (XGBoost)
    │   ├── topspin_games.json      # Modello Games (XGBoost Regressor)
    │   ├── topspin_sets.json       # Modello Sets (XGBoost Classifier)
    │   ├── topspin_2022_*.json     # Copie 2022 per confronto
    │   ├── platt_calibration.json  # Calibrazione Platt
    │   └── last_retrain.json       # Timestamp ultimo retrain
    ├── cache/                      # Cache API
    └── import/                     # Dati import (TML zip)
```

### Flusso Dati Esecuzione

```
1. Cron (09:00 UTC) → pipeline.sh
   │
   ├── Step 1: odds_api.py --settle
   │   → Legge settlement OddsPapi
   │   → Risolve bet pending (vinte/perse/push)
   │   → Aggiorna paper_portfolio nel DB
   │
   ├── Step 2: odds_api.py --report
   │   → GET /v4/sports/tennis/odds?bookmakers=Bet365,Pinnacle
   │   → Rotazione 7 chiavi API per aggirare rate limit
   │   → Parsing quote (match_winner, over_under, game_handicap)
   │   → Salvataggio in tennis_odds table
   │
   ├── Step 3: daily_report.py
   │   → Importa nuovi match in tennis_matches
   │   → Aggiorna rating ELO per ogni giocatore
   │   → Calcola feautures contestuali per ogni match
   │   → Esegue predizione XGBoost (3 modelli)
   │   → Confronta con quote bookmaker → value detection
   │   → Salva bet in paper_portfolio
   │   → Self-improvement: analizza prediction_errors → bias_corrections
   │
   └── Step 4: generate_webapp_data.py
       → Legge DB (match, portfolio, predizioni)
       → Produce data.json
       → Git push → GitHub Pages deploy
```

---

## 3. Strato 1 — ELO Tennis Dinamico

**File:** `src/engine/elo_tennis.py` (325 righe)

### 3.1 Sistema a 5 Rating per Giocatore

Ogni giocatore ATP ha **6 valori ELO**:

| Rating | Descrizione |
|--------|-------------|
| `overall` | Generale (tutte le superfici) |
| `hard` | Cemento (Hard) |
| `clay` | Terra battuta (Clay) |
| `grass` | Erba (Grass) |
| `carpet` | Sintetico (Carpet) |
| `mov` | Margin of Victory — penalizza vittorie nette |

### 3.2 Blended Rating

Per ogni match, il sistema combina overall + superficie con un peso dinamico:

- **Confidenza = min(match_su_superficie / 100, 0.5)**
- **Peso overall = 1.0 - confidenza**
- **Blended = overall × peso_overall + superficie × confidenza**

Un giocatore con pochi match su erba (es. 5) avrà blend 95% overall. Con 50+ match su erba, sarà 50/50.

### 3.3 K-Factor Dinamico

Il K-factor (quanto il rating cambia dopo un match) non è fisso:

- **Base:** 16 (ridotto da 32 per stabilizzare su 73k match)
- **Assenza:** +10% per ogni mese oltre 3 mesi di inattività
- **Margin of Victory:** K moltiplicato per `ln(games_vinti/games_persi + 1)`
- **Bo5:** +10% per vittorie in best-of-5

### 3.4 Decay Temporale

Dopo 270 giorni di inattività, il rating decade verso 1500 (default):
- A 270gg: nessun decay
- A 540gg (2×): ~50% del rating perso verso default

### 3.5 Predizione ELO

Probabilità standard: `1 / (1 + 10^((R_avversario - R_self) / 400))`

---

## 4. Strato 2 — Markov Serve/Return

**File:** `src/engine/markov_tennis.py` (429 righe)

### 4.1 Catena Punto → Game → Set → Match

Modella il match di tennis a livello di punto usando catene di Markov.

**Servizio → Game:**
- Formula Markoviana a 4 punti: calcola probabilità di vincere un game al servizio
- Include deuce: `p_deuce = p² / (1 - 2p(1-p))`
- Pattern: 4-0, 4-1, 4-2, deuce

**Game → Set:**
- Simulazione DP su griglia 7×7 (game A × game B)
- Alternanza servizio ogni game
- A 6-6 → tiebreak con alternanza ogni 2 punti
- Formula O'Malley per probabilità tiebreak: `P = 0.5 + (p_A - p_B) × 0.4`

**Set → Match:**
- Bo3: P_match = P_set² + 2·P_set²·(1-P_set)
- Bo5: P_match = P_set³ + 3·P_set³·(1-P_set) + 6·P_set³·(1-P_set)²

### 4.2 Monte Carlo per Mercati Secondari

Simula 20.000 match per estrarre:
- **Distribuzione game totali** → Over/Under probabilità
- **Distribuzione game differenza** → Game handicap probabilità
- **Expected total games**

### 4.3 Stima p_serve dai Dati

Da dati storici: `p_serve = punti_vinti_servizio / punti_totali_servizio`
Richiede minimo 20 punti per stima affidabile.

---

## 5. Strato 3 — Fattori Contestuali

**File:** `src/engine/contextual_factors.py` (299 righe)

Calcola variabili che ELO e Markov non catturano:

| Feature | Descrizione | Range |
|---------|-------------|-------|
| `fatigue_p1_7d` | Match giocati negli ultimi 7 giorni | 0–5+ |
| `fatigue_diff_7d` | Differenza fatica tra giocatori | -5–+5 |
| `b2b_p1` | Back-to-back (match ieri) | 0/1 |
| `h2h_total_p1` | Vittorie H2H totali | 0–N |
| `h2h_surface_p1` | Vittorie H2H sulla superficie | 0–N |
| `momentum_p1_5` | Win rate ultimi 5 match | 0–1 |
| `win_streak_p1` | Vittorie consecutive | 0–N |
| `age_diff` | Differenza età in anni | -20–+20 |
| `injury_p1` | Ritiri/ritiri forzati ultimi 12 mesi | 0–10 |
| `tournament_stage` | Round numerico (R128=0 → F=0.9) | 0–0.9 |
| `is_hard/clay/grass/carpet` | Superficie one-hot | 0/1 |
| `best_of_5` | Flag Bo5 | 0/1 |

---

## 6. Strato 4 — XGBoost Meta-Modello

**File:** `src/engine/xgboost_tennis.py` (578 righe)

### 6.1 Feature Extraction

Unisce tutti gli strati in un **vettore di ~38 features** per ogni match:

1. **ELO features** (7): diff_overall, diff_surface, blended_diff, mov_diff, prob, surf_conf_p1/p2
2. **Ranking features** (2): rank giocatore 1 e 2
3. **Contestuali** (20+): fatica, H2H, momentum, età, infortunio, torneo
4. **Superficie** (4): one-hot encoding
5. **Best of** (1): Bo3 vs Bo5

### 6.2 Tre Modelli Separati

| Modello | Tipo | Target |
|---------|------|--------|
| **Winner** | XGBClassifier | Chi vince il match (binary) |
| **Games** | XGBRegressor | Game totali del match (regression) |
| **Sets** | XGBClassifier | 4 classi: 2-0, 2-1, 1-2, 0-2 |

### 6.3 Training

- **Dati di training:** ~7.400 match (storici recenti)
- **Parametri:** learning_rate=0.05, max_depth=6, n_estimators=300, early_stopping=30
- **Retrain automatico:** ogni 100 prediction errors
- **Calibrazione Platt:** converte output XGBoost in probabilità calibrate

### 6.4 Self-Improvement

Il sistema traccia ogni errore di predizione (`prediction_errors` table) e calcola:
- **Bias corrections per slice:** correzioni per superficie, livello torneo, round
- **Retrain trigger:** a 100 errori, riaddestra il modello
- **Accuracy tracking:** 91.1% su 371 errori loggati

---

## 7. Strato 5 — Value Detection & Kelly

**File:** `src/engine/value_detector.py` (259 righe)

### 7.1 Come Trova le Value Bet

Per ogni match con quote disponibili:

1. Prende le probabilità modello (da XGBoost calibrato)
2. Legge le quote bookmaker (Pinnacle > Bet365 > fallback)
3. Calcola la probabilità implicita: `P_implied = 1 / odds`
4. Calcola l'edge: `edge = P_modello - P_implied`
5. Se edge ≥ 5% E confidenza modello ≥ 50% → è una value bet
6. **Consensus check:** se la quota del bookmaker è >10% dalla media, l'edge viene dimezzato

### 7.2 Mercati Supportati

| Mercato | Priorità | Stato | Edge medio |
|---------|----------|-------|------------|
| **Over/Under Games** | 1ª | ✅ Attivo (WR 68.8% storico) | +12-30% |
| **Game Handicap** | 3ª | ✅ Attivo (solo edge >12%) | +8-15% |
| **Match Winner** | 2ª | ⚠️ Bloccato su odds ≥ 2.0 | Variabile |
| **Set Betting** | 4ª | 🔧 Non ancora implementato | — |

**Regola V2.0:** massimo 2 bet per match, priorità over_under > match_winner > game_handicap

### 7.3 Kelly Criterion

Formula: `f = 0.125 × edge / (odds - 1)`

Regole di safety:
- **Max 5%** del bankroll per scommessa
- **Max 15%** esposizione giornaliera
- **Max 10%** esposizione per torneo
- **Stop loss** dopo 3 perdite consecutive (24 ore)
- **Drawdown stop:** se perde il 25% dal picco, stop definitivo
- **Arrotondamento umano:** lo stake viene arrotondato al 0.50€ più vicino

### 7.4 Confidenza

| Edge | Probabilità | Confidenza |
|------|-------------|------------|
| ≥ 10% | ≥ 65% | HIGH |
| ≥ 7% | ≥ 55% | MEDIUM |
| ≥ 5% | ≥ 50% | LOW |

---

## 8. Database SQLite

**File:** `data/tennis.db` (40MB) — gestito da `src/database.py` (316 righe)

### 8.1 Schema

```sql
-- Giocatori
players (atp_id, name, country, hand, height_cm, turned_pro)

-- Match storici (76.066 righe)
tennis_matches (match_date, tournament, surface, round, best_of,
  winner_id→players, loser_id→players, score, stats servizio...)

-- Quote bookmaker (3.908 righe)
tennis_odds (match_id→matches, bookmaker, odds_winner, odds_loser,
  handicap_line, handicap_odds, total_line, over_odds, under_odds)

-- Rating ELO storico (150.782 righe)
elo_ratings (player_id, match_id, rating_date,
  rating_overall, rating_hard, ..., matches_played)

-- Parametri servizio/risposta (553 righe)
serve_return_params (player_id, surface, p_serve, q_return, confidence)

-- Errori di predizione (371 righe + bias_corrections)
prediction_errors (match_id, pred_prob, edge, actual_winner, ...)
bias_corrections (slice_type, slice_value, bias, n_errors)

-- Portfolio (396 righe)
paper_portfolio (match_id, selection, market, odds, stake, status, result)
```

### 8.2 Performance

- **WAL mode** (Write-Ahead Logging) per letture/scritture concorrenti
- **Indici** su: date, winner_id, loser_id, surface, tournament
- **Timeout:** 10 secondi

### 8.3 Fonti Dati

| Fonte | Dati | Copertura |
|-------|------|-----------|
| **Jeff Sackmann** (tennis_atp) | Risultati + ranking + stats | 2001–2026 |
| **TML-Database** | Match + statistiche | 2010–2026 |
| **OddsPapi** | Quote live Bet365 + Pinnacle | Match in corso |
| **tennis-data.co.uk** | Quote storiche | 2008–2026 |

---

## 9. Scripts e Pipeline

### 9.1 Pipeline Principale

**`scripts/pipeline.sh`** — orchestratore bash eseguito da cron:

```bash
#!/bin/bash
set -e
BASE="/opt/data/jbe-topspin-webapp"
export PYTHONPATH="$BASE/src"

# Step 1: Risolvi bet pending
python3 scripts/odds_api.py --settle

# Step 2: Scarica quote live
python3 scripts/odds_api.py --report

# Step 3: Daily report (import + predici + value detection)
python3 scripts/daily_report.py

# Step 4: Genera webapp data
python3 scripts/generate_webapp_data.py
```

### 9.2 Script Principali

| Script | Righe | Funzione |
|--------|-------|----------|
| `odds_api.py` | **1.041** | ⭐ API OddsPapi: fetch quote, parsing, settlement, value detection combinata |
| `daily_report.py` | **549** | Run completa giornaliera: import match → ELO → XGBoost → value → portfolio |
| `paper_portfolio.py` | **369** | Gestione portfolio: CRUD bet, calcolo P&L, report |
| `generate_webapp_data.py` | **34** | Produce data.json per frontend |
| `backtest_historical.py` | — | Backtest su 1.135 match storici |
| `train_topspin.py` | — | Addestra modelli XGBoost da zero |
| `resolve_pending.py` | — | Risolve bet usando DB storico |
| `resolve_wimbledon.py` | — | Risoluzione manuale Wimbledon 2026 |
| `fetch_historical_odds.py` | — | Recupera quote storiche OddsPapi |

### 9.3 Cron Job

| Job | Schedule | Descrizione |
|-----|----------|-------------|
| **JBE TopSpin Pipeline** | 09:00 UTC (11:00 IT) | Esegue pipeline completa (fetch → predici → deploy) |
| **Deliver:** `local` (nessun messaggio Discord) | | |

---

## 10. Frontend PWA

**File:** `docs/index.html` (1.276 righe — SPA vanilla)

### 10.1 Caratteristiche

- **Single Page Application** in HTML + CSS + JS vanilla
- **PWA installabile** (manifest.json + service worker sw.js)
- **4 Tab navigabili:**
  - **Match** — Partite in programma con quote e probabilità modello
  - **Value** — Value bet del giorno con stake suggerito
  - **Storico** — Tutte le bet passate con P&L
  - **Report** — Report giornaliero generato dal sistema
- **Dark theme** professionale
- **Font: Inter** per massima leggibilità
- **Responsive** (mobile-first, funziona su telefono)

### 10.2 Deploy

- **Hosting:** GitHub Pages (branch `master`, directory `docs/`)
- **URL:** https://jarvisagent-justfix.github.io/jbe-topspin-webapp/
- **Trigger:** push su master → GitHub Actions deploya automaticamente
- **Pipeline:** `generate_webapp_data.py` → `docs/api/data.json` → git push

---

## 11. Portfolio e Performance

### 11.1 Statistiche Generali (25 Giugno — 8 Luglio 2026)

| Metrica | Valore |
|---------|--------|
| **Bet totali** | 396 |
| **Settled** | 237 (183 W / 52 L / 2 Push) |
| **Pending** | 159 (match non ancora giocati) |
| **Totale puntato** | 1.447,00€ |
| **P&L** | **+2.002,60€** |
| **ROI dichiarato** | **138,4%** |
| **Bankroll attuale** | 215,51€ (partenza 200€) |

### 11.2 Distribuzione per Mercato

| Mercato | Settled | W/L | P&L | Contributo |
|---------|---------|-----|-----|------------|
| **Over/Under** | 161 | 145W / 14L / 2Push | **+1.963,20€** | 98% |
| **Game Handicap** | 35 | 24W / 11L | **+69,71€** | 3,5% |
| **Match Winner** | 41 | 14W / 27L | **-30,31€** | -1,5% |
| **Totale** | **237** | **183W / 52L** | **+2.002,60€** | 100% |

### 11.3 ⚠️ P&L Reale Stimato

I dati contengono **distorsioni da bug nei dati di input**. Se si escludono le bet con edge > 50%:

| Metrica | Dichiarato | Reale (stimato) |
|---------|-----------|-----------------|
| P&L | +2.002,60€ | **~+235€** |
| ROI | 138,4% | **~7%** |
| Bankroll | 215,51€ | **~235€** |

Il 98% del profitto dichiarato viene da **bet Over/Under** — le stesse più affette da dati corrotti (linee O/U 6.5-7.5 games impossibili in tennis singolare, odds >10.0).

---

## 12. Criticità Identificate

### 🔴 CRITICA #1 — Dati API Corrotti: O/U con Linee Impossibili

**Dove:** `odds_api.py → parse_odds()` + `predict_and_find_value()`

Il sistema non filtra linee Over/Under impossibili per tennis singolare. Un match di tennis non può finire sotto i 12 games (6-0 6-0). Linee 6.5 o 7.5 games vengono da formati speciali (es. doppio, partite accorciate). Il sistema vede odds pazzeschi (13.0-26.0), calcola edge del 90%+ e ci scommette su.

**Impatto:** 33 bet a odds > 10.0, **+747€ di profitto falso**

**Soluzione proposta:** Ignorare O/U con linee < 18.0.

---

### 🔴 CRITICA #2 — Edge Impossibili (50-93%)

**Dove:** `predict_and_find_value()`

Il sistema calcola edge del 50-93%. In value betting reale, un edge > 10% è rarissimo. > 50% è impossibile — significa errore nei dati in ingresso. Nessun controllo blocca queste bet.

**Impatto:** 94 bet con edge > 50%, **+1.767€ di profitto falso**

**Soluzione proposta:** Scartare qualsiasi bet con edge > 40% (dato corrotto).

---

### 🟡 CRITICA #3 — Bet su DOUBLES non Filtrate

**Dove:** `get_upcoming_matches()`

L'API OddsPapi torna match di doppio (es. "Pavlasek A / Rikl P") mescolati con singolari. Il sistema non filtra i nomi con " / ".

**Impatto:** 44 bet su doppio, **+389€ di profitto**

**Soluzione proposta:** Filtrare i nomi giocatore contenenti " / ".

---

### 🟡 CRITICA #4 — Kelly Calculator non Persistente

**Dove:** `predict_and_find_value()`

Il `KellyCalculator` è creato come variabile statica di funzione. A ogni run cron, il bankroll riparte da 200€, le perdite consecutive non vengono mai tracciate. Lo stop loss non funziona.

**Impatto:** Il sistema non si ferma mai, anche se perde 10 bet di fila

**Soluzione proposta:** Salvare stato Kelly su file JSON ogni run, caricarlo alla prossima.

---

### ⚠️ CRITICA #5 — Due Logiche di Settlement Divergenti

**Dove:** `resolve_pending_bets()` in `odds_api.py` vs `resolve_pending.py` vs `resolve_wimbledon.py`

3 modi diversi di risolvere bet pending, con logiche di matching nomi e parsing score diverse. Possibile doppia contabilizzazione.

---

### ⚠️ CRITICA #6 — Filtro basato su Campione Debole

**Dove:** `predict_and_find_value()` — blocco `match_winner` su odds ≥ 2.0

Decisione basata su solo 32 bet risolte — campione statisticamente irrilevante.

---

## 13. Infrastruttura

### 13.1 Ambiente

| Risorsa | Valore |
|---------|--------|
| **Host** | Linux (VM Hermes) |
| **Python** | 3.13.5, venv: `/tmp/jbe-venv2/` |
| **Spazio disco** | 68 MB (progetto) + 40 MB (DB) |
| **API Keys** | 7 chiavi OddsPapi in rotazione |
| **Cache** | 6 mesi di validità (forward le chiavi) |

### 13.2 Dipendenze Python

```
xgboost, numpy, scikit-learn, sqlite3 (stdlib)
```

### 13.3 Cron

```yaml
name: JBE TopSpin Pipeline
schedule: "0 9 * * *"    # 09:00 UTC = 11:00 IT
script: jbe-topspin-webapp/scripts/pipeline.sh
no_agent: true
workdir: /opt/data
deliver: local
```

---

## 14. Struttura File Completa

```
/opt/data/jbe-topspin-webapp/
│
├── PROJECT_CONTEXT.md          # ← Questo documento sintetico (appena creato)
├── DECISIONS_LOG.md            # ← Log decisioni progetto (appena creato)
├── PARKING_LOT.md              # ← Idee rimandate (appena creato)
├── README.md                   # README pubblico
├── CHANGELOG.md                # Storico versioni
├── wta_integration_plan.md     # Piano integrazione WTA
│
├── src/                        # 1.700 righe — Engine
│   ├── __init__.py
│   ├── config.py               # Costanti globali (ELO, Kelly, superficie...)
│   ├── database.py             # Interfaccia SQLite (schema, CRUD, query)
│   └── engine/
│       ├── __init__.py
│       ├── elo_tennis.py       # Strato 1: 5 rating per giocatore, decay, K dinamico
│       ├── markov_tennis.py    # Strato 2: Catene di Markov punto→game→set→match
│       ├── contextual_factors.py # Strato 3: Età, fatica, H2H, momentum
│       ├── xgboost_tennis.py   # Strato 4: 38 features, 3 modelli, Platt calibration
│       └── value_detector.py   # Strato 5: Value detection + Kelly Criterion
│
├── scripts/                    # 33 script (~12.272 righe)
│   ├── pipeline.sh             # Orchestratore bash (cron)
│   ├── odds_api.py             # ⭐ 1.041 righe — API OddsPapi
│   ├── daily_report.py         # Run giornaliera completa
│   ├── paper_portfolio.py      # Gestione portfolio
│   ├── generate_webapp_data.py # Produce data.json per frontend
│   ├── backtest_historical.py  # Backtest su match storici
│   ├── backtest_2025.py        # Backtest 2025
│   ├── backtest_2026.py        # Backtest 2026
│   ├── final_backtest_2026.py  # Backtest finale 2026
│   ├── train_topspin.py        # Addestramento modelli
│   ├── retrain_2026.py         # Retrain 2026
│   ├── calibrate.py            # Calibrazione Platt
│   ├── resolve_pending.py      # Risoluzione bet pending
│   ├── resolve_wimbledon.py    # Risoluzione manuale Wimbledon 2026
│   ├── fetch_historical_odds.py# Recupero quote storiche
│   ├── import_tennis_data.py   # Import dati Sackmann
│   ├── import_tml.py           # Import dati TML
│   ├── import_tml_stats.py     # Import statistiche TML
│   ├── import_odds_xlsx.py     # Import quote da XLSX
│   ├── compute_elo_history.py  # Calcolo storico ELO
│   ├── compute_serve_params.py # Calcolo parametri servizio
│   ├── verify_import.py        # Verifica import dati
│   ├── create_notion_portfolio.py # Portfolio su Notion
│   ├── notion_sync.py          # Sync Notion
│   ├── sync_portfolio_notion.py # Sync portfolio Notion
│   ├── update_notion_docs.py   # Update documentazione Notion
│   ├── update_notion_final.py  # Finalizzazione Notion
│   ├── update_notion_v2.py     # Notion v2
│   ├── update_notion_v3.py     # Notion v3
│   ├── update_notion_final2.py # Finalizzazione 2
│   ├── update_nd.py            # Update ND
│   ├── update_nd.sh            # Script bash ND
│   ├── self_improvement.py     # Self-improvement engine
│   ├── setup_cron.py           # Setup cron job
│   └── add_datetime_column.py  # Migrazione DB
│
├── docs/                       # Frontend PWA (GitHub Pages)
│   ├── index.html              # SPA — 1.276 righe
│   ├── manifest.json           # Config PWA
│   ├── sw.js                   # Service Worker
│   └── api/
│       ├── data.py             # Generatore dati — 625 righe
│       ├── data.json           # Output (match, value, storico, report)
│       └── __pycache__/
│
└── data/                       # Dati (ignorati da git)
    ├── tennis.db               # 40MB — 76.066 match, 2.675 giocatori
    ├── paper_portfolio.db      # 0B — non usato (usa tennis.db)
    ├── models/                 # 8.3MB — 6 modelli ML
    │   ├── topspin_winner.json
    │   ├── topspin_games.json
    │   ├── topspin_sets.json
    │   ├── topspin_2022_winner.json
    │   ├── topspin_2022_games.json
    │   ├── topspin_2022_sets.json
    │   ├── platt_calibration.json
    │   └── last_retrain.json
    ├── cache/
    │   ├── oddspapi_markets.json
    │   └── backtest_results.json
    ├── delivery/               # Report giornalieri (testo)
    ├── import/
    │   └── tml_all.zip
    └── output/
        └── backtest_2025_results.json
```

---

## Appendice A — Glossario

| Termine | Significato |
|---------|-------------|
| **Value Bet** | Scommessa con valore atteso positivo: modello dice X%, bookmaker dice Y% < X% |
| **Edge** | Vantaggio = probabilità modello - probabilità implicita della quota |
| **Kelly Criterion** | Formula matematica per calcolare la puntata ottimale |
| **ELO** | Sistema di rating usato negli scacchi, adattato al tennis |
| **Markov Chain** | Modello probabilistico dove lo stato futuro dipende solo da quello presente |
| **XGBoost** | Algoritmo di machine learning basato su gradient boosting |
| **PWA** | Progressive Web App — applicazione web installabile come app nativa |
| **Paper Trading** | Simulazione di scommesse con denaro virtuale |
| **Settlement** | Risoluzione di una scommessa dopo la conclusione del match |
| **Bo3 / Bo5** | Best of 3 sets / Best of 5 sets |

---

*Documento generato da Jarvis (Hermes Agent) — 8 Luglio 2026*
