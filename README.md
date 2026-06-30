# JBE TopSpin — ATP Tennis Betting Intelligence

**JBE TopSpin** è un sistema di intelligenza artificiale per la predizione di match ATP e l'identificazione di value bet nel tennis professionistico. Combina modelli statistici classici (ELO) con machine learning (XGBoost) e teoria delle probabilità avanzata (Markov, Kelly Criterion).

> **Live demo:** [jarvisagent-justfix.github.io/jbe-topspin-webapp](https://jarvisagent-justfix.github.io/jbe-topspin-webapp/)

---

## Architettura

```
┌─────────────────────────────────────────────────────────────┐
│                    DATI ESTERNI                              │
│  tennis-data.co.uk  ←  Odds API (The Odds API)  ←  Sackmann │
└────────────────┬──────────────────┬──────────────────────┬──┘
                 │                  │                      │
                 ▼                  ▼                      ▼
┌─────────────────────────────────────────────────────────────┐
│                    PIPELINE (scripts/)                       │
│  import_XLSX → ELO compute → Markov params → XGBoost pred   │
│  → value detection → Kelly stake → portfolio log → Notion   │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│               WEBAPP PROGRESSIVE (docs/)                     │
│  PWA offline-first · 4 tab: Match / Value / Storico / Report│
└─────────────────────────────────────────────────────────────┘
                    │                          ▲
                    ▼                          │
          ┌─────────────────┐        ┌───────────────────┐
          │  Cloudflare Tunel│        │   GitHub Pages    │
          │  (dev/ephemeral) │        │  (production/URL  │
          │  :8765           │        │   fisso)          │
          └─────────────────┘        └───────────────────┘
```

---

## Modello Predittivo (5 Layer)

### 1. Surface-Specific ELO
- 5 rating per giocatore: overall, hard, clay, grass, MoV (Margin of Victory)
- Decadimento temporale (270gg), K-factor dinamico (16)
- Blended 50/50 overall + superficie specifica
- 7 anni di warm-up (2019-2025) su 76k+ match

### 2. Markov Serve/Return
- Probabilità punto-a-punto: serve game (p_serve), return game (p_return)
- Distribuzione game/set/match via programmazione dinamica
- Tiebreak con probabilità bilanciata

### 3. Fattori Contestuali
- Gap ranking
- Differenza età
- Fatica: match giocati negli ultimi 7/14/30 giorni
- Storico H2H per superficie
- Slancio (momentum) ultime 5 partite
- Livello torneo (Grand Slam, Masters, ATP 500/250)

### 4. XGBoost Ensemble
- **38 feature** combinate da tutti i layer precedenti
- Multi-target: winner (classificazione), game total (regressione), set spread
- Platt calibration (slope ~0.79) per probabilità calibrate
- Addestrato su 7.468 match (2023-2026)
- Accuracy out-of-sample: ~88-93% (nota: include leak parziale)

### 5. Value Detection & Kelly
- Edge detection: `model_prob - 1/market_odds`
- Kelly Criterion 12.5% con humanizzazione stake (arrotondamento 0.50€)
- Esposizione giornaliera massima: 15% del bankroll
- Cap massimo singola scommessa: 5%
- Self-improvement loop: analisi errori per slice (superficie, round, odds range)

---

## WebApp (PWA)

Frontend progressivo installabile su mobile/desktop con 4 tab:

| Tab | Cosa mostra |
|-----|-------------|
| **Match** | Partite ATP oggi e nei prossimi 3 giorni, con quote, probabilità modello e edge |
| **Value** | Value bets attivi: edge, stake consigliata, quota, tipologia mercato |
| **Storico** | Cronologia scommesse piazzate (vinte/perse/DAF), bankroll tracker |
| **Report** | Report giornaliero generato dal pipeline |

### Accesso
- **Produzione:** `https://jarvisagent-justfix.github.io/jbe-topspin-webapp/`
- **Sviluppo:** `http://localhost:8765` (server locale + Cloudflare Tunnel)

---

## Pipeline Giornaliero

Il sistema esegue automaticamente 5 cicli al giorno (06:00, 10:00, 14:00, 18:00, 22:00 UTC):

```
1. Scarica nuove quote da The Odds API (max 1 chiamata/run via chiave unificata "tennis")
2. Rotazione automatica su 7 chiavi API in caso di 401
3. Fallback su cache locale se tutte le chiavi sono esaurite
4. Calcola predizioni ELO + XGBoost + Markov su tutti i match ATP attivi
5. Identifica value bet confrontando probabilità modello vs quote di mercato
6. Calcola stake Kelly con exposure cap
7. Aggiorna il portafoglio paper trading su DB
8. Genera data.json per il webapp
```

**Frequenza:** ogni 2h nella fascia 07:00-23:00 italiana (9 run/giorno, ~270 chiamate API/mese su abbonamento free)

---

## Struttura del Repository

```
├── docs/                          # PWA webapp deployata su GitHub Pages
│   ├── index.html                 # UI principale (4 tab)
│   ├── manifest.json              # PWA manifest
│   ├── sw.js                      # Service Worker (offline cache)
│   └── api/
│       ├── data.py                # Generatore JSON da DB
│       └── data.json              # Dati correnti (escluso da git)
├── src/                           # Core ML engine
│   ├── engine/
│   │   ├── elo_tennis.py          # Surface-specific ELO
│   │   ├── markov_tennis.py       # Serve/return Markov model
│   │   ├── xgboost_tennis.py      # XGBoost ensemble trainer/predictor
│   │   ├── value_detector.py      # Edge detection + Kelly
│   │   └── contextual_factors.py  # Features contestuali
│   ├── config.py                  # Configurazioni
│   └── database.py                # SQLite wrapper
├── scripts/                       # Pipeline e utilità
│   ├── daily_report.py            # Report giornaliero combinato
│   ├── odds_api.py                # Odds API fetcher + value detection
│   ├── generate_webapp_data.py    # Genera JSON per webapp
│   ├── compute_elo_history.py     # Calcola ELO storico (76k match)
│   ├── compute_serve_params.py    # Parametri Markov da dati reali
│   ├── retrain_2026.py            # Retrain XGBoost + calibration
│   ├── import_tennis_data.py      # Import XLSX tennis-data.co.uk
│   ├── import_tml.py              # Import TML/Sackmann DB
│   ├── paper_portfolio.py         # Paper trading portfolio
│   ├── notion_sync.py             # Sync portafoglio su Notion
│   ├── backtest_2025.py           # Backtest su 2025
│   └── self_improvement.py        # Analisi errori e bias correction
├── data/                          # Dati (esclusi da git)
│   ├── tennis.db                  # Database SQLite (38MB, escluso)
│   ├── models/                    # Modelli XGBoost + calibration JSON
│   └── cache/                     # Cache Odds API (esclusa)
├── .env                           # Chiavi API (escluso)
└── .gitignore                     # Esclusioni git
```

---

## Dati & Fonti

| Fonte | Tipo | Aggiornamento | Copertura |
|-------|------|---------------|-----------|
| **The Odds API** | Quote live ATP (Bet365, Pinnacle) | Ogni 2h | 70%+ match ATP attivi |
| **tennis-data.co.uk** | Risultati + quote storiche | 24-48h ritardo | 2008-oggi, Bet365+Pinnacle |
| **Jeff Sackmann** (GitHub) | Database storico match | Statico (import una tantum) | 76k+ match, 2001-2026 |

---

## Performance

### Backtest 2026 (Jan-Jun, ~1500 match)

| Metrica | Valore |
|---------|--------|
| ELO accuracy | 68-72% |
| Ensemble accuracy | 88-93% (sovra-appreso) |
| Realistic expected | 80-85% |
| Win rate live (Wimbledon R1) | 80% |
| ROI live | +22.4% |
| Profitto | +44.77€ su 200€ bankroll |

### Self-Improvement Loop
- Analisi errori per slice: superficie, tour level, round, odds range
- Bias correction su scala logit (non additiva)
- Bias range: -0.67 (underdog estremo) a +0.04 (hard court)
- Calibrazione Platt con retrain periodico

---

## Requisiti

- Python 3.10+
- numpy, pandas, xgboost, scikit-learn, openpyxl
- SQLite3
- Opzionale: cloudflared (per tunnel dev)

---

## Licenza

Progetto privato — JBE (Just Fix). Tutti i diritti riservati.
