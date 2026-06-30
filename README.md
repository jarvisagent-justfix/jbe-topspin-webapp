# JBE TopSpin — Sistema Intelligente per Scommesse sul Tennis

**JBE TopSpin** è un sistema che analizza migliaia di partite di tennis ATP per trovare le migliori occasioni da scommessa. Non è un "sistema magico" che vince sempre — è uno strumento che aiuta a prendere decisioni più informate basate sui dati.

> **Provalo ora:** [jarvisagent-justfix.github.io/jbe-topspin-webapp](https://jarvisagent-justfix.github.io/jbe-topspin-webapp/)

---

## Cosa fa JBE TopSpin?

Immagina di avere un amico che:
1. Ha studiato **decenni di partite di tennis** e ricorda ogni risultato
2. Conosce i punti di forza di ogni giocatore su ogni superficie (erba, terra, cemento)
3. Analizza le **quote dei bookmaker** in tempo reale
4. Ti dice: "Secondo i dati, questa partita ha un valore nascosto — conviene scommettere"

Questo è JBE TopSpin. In pratica:

- **Scarica le quote live** dai bookmaker (Bet365, Pinnacle)
- **Confronta le probabilità del modello** con quelle del mercato
- **Trova "value bet"** — partite dove il modello crede che le probabilità reali siano migliori di quelle indicate dalle quote
- **Suggerisce quanto scommettere** usando un metodo matematico (Kelly Criterion) per gestire il rischio
- **Tiene traccia di tutto** in un portafoglio virtuale (paper trading) per vedere se funziona

---

## Come funziona? (spiegato semplice)

Il sistema usa **5 strati di analisi** che lavorano insieme:

### 1. Punteggio ELO (come negli scacchi)
Ogni giocatore ha un punteggio che si aggiorna dopo ogni partita. Se batti un giocatore forte, il tuo punteggio sale tanto. Se perdi contro uno debole, scende tanto. JBE TopSpin tiene **5 punteggi diversi** per ogni giocatore: uno generale, uno per cemento, uno per terra, uno per erba, e uno che considera quanto hai vinto nettamente.

### 2. Percentuali di servizio e risposta
Analizza statisticamente quanto è forte un giocatore al servizio e in risposta. Sa, per esempio, che un giocatore con una prima potente vince il 72% dei suoi game di servizio. Con questi dati, calcola la probabilità che vinca un set, e poi un intero match.

### 3. Fattori umani
Considera cose che influenzano una partita ma non si vedono nelle statistiche base:
- **Età** dei giocatori (un veterano di 35 anni è diverso da un giovane di 20)
- **Stanchezza** (quante partite ha giocato nelle ultime settimane)
- **Storico testa a testa** (ci sono giocatori che "si affrontano male" a vicenda)
- **Momentum** (sta vincendo spesso ultimamente o no?)
- **Importanza del torneo** (una finale Slam è diversa da un primo turno ATP 250)
- **Superficie preferita** (c'è chi è fortissimo solo sull'erba)

### 4. Intelligenza Artificiale (XGBoost)
Qui entrano in gioco tutti i dati precedenti. Un algoritmo di machine learning (lo stesso usato in molti sistemi professionali) combina **38 diversi indicatori** per ogni partita e produce una probabilità finale. L'algoritmo è stato "addestrato" su oltre **7.400 partite** reali del passato.

### 5. Caccia al valore
Il modello confronta le sue probabilità con quelle dei bookmaker. Se per esempio il modello dice che Sinner ha l'85% di vincere, ma le quote dei bookmaker dicono solo il 75%, c'è **valore** — il mercato sta sottovalutando Sinner. A questo punto calcola quanto scommettere.

---

## Come si gestiscono i soldi?

Non è solo trovare scommesse buone — è anche **non perderle tutte in tre colpi**. JBE TopSpin usa queste regole:

- **Kelly Criterion** — una formula matematica che dice quanto scommettere in base al vantaggio che hai trovato. Più il vantaggio è sicuro, più si punta
- **Mai più del 5% del bankroll** su una singola scommessa
- **Mai più del 15% in un giorno** — se trovi 4 scommesse buone lo stesso giorno, non le giochi tutte al massimo
- **Stakes arrotondati** — niente cifre strane tipo 4.23€, sempre multipli di 0.50€
- **Stop loss** — se perdi 3 scommesse di fila, ti fermi 24 ore

Il tutto su un **portafoglio virtuale** (paper trading) — non si usano soldi veri, ma si simula per vedere se il sistema funziona.

---

## Il WebApp

Tutte queste informazioni sono visibili su una pagina web (PWA — si installa come app su telefono o computer):

**Apri:** [jarvisagent-justfix.github.io/jbe-topspin-webapp](https://jarvisagent-justfix.github.io/jbe-topspin-webapp/)

La pagina ha 4 sezioni:

| Sezione | Cosa trovi |
|---------|------------|
| **Match** | Le partite ATP in programma oggi e nei prossimi giorni, con le quote e la probabilità calcolata dal modello |
| **Value** | Le scommesse consigliate — quelle dove il modello vede un vantaggio rispetto al bookmaker, con quota e importo suggerito |
| **Storico** | Tutte le scommesse fatte finora, quante vinte e quante perse, e l'andamento del portafoglio |
| **Report** | Il riepilogo giornaliero prodotto automaticamente dal sistema |

Si aggiorna **5 volte al giorno** (6:00, 10:00, 14:00, 18:00, 22:00 ora italiana) con i dati più freschi.

---

## Come funziona il ciclo giornaliero?

Ogni giorno il sistema lavora in automatico:

```
1. Scarica le quote di tutti i match ATP in corso
2. Calcola le probabilità per ogni partita
3. Confronta con le quote del bookmaker
4. Trova le value bet
5. Calcola quanto scommettere
6. Aggiorna il portafoglio
7. Pubblica i nuovi dati sul webapp
```

Il tutto senza che nessuno debba fare niente — parte da solo 9 volte al giorno nella fascia oraria 7:00-23:00.

---

## Da dove arrivano i dati?

| Fonte | Cosa fornisce | Copertura |
|-------|---------------|-----------|
| **The Odds API** | Quote live di Bet365, Pinnacle e altri | Il 70%+ dei match ATP in corso |
| **tennis-data.co.uk** | Risultati storici + quote passate | Dal 2008 a oggi |
| **Jeff Sackmann** (GitHub) | Statistiche complete di ogni partita | 76.000+ match, dal 2001 |

Il sistema ha **7 chiavi API** gratuite in rotazione che si ricaricano ogni mese. Quando finiscono, usa i dati in cache dell'ultimo aggiornamento.

---

## Quanto è preciso?

### Nei test su partite passate (2026, ~1.500 partite)

| Metrica | Risultato |
|---------|-----------|
| Precisione modello base (ELO) | 68-72% |
| Precisione modello completo (AI + tutti i fattori) | 88-93% |
| Precisione realistica stimata | 80-85% |

### In prove reali (Wimbledon 2026, primo turno)

| Metrica | Risultato |
|---------|-----------|
| Partite indovinate | 8 su 10 (80%) |
| Rendimento | +22.4% |
| Guadagno virtuale | +44.77€ su 200€ iniziali |

**Attenzione:** questi risultati sono su un periodo breve. Le scommesse sportive sono sempre rischiose — nessun sistema è perfetto.

### Perché il modello a volte sbaglia?
- I bookmaker hanno dati migliori e più aggiornati
- Una partita di tennis ha molta varianza (un paio di punti possono cambiare tutto)
- Infortuni o condizioni meteo non previste
- Il modello è stato addestrato su dati passati — il futuro è sempre diverso

---

## Cosa c'è "sotto il cofano" (per chi è curioso)

Se ti interessa il lato tecnico, il progetto è organizzato così:

```
├── docs/                    # Il sito web (quello che vedi su Pages)
│   ├── index.html           # La pagina principale
│   ├── manifest.json        # Configurazione per installare l'app
│   ├── sw.js                # Per funzionare offline
│   └── api/
│       ├── data.py          # Script che genera i dati
│       └── data.json        # I dati del momento
├── src/                     # Il "cervello" del sistema
│   └── engine/
│       ├── elo_tennis.py    # Sistema di punteggio ELO
│       ├── markov_tennis.py # Probabilità servizio/risposta
│       ├── xgboost_tennis.py# L'algoritmo AI
│       └── value_detector.py# Trova le scommesse di valore
├── scripts/                 # I programmi che fanno funzionare tutto
│   ├── odds_api.py          # Scarica quote dai bookmaker
│   ├── daily_report.py      # Genera il report giornaliero
│   └── generate_webapp_data.py # Prepara i dati per il sito
└── data/                    # I dati (esclusi da git)
    ├── tennis.db            # Database con 76.000+ partite
    └── models/              # I modelli AI addestrati
```

È tutto open source — puoi esplorare il codice su [GitHub](https://github.com/jarvisagent-justfix/jbe-topspin-webapp).

---

## Chi c'è dietro?

JBE TopSpin è un progetto di **JBE (Just Fix)** — un sistema nato per applicare l'intelligenza artificiale al betting sportivo in modo trasparente, senza promesse di facili guadagni.

> **Importante:** Questo progetto è a scopo educativo e di analisi. Le scommesse sportive comportano rischi reali. Non scommettere mai più di quanto puoi permetterti di perdere.
