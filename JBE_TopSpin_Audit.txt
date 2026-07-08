# JBE TopSpin — Audit Completo

> **Tutti i problemi, codice morto, errori e anomalie del sistema.**
> Classificati per gravità: 🔴 CRITICO / 🟠 ALTO / 🟡 MEDIO / 🔵 BASSO
> 8 Luglio 2026

---

## 🔴 PROBLEMI CRITICI (da risolvere SUBITO — 6)

### P.01 — `model_prob = 0.97` hardcoded per TUTTE le bet Over/Under
📍 **Dove:** `scripts/odds_api.py` (righe 586-609) o `scripts/daily_report.py`

**Il dato:** 260 bet su 443 (58.7%) hanno `model_prob = 0.97` fissa — identico per linee O/U 6.5, 7.5, 8.5, 9.5, 10.5, indipendentemente dai giocatori, dalla superficie, o dagli odds di mercato.

**Perché succede:** Il calcolo della probabilità per Over/Under Games NON viene dal modello Markov (che pure esiste e funziona in `markov_tennis.py`). La logica di `_simulate_game_distribution()` produce una distribuzione realistica dei game totali, ma `value_detector.py` NON la usa sui mercati O/U — ha gli scheletri vuoti con `pass`.

**Impatto:** Le bet Over/Under sono tutte generate con probabilità fittizia 97%. Quando il bookmaker quota un Over a 29.0 (implied 3.4%), il sistema vede edge del 93% e ci scommette su massiccio. Il 91.2% di queste bet vince perché statisticamente, in un match Bo3, 6.5 games è quasi sempre superato. Ma il modello **non ha idea** della probabilità reale.

**Risultato falso:** +1.963€ di profitto O/U su un totale di +2.002€ = **98% del P&L è fittizio**.

### P.02 — Path /opt/data/jbe-tennis in 4 script — CRASHANO
📍 **Dove:** `scripts/backtest_2026.py` riga 10, `final_backtest_2026.py` riga 12, `full_backtest_2026.py` riga 14, `import_odds_xlsx.py` riga 11

```python
sys.path.insert(0, "/opt/data/jbe-tennis/src")
```

Il progetto si trova in `/opt/data/jbe-topspin-webapp/`. Il path `/opt/data/jbe-tennis` **non esiste più**. Questi 4 script crashano con `ImportError` appena lanciati. Non possono essere eseguiti in questo stato.

### P.03 — Bo5 detection sempre FALSE — Wimbledon predetto come Bo3
📍 **Dove:** `scripts/odds_api.py` (righe 503-504)

```python
best_of = 5 if any(slam in sport_key or slam in tournament
                   for slam in ["wimbledon", "australian", "roland", "french", "usopen", "grand_slam"]) else 3
```

`sport_key` vale `"tennis"` — non contiene nessuna delle stringhe degli Slam. La variabile `tournament` esiste ma non viene mai matchata correttamente. Il risultato è che **TUTTI i match sono classificati come Bo3**, compresi Wimbledon, Roland Garros, Australian Open e US Open.

**Impatto:** I match Slam (che sono Bo5) vengono predetti con probabilità Bo3. La differenza è sostanziale: in Bo5, il giocatore più forte ha un vantaggio maggiore (più set = più chance che la qualità emerga). Il sistema sottostima sistematicamente i favoriti negli Slam.

### P.04 — API key hardcoded in chiaro nel codice
📍 **Dove:** `scripts/odds_api.py` (righe 49-56)

6 chiavi API OddsPapi scritte in chiaro:
```python
ODDSPAPI_KEYS = [
    "0f8c6e9a-23d9-49df-934e-3222a2566559",
    "dd7cc9b0-84c4-4ce9-9dd7-0939bacce0de",
    ...
]
```
Se il repository diventasse pubblico (anche solo per errore), tutte le chiavi sono compromesse. Dovrebbero stare in `.env` e venire caricate come variabili d'ambiente.

### P.05 — SQL injection potenziale in database.py
📍 **Dove:** `src/database.py` (riga 280)

```python
cur = self.conn.execute(
    f"SELECT * FROM prediction_errors WHERE created_at>=? AND {slice_type}=?",
    (since_date, slice_value),
)
```

`slice_type` viene interpolato direttamente nella stringa SQL. La whitelist è in `self_improvement.py`, ma `database.py` non ha alcuna protezione. Chiunque chiami `get_prediction_errors_since()` con un argomento malevolo può fare SQL injection.

### P.06 — `val_acc` referenziata fuori scope in train_topspin.py
📍 **Dove:** `scripts/train_topspin.py` (riga 160)

```python
print(f"\nModello finale salvato con accuracy validation: {val_acc:.4f}")
```

`val_acc` è definita dentro un loop `for windows`. Se il loop non esegue almeno una volta il branch che la imposta, la variabile non è definita → `NameError` a runtime.

---

## 🟠 PROBLEMI AD ALTA PRIORITÀ (da risolvere — 8)

### P.07 — 3 mercati dichiarati ma VUOTI in value_detector.py
📍 **Dove:** `src/engine/value_detector.py` (righe 211-233)

Game Handicap, Over/Under Games e Set Betting hanno scheletri di codice con solo `pass`. Non calcolano edge, non producono bet. L'unico mercato funzionante è **Match Winner** — ma dal DB vediamo 263 bet over_under e 87 game_handicap, il che significa che **ODDSPAPI genera le bet O/U e GH direttamente in odds_api.py**, bypassando completamente il `ValueDetector` engine.

**Conseguenza:** Il `ValueDetector` engine servirebbe a 3 mercati su 4 ma non fa nulla — la logica è sparsa in `odds_api.py`. L'engine è monco.

### P.08 — Over/Under settlement: push non gestito, logica sbagliata
📍 **Dove:** `scripts/paper_portfolio.py` (righe 247-249), `resolve_pending.py`, `resolve_wimbledon.py`

```python
if sel.lower().startswith("o"):
    won = total_games > threshold
else:
    won = total_games < threshold  # Under = total_games < threshold?
```

**Errori:**
1. `total_games == threshold` non è gestito (es. O/U 24.5, match finisce 24 esatti) — dovrebbe essere `push` (rimborso)
2. Dipende dal naming (`startswith("o")` per over) — se una selezione si chiama "O/U 24.5" parte con "O" e va in over

### P.09 — 8 script Notion duplicati/abbandonati
📍 **Dove:** `scripts/`

```
notion_sync.py, sync_portfolio_notion.py, update_notion_v2.py,
update_notion_v3.py, update_notion_final.py, update_notion_final2.py,
update_notion_docs.py, update_nd.py
```

**8 file** che tentano tutti di sincronizzare dati su Notion. Ognuno è una versione diversa dello stesso tentativo. Nessuno è integrato nel pipeline principale. Segno di feature sperimentale iniziata e abbandonata senza fare pulizia.

### P.10 — 4 script di backtest identici
📍 **Dove:** `scripts/backtest_2025.py`, `backtest_2026.py`, `full_backtest_2026.py`, `final_backtest_2026.py`

Tutti fanno: ELO warm-up su 2019-2025 → XGBoost prediction → blend → budget simulation. Variazioni minime (soglie edge, flat vs Kelly, quote usate). Dovrebbero essere **1 script** parametrizzato con argomenti CLI.

### P.11 — resolve_pending.py + resolve_wimbledon.py: duplicati al 70%
Due script che risolvono bet pending. `resolve_pending.py` cerca match nel DB. `resolve_wimbledon.py` ha 156 risultati hardcoded. Codice duplicato, logiche potenzialmente in conflitto.

### P.12 — players.country completamente NULL (2.675 righe)
📍 **Dove:** `data/tennis.db`

TUTTI i 2.675 giocatori hanno `country = NULL`. La colonna non è mai stata popolata. Impossibile fare analisi per nazione. Dato facilmente recuperabile dal dataset di Sackmann.

### P.13 — `cron_daily.sh` con path sbagliato
📍 **Dove:** `scripts/cron_daily.sh`

```bash
cd /opt/data/jbe-tennis
PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/daily_report.py
```
Ancora il path `/opt/data/jbe-tennis` sbagliato. Questo script non parte mai.

### P.14 — `generate_webapp_data.sh` duplicato inutile
📍 **Dove:** `scripts/generate_webapp_data.sh`

Esiste anche `generate_webapp_data.py` che viene chiamata dal pipeline. Lo `.sh` è un duplicato superato.

---

## 🟡 PROBLEMI MEDI (da sistemare dopo — 12)

### P.15 — `q_return` costante 0.3238 per TUTTI su Grass
📍 **Dove:** `data/tennis.db` → `serve_return_params`

Tutti i giocatori hanno `q_return = 0.3238` su erba, indipendentemente dal numero di partite giocate. Probabile valore di default non calcolato.

### P.16 — Kelly Calculator non persistente (stop loss non funziona)
📍 **Dove:** `scripts/odds_api.py` + `src/engine/value_detector.py`

Il `KellyCalculator` è creato come variabile di funzione. A ogni run cron, bankroll riparte da 200€, le perdite consecutive non vengono tracciate. Lo stop loss è teorico ma nella pratica non si attiva mai.

### P.17 — Monte Carlo 20k iterazioni per ogni prediction (nessun caching)
📍 **Dove:** `src/engine/markov_tennis.py` (riga 294)

20.000 simulazioni per ogni match. Per 100 match in una run: 2.000.000 di simulazioni. Nessun caching dei risultati. Se due match condividono gli stessi parametri serve/return, il Monte Carlo viene eseguito due volte.

### P.18 — Query N+1 in daily_report.py
📍 **Dove:** `scripts/daily_report.py` (righe 399-422)

Per ogni match fa query separate per Pinnacle e Bet365 invece di una singola JOIN. Con 100 match sono 200 query extra.

### P.19 — `_p_score_to` mai implementata
📍 **Dove:** `src/engine/markov_tennis.py` (righe 134-136)

```python
def _p_score_to(to, from_g):
    """Prob. di passare da (gA, gB) = gA a (gA + to) in N game."""
    pass
```
Dichiarata, commentata, mai implementata. Codice morto.

### P.20 — `import pickle` inutile in xgboost_tennis.py
📍 **Dove:** `src/engine/xgboost_tennis.py` riga 14

`import pickle` mai usato.

### P.21 — `self_improvement.py` — flag `--retrain` mai usato
📍 **Dove:** `scripts/self_improvement.py`
Il flag CLI `--retrain` esiste ma nel pipeline viene chiamato con `do_retrain_if_needed=True` che ignora il flag.

### P.22 — `match_datetime` non popolata in molte righe
📍 **Dove:** `data/tennis.db` → `paper_portfolio`
Colonna aggiunta da migration, la maggior parte delle righe ha NULL.

### P.23 — 1 match placeholder senza dati in tennis_matches
Record con date, winner_id, score, tournament tutti NULL.

### P.24 — `RESOLVE_WIMBLEDON_TASKS` costante rinominata in 2 script
Stessa lista di risultati Wimbledon replicata in 2 file invece di essere in un JSON esterno.

### P.25 — `backtest_overunder.py` usa sempre quota @1.90 fittizia
Simula scommesse Over/Under con odds fissi invece di usare quote reali. I risultati non hanno validità statistica.

### P.26 — `players` table: colonna `birth_date` non nello schema base
`contextual_factors.py` usa `birth_date` ma lo schema SQL di `database.py` non la prevede. Se il DB viene rigenerato, `get_age()` crasha.

---

## 🔵 PROBLEMI BASSI (da tenere d'occhio — 8)

### P.27 — `MIN_PLAYER_MATCHES` definita in config ma mai usata
### P.28 — `correct_xgb` in backtest ha nome fuorviante (traccia blend, non solo XGBoost)
### P.29 — `EDGE_MW = 0.05` ridefinito in backtest_2025.py invece di importare da config
### P.30 — `bankroll_before` e `bankroll_after` non ricalcolati se bet cancellate
### P.31 — `prediction_errors` senza unique constraint → possibili duplicati
### P.32 — `paper_portfolio.match_id = NULL` per 443 bet → impossibile join
### P.33 — `generate_webapp_data.py` — output path da argv[1] senza sanitizzazione
### P.34 — `daily_report.py` importa `odds_api` e `self_improvement` a runtime (coupling)

---

## 🧮 RIEPILOGO GRAVITÀ

| LIVELLO | QUANTI | COSA |
|---------|--------|------|
| 🔴 **CRITICO** | 6 | Bug che producono risultati falsi, crash, o rischi di sicurezza |
| 🟠 **ALTO** | 8 | Feature rotte, duplicati, logica errata |
| 🟡 **MEDIO** | 12 | Performance, codice morto, dati incompleti |
| 🔵 **BASSO** | 8 | Naming, convenzioni, migliorabili |
| **TOTALE** | **34** | |

---

## 🗺️ ROADMAP AL PRODOTTO FINITO

Per trasformare JBE TopSpin in un sistema realmente funzionante e affidabile, servono 3 fasi:

### FASE 1 — STABILIZZAZIONE (1-2 giorni)
Correggere i bug critici prima di fare qualsiasi altra cosa:

1. ✅ **Fix P.01** — Sostituire `model_prob=0.97` fissa con la distribuzione reale da `MarkovMatchModel._simulate_game_distribution()` nel `ValueDetector`
2. ✅ **Fix P.02** — Correggere path in 4 script: `/opt/data/jbe-tennis/src` → `/opt/data/jbe-topspin-webapp/src`
3. ✅ **Fix P.03** — Correggere Bo5 detection: controllare `tournament` non `sport_key`
4. ✅ **Fix P.05** — Sanitizzare `slice_type` in database.py (whitelist esplicita)
5. ✅ **Fix P.06** — Inizializzare `val_acc = 0.0` prima del loop
6. ✅ **Fix P.16** — Rendere KellyCalculator persistente (salva stato su JSON ogni run)
7. ✅ **Fix P.08** — Gestire push in settlement O/U

### FASE 2 — PULIZIA & REFACTOR (2-3 giorni)
Ripulire il codice e mettere ordine:

1. ✅ **Eliminare** 8 script Notion → 1 script parametrizzato
2. ✅ **Unificare** 4 backtest → 1 script con flag `--year`, `--mode`, `--stake`
3. ✅ **Unificare** resolve_pending + resolve_wimbledon → 1 script con flag `--source`
4. ✅ **Eliminare** cron_daily.sh, generate_webapp_data.sh (duplicati)
5. ✅ **Eliminare** add_datetime_column.py (migration one-shot)
6. ✅ **Eliminare** backtest_overunder.py (valutazione fittizia)
7. ✅ **Eliminare** wta_integration_plan.md (piano non realizzato)
8. ✅ **Spostare** risultati Wimbledon da hardcoded a JSON esterno
9. ✅ **Implementare** i 3 mercati vuoti in value_detector.py (O/U, GH, set betting)
10. ✅ **Eliminare** codice morto (pickle import, _p_score_to, ecc.)
11. ✅ **Spostare** API key in `.env`
12. ✅ **Popolare** players.country dai dati Sackmann

### FASE 3 — PRODOTTO FINITO (3-5 giorni)
Feature per un sistema completo e affidabile:

#### 🎯 Mercati reali
- Implementare **Game Handicap** (value_detector.py usa la distribuzione Monte Carlo)
- Implementare **Over/Under Games** reale (stessa distribuzione, non probabilità fissa)
- Implementare **Set Betting** (2-0, 2-1, 1-2, 0-2)
- Filtro linee O/U: scartare < 18 games (impossibili in singolare Bo3)
- Filtro edge: scartare edge > 25% (dato probabilmente corrotto)

#### 📊 Validazione dati in ingresso
- **Pipeline di sanity check** prima di ogni run:
  - Odds bookmaker: 1.01 < odds < 50
  - Linee O/U: ≥ 18 games per Bo3, ≥ 24 per Bo5
  - Nomi giocatori: no " / " (doppio)
  - Edge massimo: 25% (sopra → dato corrotto, scarta)
- **Rate limiting API**: 7 chiavi in rotazione con backoff esponenziale
- **Cache persistente**: non richiamare API per match già fetchati oggi

#### 💼 Portfolio reale
- **Tracking bankroll persistente** (salva su JSON/DB ogni run)
- **Stop loss funzionante**: 3 consecutive → 24h stop (con stato persistente)
- **Drawdown stop**: -25% dal picco (persistente)
- **Settlement automatizzato**: risoluzione bet entro 24h dalla fine del match

#### 🌐 Webapp avanzata
- **5x aggiornamenti/giorno** (cron: 7, 11, 15, 19, 23 IT) invece di 1
- **Notifiche push PWA** per nuove value bet
- **Grafico P&L** nel tempo (line chart)
- **Distribuzione edge** (istogramma)
- **Pagina "Modello"** che spiega in tempo reale come vengono calcolate le probabilità

#### 📈 Self-Improvement migliorato
- **Retrain automatico** ogni 100 errori (già esiste ma va verificato funzioni)
- **A/B testing**: run parallelo di 2 strategie per confrontare performance
- **Backtest walk-forward** automatico ogni domenica notte
- **Alert proattivo** se accuracy modello scende sotto 70%

#### 🔐 Sicurezza
- API key in `.env` (subito)
- Sanitizzazione input in tutte le query SQL (whitelist)
- Output path sicuri in generate_webapp_data.py

---

## 📊 STATO ATTUALE vs PRODOTTO FINITO

| Dimensione | Oggi | Prodotto Finito |
|---|---|---|
| **Mercati funzionanti** | 1/4 (match winner) | 4/4 |
| **Accuracy P&L** | 98% fittizio | 100% reale |
| **Validazione dati** | 0 check | 5 sanity check |
| **Cron giornaliero** | 1x/giorno | 5x/giorno |
| **Script Notion** | 8 file (abbandonati) | 1 file (integrato) |
| **Backtest** | 4 file (non eseguibili) | 1 file (funzionante) |
| **Codice morto** | ~2.000 righe stimate | 0 righe |
| **Modelli ML** | 3 non usati a pieno | 3 calibrati e integrati |
| **API key** | In chiaro nel codice | In .env |
| **Stop loss** | Teorico (non persiste) | Reale (salva su file) |
| **Notifiche** | Nessuna | Push su webapp |
| **Portfolio** | +2.002€ (falso) | Dato reale e verificato |

---

*Report generato da Jarvis — 8 Luglio 2026*
