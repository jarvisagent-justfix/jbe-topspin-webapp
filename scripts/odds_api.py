#!/usr/bin/env python3
"""
JBE TopSpin — OddsPapi Integration
====================================
Recupera match ATP + quote live da OddsPapi (350+ bookmaker).
Confronta con predizioni del modello e trova value bet PRE-MATCH.

Flusso:
1. Chiama OddsPapi /v4/markets → mappa ID mercati tennis
2. Chiama OddsPapi /v4/fixtures → lista match con nomi
3. Per ogni torneo ATP: /v4/odds-by-tournaments?bookmaker=bet365
4. Fallback a bookmaker=pinnacle se Bet365 non ha mercati
5. Per ogni match: predici (ELO+XGBoost+Markov) e trova value bet
6. Salva in paper_portfolio

API: https://oddspapi.io (free tier: 250 req/mese)
Rotazione: 6 chiavi per superare il limite mensile

Uso: PYTHONPATH=src python3 scripts/odds_api.py [--report]
"""
import sys, os, json, urllib.request, urllib.parse, time
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List, Dict

def log(msg: str = ""):
    print(msg, file=sys.stderr)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine.xgboost_tennis import TopSpinEngine
from engine.value_detector import ValueDetector, ValueBet, KellyCalculator
from paper_portfolio import add_bet
from config import DB_PATH

# --- Config ---
BASE_URL = "https://api.oddspapi.io/v4"
ATP_TOURNAMENT_IDS = [2555]  # Wimbledon Men Singles
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
MARKETS_CACHE = os.path.join(CACHE_DIR, "oddspapi_markets.json")
FIXTURES_CACHE = os.path.join(CACHE_DIR, "oddspapi_fixtures.json")

# Bookmaker priority: Bet365 preferito (accessibile Italia), Pinnacle fallback
BOOKMAKERS = ["bet365", "pinnacle"]

# Market types che ci interessano
RELEVANT_MARKET_TYPES = {"moneyline", "totals-games", "spreads-games"}

# --- Key rotation (6 chiavi OddsPapi) ---
ODDSPAPI_KEYS = [
    "0f8c6e9a-23d9-49df-934e-3222a2566559",
    "dd7cc9b0-84c4-4ce9-9dd7-0939bacce0de",
    "160e7dc6-9667-4d8d-8a7a-70201929c9f5",
    "f5c78387-2025-4fab-b48e-3abbba7ca9e7",
    "3fa463d1-4802-426f-8c6c-f6d8507d2266",
    "ecd8c19a-eb02-42db-bd50-39d91e6bd365",
]

def get_oddspapi_key() -> list:
    """Legge chiavi OddsPapi da .env (se presenti) con fallback alle hardcoded."""
    keys = []
    try:
        with open("/opt/data/.env") as f:
            for line in f:
                if line.startswith("ODDSPAPI_KEY") and "=" in line:
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2 and len(parts[1]) >= 20:
                        keys.append(parts[1])
    except FileNotFoundError:
        pass
    # Fallback hardcoded
    if not keys:
        keys = ODDSPAPI_KEYS[:]
    # Aggiungi anche le env vars
    for i in range(1, 10):
        k = os.environ.get(f"ODDSPAPI_KEY_{i}")
        if k and len(k) >= 20:
            keys.append(k)
    return keys


def odds_api_request(endpoint: str, params: dict = None, key_idx: int = 0) -> Optional[dict]:
    """
    Chiamata a OddsPapi con rotazione automatica chiavi.
    Ritorna il JSON response o None se tutte le chiavi falliscono.
    """
    api_keys = get_oddspapi_key()
    if not api_keys:
        log("[ERRORE] Nessuna chiave OddsPapi configurata.")
        return None

    for attempt in range(len(api_keys)):
        idx = (key_idx + attempt) % len(api_keys)
        api_key = api_keys[idx]
        url = f"{BASE_URL}/{endpoint}?apiKey={api_key}"
        if params:
            for k, v in params.items():
                url += f"&{k}={urllib.parse.quote(str(v))}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JBE-TopSpin/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 429:
                log(f"  [RATE LIMIT] Chiave {idx+1}/{len(api_keys)} — provo prossima...")
                time.sleep(1)
                continue
            elif e.code == 401:
                log(f"  [KEY INVALIDA] Chiave {idx+1}/{len(api_keys)} — provo prossima...")
                continue
            elif e.code == 400:
                log(f"  [BAD REQUEST] {endpoint}: {body[:100]}")
                return None
            else:
                log(f"  [ERRORE HTTP {e.code}] Chiave {idx+1}/{len(api_keys)}: {body[:100]}")
                continue
        except Exception as e:
            log(f"  [ERRORE] Chiave {idx+1}/{len(api_keys)}: {e}")
            continue

    log("  -> Tutte le chiavi OddsPapi esaurite o non funzionanti.")
    return None


def load_market_map() -> dict:
    """
    Carica (con cache) la mappa dei mercati tennis da OddsPapi.
    Returns: {market_id: {marketType, marketName, handicap, outcomes: [{outcomeId, outcomeName}]}}
    """
    # Cache per 24h
    if os.path.exists(MARKETS_CACHE):
        mtime = os.path.getmtime(MARKETS_CACHE)
        age = time.time() - mtime
        if age < 86400:  # < 24h
            try:
                with open(MARKETS_CACHE) as f:
                    return json.load(f)
            except:
                pass

    log("[INFO] Caricamento mercati tennis da OddsPapi...")
    data = odds_api_request("markets", {"language": "en"})
    if not data or not isinstance(data, list):
        log("[WARN] Fallback a mercati vuoti")
        return {}

    market_map = {}
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = m.get("marketId")
        mtype = m.get("marketType")
        if m.get("sportId") == 12 and mtype in RELEVANT_MARKET_TYPES:
            market_map[str(mid)] = {
                "marketType": mtype,
                "marketName": m.get("marketName", "?"),
                "handicap": m.get("handicap", 0),
                "outcomes": {str(o["outcomeId"]): o.get("outcomeName", "?")
                            for o in m.get("outcomes", [])},
            }

    # Salva cache
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(MARKETS_CACHE, "w") as f:
            json.dump(market_map, f)
    except:
        pass

    log(f"  -> {len(market_map)} mercati tennis caricati")
    return market_map


def get_atp_fixtures(days_ahead: int = 3) -> list:
    """
    Recupera fixture ATP da OddsPapi per i prossimi giorni.
    Filtra solo tornei ATP (Wimbledon Men Singles, ATP Challenger, ecc.).
    """
    today = date.today()
    future = today + timedelta(days=days_ahead)

    data = odds_api_request("fixtures", {
        "sportId": "12",
        "from": today.isoformat(),
        "to": future.isoformat(),
    })
    if not data or not isinstance(data, list):
        return []

    # Filtra solo tornei ATP Singles (no WTA, no Doubles, no junior, no UTR)
    atp_keywords = ["Wimbledon Men Singles", "ATP Challenger", "Men Singles"]
    fixtures = []
    for f in data:
        if not isinstance(f, dict):
            continue
        tn = f.get("tournamentName", "")
        if any(kw in tn for kw in atp_keywords) and "Women" not in tn and "Doubles" not in tn:
            fixtures.append(f)

    log(f"  {len(fixtures)} match ATP su {len(data)} totali")
    return fixtures


def get_odds_for_tournament(tournament_id: int, bookmaker: str) -> list:
    """
    Prende odds per un torneo da un bookmaker specifico.
    """
    data = odds_api_request("odds-by-tournaments", {
        "tournamentIds": str(tournament_id),
        "bookmaker": bookmaker,
    })
    if not data or not isinstance(data, list):
        return []
    return data


def extract_price(outcome_data: dict) -> Optional[float]:
    """
    Estrae il prezzo (quota) da un outcome di OddsPapi.
    Struttura: { "players": { "0": { "price": 1.5, "active": true } } }
    """
    players = outcome_data.get("players", {})
    for p_id in ("0", "1"):
        p = players.get(p_id, {})
        if p.get("active", True) and p.get("price", 0) > 1.0:
            return float(p["price"])
    return None


def parse_odds(market_map: dict, fixture: dict, bookmaker_name: str) -> dict:
    """
    Data una fixture con bookmakerOdds, estrae le quote strutturate.
    Returns: dict con odds_h2h_p1, odds_h2h_p2, spreads: [{name, odds, point}], totals: [{name, odds, point}]
    """
    result = {
        "odds_p1": 0, "odds_p2": 0,
        "bookmaker": bookmaker_name,
        "spreads": [], "totals": [],
    }

    bmk_data = fixture.get("bookmakerOdds", {}).get(bookmaker_name, {})
    if not bmk_data or not bmk_data.get("bookmakerIsActive"):
        return result

    markets = bmk_data.get("markets", {})

    for mk_id, mk_data in markets.items():
        info = market_map.get(str(mk_id))
        if not info:
            continue

        mtype = info["marketType"]
        handicap = info.get("handicap", 0)
        outcomes_raw = mk_data.get("outcomes", {})
        outcomes_info = info.get("outcomes", {})

        if mtype == "moneyline" and handicap == 0:
            # Match Winner (H2H)
            prices = []
            for out_id in sorted(outcomes_raw.keys()):
                price = extract_price(outcomes_raw[out_id])
                if price:
                    prices.append(price)
            if len(prices) >= 2:
                result["odds_p1"] = prices[0]
                result["odds_p2"] = prices[1]

        elif mtype == "spreads-games" and abs(handicap) > 0:
            # Game Handicap
            prices = []
            for out_id in sorted(outcomes_raw.keys()):
                price = extract_price(outcomes_raw[out_id])
                if price:
                    out_name = outcomes_info.get(out_id, f"Player {out_id}")
                    # Interpreta il segno dell'handicap per assegnare al giocatore giusto
                    if handicap < 0:
                        # handicap negativo = favorito
                        if not prices:
                            prices.append({"name": f"Favorito {handicap}", "odds": price, "point": handicap})
                        else:
                            prices.append({"name": f"Sfavorito {abs(handicap)}", "odds": price, "point": abs(handicap)})
                    else:
                        if not prices:
                            prices.append({"name": f"Sfavorito +{handicap}", "odds": price, "point": handicap})
                        else:
                            prices.append({"name": f"Favorito -{handicap}", "odds": price, "point": -handicap})
            if prices:
                result["spreads"].extend(prices)

        elif mtype == "totals-games":
            # Over/Under Total Games
            prices = []
            for out_id in sorted(outcomes_raw.keys()):
                price = extract_price(outcomes_raw[out_id])
                if price:
                    out_name = outcomes_info.get(out_id, f"Out{out_id}")
                    if "Over" in str(out_name or ""):
                        prices.insert(0, {"name": f"Over {handicap}", "odds": price, "point": handicap})
                    else:
                        prices.append({"name": f"Under {handicap}", "odds": price, "point": handicap})
            if len(prices) >= 2:
                result["totals"].append({
                    "over": prices[0],
                    "under": prices[1],
                    "point": handicap,
                })

    return result


def get_upcoming_matches(days_ahead: int = 3, key_idx: int = 0) -> list:
    """
    Punto di ingresso principale: recupera match ATP con quote da OddsPapi.
    Priorità: Bet365 > Pinnacle.
    Returns: lista match formattati come dict compatibile col resto del sistema.
    """
    market_map = load_market_map()
    if not market_map:
        log("[WARN] Mappa mercati vuota — procedo con quote H2H grezze")

    fixtures = get_atp_fixtures(days_ahead)
    if not fixtures:
        return []

    # Raggruppa fixture per tournamentId
    from collections import defaultdict
    by_tournament = defaultdict(list)
    for f in fixtures:
        tid = f.get("tournamentId")
        by_tournament[tid].append(f)

    matches = []
    all_tournament_ids = list(by_tournament.keys())

    for bidx, bookmaker in enumerate(BOOKMAKERS):
        if matches:
            log(f"  [OK] Quote trovate con {bookmaker}, salto {BOOKMAKERS[bidx+1:]}")
            break
        log(f"  Tentativo bookmaker: {bookmaker}...")

        for tid in all_tournament_ids:
            time.sleep(0.3)  # Rate limiting
            odds_data = get_odds_for_tournament(tid, bookmaker)
            if not odds_data:
                continue

            # Matcha fixture con odds
            odds_by_fid = {f["fixtureId"]: f for f in odds_data}

            for fixture in fixtures:
                fid = fixture["fixtureId"]
                if fid not in odds_by_fid:
                    continue

                odds_fixture = odds_by_fid[fid]
                if not odds_fixture.get("hasOdds"):
                    continue

                parsed = parse_odds(market_map, odds_fixture, bookmaker)
                if parsed["odds_p1"] <= 1.0 or parsed["odds_p2"] <= 1.0:
                    continue

                # Costruisci match compatibile col sistema
                p1_name = fixture.get("participant1Name", "")
                p2_name = fixture.get("participant2Name", "")
                # OddsPapi usa formato "Cognome, Nome" -> normalizza a "Nome Cognome"
                for name_var in ["participant1Name", "participant2Name"]:
                    raw = fixture.get(name_var, "")
                    if ", " in raw:
                        parts = raw.split(", ", 1)
                        normalized = f"{parts[1]} {parts[0]}"
                        fixture[name_var] = normalized

                ct = fixture.get("startTime", "")
                match = {
                    "api_id": fid,
                    "sport_key": "tennis",
                    "commence_time": ct,
                    "player1": fixture.get("participant1Name", "?"),
                    "player2": fixture.get("participant2Name", "?"),
                    "tournament": fixture.get("tournamentName", "ATP"),
                    "surface": None,
                    "bookmakers": [{
                        "key": bookmaker,
                        "markets": [
                            {"key": "h2h", "outcomes": [
                                {"name": fixture["participant1Name"], "price": parsed["odds_p1"]},
                                {"name": fixture["participant2Name"], "price": parsed["odds_p2"]},
                            ]},
                        ]
                    }],
                }

                # Aggiungi spreads e totals se disponibili
                if parsed["spreads"]:
                    spreads_outcomes = []
                    for s in parsed["spreads"]:
                        spreads_outcomes.append({
                            "name": s["name"], "price": s["odds"], "point": s["point"]
                        })
                    match["bookmakers"][0]["markets"].append({
                        "key": "spreads", "outcomes": spreads_outcomes,
                    })

                if parsed["totals"]:
                    totals_outcomes = []
                    for t in parsed["totals"]:
                        totals_outcomes.append({
                            "name": t["over"]["name"], "price": t["over"]["odds"], "point": t["point"]
                        })
                        totals_outcomes.append({
                            "name": t["under"]["name"], "price": t["under"]["odds"], "point": t["point"]
                        })
                    match["bookmakers"][0]["markets"].append({
                        "key": "totals", "outcomes": totals_outcomes,
                    })

                match["odds_data"] = parsed
                matches.append(match)

    log(f"  Totale match con odds: {len(matches)}")
    return matches


def match_players_to_db(db, player1_name: str, player2_name: str) -> tuple:
    """Matcha i nomi giocatori dal DB (stessa logica della vecchia odds_api)."""
    def find_player(name):
        row = db.conn.execute(
            "SELECT id FROM players WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return row["id"]
        parts = name.strip().split()
        surname = parts[-1] if parts else name
        row = db.conn.execute(
            "SELECT id FROM players WHERE name LIKE ? ORDER BY id DESC LIMIT 1",
            (f"%{surname}%",)
        ).fetchone()
        if row:
            return row["id"]
        return None

    return find_player(player1_name), find_player(player2_name)


def get_best_odds(bookmakers: list, player1_name: str, player2_name: str) -> dict:
    """Estrae le migliori quote dai bookmaker (stessa interfaccia della vecchia versione)."""
    # Nella nuova versione, i bookmakers[0] contiene gia' le quote parsate
    if bookmakers and len(bookmakers) > 0:
        bm = bookmakers[0]
        bm_key = bm.get("key", "bet365")
        best = {"odds_p1": 0, "odds_p2": 0, "bookmaker": bm_key, "consensus": {},
                "spreads": None, "totals": None}

        for market in bm.get("markets", []):
            mk = market.get("key")
            outcomes = market.get("outcomes", [])

            if mk == "h2h":
                odds = {o["name"]: o.get("price", 0) for o in outcomes}
                best["odds_p1"] = odds.get(player1_name, 0)
                best["odds_p2"] = odds.get(player2_name, 0)
                best["bookmaker"] = bm_key

            elif mk == "spreads":
                best["spreads"] = [{
                    "name": o["name"], "odds": o.get("price", 0),
                    "point": o.get("point", 0)
                } for o in outcomes if o.get("price", 0) > 1.0]

            elif mk == "totals":
                best["totals"] = [{
                    "name": o["name"], "odds": o.get("price", 0),
                    "point": o.get("point", 0)
                } for o in outcomes if o.get("price", 0) > 1.0]

        return best
    return {"odds_p1": 0, "odds_p2": 0, "bookmaker": None, "consensus": {},
            "spreads": None, "totals": None}


def predict_and_find_value(db, engine, match):
    """Predice e trova value bet (identica logica della vecchia versione)."""
    p1_name = match.get("player1", "?")
    p2_name = match.get("player2", "?")

    p1_id, p2_id = match_players_to_db(db, p1_name, p2_name)
    if not p1_id or not p2_id:
        return None

    match_date = date.today()
    if match.get("commence_time"):
        try:
            ct = datetime.fromisoformat(match["commence_time"].replace("Z", "+00:00"))
            italian_dt = ct.astimezone(timezone(timedelta(hours=2)))
            match_date = italian_dt.date()
        except (ValueError, AttributeError):
            pass

    surface = match.get("surface") or "Hard"
    sport_key = match.get("sport_key", "").lower()
    tournament = match.get("tournament", "").lower()
    best_of = 5 if any(slam in sport_key or slam in tournament
                       for slam in ["wimbledon", "australian", "roland", "french", "usopen", "grand_slam"]) else 3

    odds_data = get_best_odds(match["bookmakers"], match["player1"], match["player2"])
    if odds_data["odds_p1"] <= 1.0 or odds_data["odds_p2"] <= 1.0:
        return None

    pred = engine.predict(
        0, p1_id, p2_id, surface, match_date, best_of,
        None, None, None, None, None, None,
        odds_p1=odds_data.get("odds_p1"), odds_p2=odds_data.get("odds_p2")
    )

    def clamp_model_prob(p: float) -> float:
        if p > 0.97:
            return 0.97 - (1.0 - p) * 20
        if p < 0.03:
            return 0.03 + p * 20
        return p

    prob_p1 = clamp_model_prob(pred["prob_player1"])
    prob_p2 = clamp_model_prob(pred["prob_player2"])

    bets = []
    if not hasattr(predict_and_find_value, "kelly"):
        predict_and_find_value.kelly = KellyCalculator(initial_bankroll=200.0)
    kc = predict_and_find_value.kelly

    # H2H
    implied_p1 = 1.0 / odds_data["odds_p1"]
    edge_p1 = prob_p1 - implied_p1
    if prob_p1 >= 0.50 and edge_p1 >= 0.05:
        stake = kc.calculate_stake(edge_p1, odds_data["odds_p1"])
        if stake >= 0.5:
            bets.append(ValueBet(
                match_id=0, market="match_winner",
                selection=match["player1"], odds=odds_data["odds_p1"],
                model_prob=prob_p1, edge=edge_p1, stake=stake,
                confidence="HIGH" if edge_p1 > 0.10 else "MEDIUM",
                reason=f"Edge +{edge_p1:.1%} @{odds_data['odds_p1']:.2f} ({odds_data['bookmaker'] or 'API'})"
            ))

    implied_p2 = 1.0 / odds_data["odds_p2"]
    edge_p2 = prob_p2 - implied_p2
    if prob_p2 >= 0.50 and edge_p2 >= 0.05:
        stake = kc.calculate_stake(edge_p2, odds_data["odds_p2"])
        if stake >= 0.5:
            bets.append(ValueBet(
                match_id=0, market="match_winner",
                selection=match["player2"], odds=odds_data["odds_p2"],
                model_prob=prob_p2, edge=edge_p2, stake=stake,
                confidence="HIGH" if edge_p2 > 0.10 else "MEDIUM",
                reason=f"Edge +{edge_p2:.1%} @{odds_data['odds_p2']:.2f} ({odds_data['bookmaker'] or 'API'})"
            ))

    # Game Handicap (via Markov)
    if odds_data.get("spreads") and len(odds_data["spreads"]) >= 2:
        markov_pred = engine.predict_markov(p1_id, p2_id, surface, best_of=best_of)
        for outcome in odds_data["spreads"]:
            ond_name = outcome["name"]
            point = outcome["point"]
            odds_val = outcome["odds"]
            if odds_val <= 1.0:
                continue
            handicap = abs(point)
            if ond_name == match["player1"]:
                model_prob_hc = clamp_model_prob(markov_pred["markov_p_cover_handicap"](handicap))
            else:
                model_prob_hc = clamp_model_prob(1 - markov_pred["markov_p_cover_handicap"](handicap))
            implied_hc = 1.0 / odds_val
            edge_hc = model_prob_hc - implied_hc
            if model_prob_hc >= 0.40 and edge_hc >= 0.08:
                stake_hc = kc.calculate_stake(edge_hc, odds_val)
                if stake_hc >= 0.5:
                    hc_label = f"{ond_name} {point:+.1f}"
                    bets.append(ValueBet(
                        match_id=0, market="game_handicap",
                        selection=hc_label, odds=odds_val,
                        model_prob=model_prob_hc, edge=edge_hc, stake=stake_hc,
                        confidence="MEDIUM" if edge_hc > 0.12 else "LOW",
                        reason=f"Edge +{edge_hc:.1%} (handicap {point:+.1f}) @{odds_val:.2f}"
                    ))

    # Over/Under (via Markov)
    if odds_data.get("totals") and len(odds_data["totals"]) >= 2:
        if not odds_data.get("spreads"):
            markov_pred = engine.predict_markov(p1_id, p2_id, surface, best_of=best_of)

        for outcome in odds_data["totals"]:
            threshold = outcome["point"]
            odds_val = outcome["odds"]
            if odds_val <= 1.0:
                continue
            model_prob_ou = clamp_model_prob(markov_pred["markov_p_over_threshold"](threshold))
            implied_ou = 1.0 / odds_val
            edge_ou = model_prob_ou - implied_ou
            if model_prob_ou >= 0.40 and edge_ou >= 0.08:
                stake_ou = kc.calculate_stake(edge_ou, odds_val)
                if stake_ou >= 0.5:
                    ou_label = f"O/U {threshold}"
                    bets.append(ValueBet(
                        match_id=0, market="over_under",
                        selection=ou_label, odds=odds_val,
                        model_prob=model_prob_ou, edge=edge_ou, stake=stake_ou,
                        confidence="MEDIUM" if edge_ou > 0.12 else "LOW",
                        reason=f"Edge +{edge_ou:.1%} @{odds_val:.2f}"
                    ))

    # === STRATEGIA FINALE: massimo 2 bet per match ===
    # Regole basate su analisi dati reali (32 bet risolte + backtest 1135 match)
    
    # 1. Blocca match_winner su odds >= 2.0 (WR 33% vs 75% su odds < 2.0)
    # 2. game_handicap solo se edge > 12% E odds < 2.5
    # 3. over_under edge minimo 8%
    # 4. Confidence: HIGH su match_winner → MEDIUM (WR 31% vs 57%)
    
    filtered = []
    for b in bets:
        if b.market == "match_winner" and b.odds >= 2.0:
            continue
        if b.market == "game_handicap" and (b.edge < 0.12 or b.odds >= 2.5):
            continue
        if b.market == "over_under" and b.edge < 0.08:
            continue
        # Downgrade confidence su match_winner
        if b.market == "match_winner" and b.confidence == "HIGH":
            b.confidence = "MEDIUM"
        filtered.append(b)
    
    # Ordina: over_under > match_winner > game_handicap, poi per edge
    priority = {"over_under": 0, "match_winner": 1, "game_handicap": 2}
    filtered.sort(key=lambda b: (priority.get(b.market, 9), -b.edge))
    
    # Seleziona fino a 2 (mercati diversi: massimo 1 per tipo)
    best = []
    for b in filtered:
        if len(best) >= 2:
            break
        same_market = [x for x in best if x.market == b.market]
        if not same_market:
            best.append(b)
    
    bets = best[:2]
    
    return {
        "match": match,
        "match_id": match.get("api_id", 0),
        "p1_name": match["player1"], "p2_name": match["player2"],
        "p1_id": p1_id, "p2_id": p2_id,
        "surface": surface, "match_date": match_date,
        "pred": pred, "odds": odds_data, "bets": bets,
        "prob_p1": prob_p1, "prob_p2": prob_p2,
    }


def generate_odds_report(target_date: date = None) -> str:
    """Genera report value bet (stessa interfaccia)."""
    if target_date is None:
        target_date = date.today()

    log(f"[INFO] Recupero match ATP da OddsPapi...")

    from database import TennisDatabase
    db = TennisDatabase(DB_PATH)

    matches = get_upcoming_matches(days_ahead=3)
    fonte = "OddsPapi (Bet365/Pinnacle)"

    report_lines = []
    report_lines.append(f"🎾 JBE TopSpin — Value Bets ({target_date.strftime('%d/%m/%Y')})")
    report_lines.append(f"📡 Fonte: {fonte} | Match trovati: {len(matches)}")
    report_lines.append("")

    if not matches:
        report_lines.append("Nessun match ATP in programma nei prossimi 3 giorni.")
        db.close()
        return "\n".join(report_lines)

    engine = TopSpinEngine(db, load_models=True)
    engine.elo_engine.load_all_ratings()

    value_bets_found = 0
    matches_analyzed = 0

    for match in matches:
        try:
            result = predict_and_find_value(db, engine, match)
            if result is None:
                report_lines.append(f"❌ {match['player1']} vs {match['player2']} — Giocatori non trovati nel DB")
                continue

            matches_analyzed += 1
            ct_display = match.get("commence_time", "?")[:16].replace("T", " ")
            bm_name = result["odds"].get("bookmaker", "OddsPapi")

            if result["bets"]:
                value_bets_found += len(result["bets"])
                for bet in result["bets"]:
                    report_lines.append(f"🟢 VALUE BET ({bet.market.replace('_', ' ').title()})")
                    report_lines.append(f"   {ct_display} | {match.get('tournament', 'ATP')} | 🏦 {bm_name}")
                    report_lines.append(f"   {result['p1_name']} vs {result['p2_name']}")
                    report_lines.append(f"   {bet.to_discord_message(200)}")
                    report_lines.append("")

                    try:
                        ct = match.get("commence_time", "")
                        italian_time = None
                        if ct:
                            try:
                                dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                                italian_time = dt.astimezone(timezone(timedelta(hours=2))).strftime("%d/%m/%Y %H:%M")
                            except:
                                pass

                        existing = db.conn.execute("""
                            SELECT id FROM paper_portfolio
                            WHERE player1 = ? AND player2 = ? AND market = ? AND selection = ?
                            LIMIT 1
                        """, (result["p1_name"], result["p2_name"], bet.market, bet.selection)).fetchone()
                        if existing:
                            log(f"  [SKIP] Bet gia loggata (id={existing['id']}): {result['p1_name']} vs {result['p2_name']} | {bet.market} | {bet.selection}")
                        else:
                            add_bet(db, match_id=None,
                                    match_date=result["match_date"], tournament=match.get("tournament", ""),
                                    surface=result.get("surface", ""),
                                    player1=result["p1_name"], player2=result["p2_name"],
                                    selection=bet.selection, odds=bet.odds, model_prob=bet.model_prob,
                                    edge=bet.edge, stake=bet.stake, bankroll_before=200,
                                    market=bet.market,
                                    bookmaker=bm_name,
                                    confidence=bet.confidence, source="oddspapi",
                                    match_datetime=italian_time)
                    except Exception as e_bet:
                        log(f"  [WARN] Portfolio log: {e_bet}")

            else:
                fav_name = result["p1_name"] if result["prob_p1"] >= 0.5 else result["p2_name"]
                fav_prob = max(result["prob_p1"], result["prob_p2"])
                best_odd = result["odds"]["odds_p1"] if result["prob_p1"] >= 0.5 else result["odds"]["odds_p2"]
                if fav_prob >= 0.65:
                    report_lines.append(f"⚪ {ct_display}")
                    report_lines.append(f"   {result['p1_name']} vs {result['p2_name']}")
                    report_lines.append(f"   Modello: {fav_name} {fav_prob:.0%} @{best_odd:.2f} (nessun edge sufficiente)")
                    report_lines.append("")
                else:
                    report_lines.append(f"⚪ {ct_display} — {result['p1_name']} vs {result['p2_name']} (confidenza bassa)")
                    report_lines.append("")

        except Exception as e:
            report_lines.append(f"⚠️ Errore {match.get('player1','?')} vs {match.get('player2','?')}: {e}")
            report_lines.append("")

    report_lines.append("📊 RIEPILOGO")
    report_lines.append(f"   Bookmaker: {fonte}")
    report_lines.append(f"   Match analizzati: {matches_analyzed}")
    report_lines.append(f"   Value bets trovate: {value_bets_found}")

    db.close()
    return "\n".join(report_lines)


def resolve_pending_bets():
    """
    Controlla i match pending in paper_portfolio e verifica se sono finiti
    usando OddsPapi /v4/settlements e /v4/scores.
    Aggiorna lo stato: won/lost con profitto e data settling.
    """
    import sqlite3
    from database import TennisDatabase

    db = TennisDatabase(DB_PATH)
    log("[INFO] Risoluzione value bet pending...")

    # Prendi tutte le bet pending (non solo quelle recenti)
    pending = db.conn.execute("""
        SELECT pp.id, pp.player1, pp.player2, pp.selection, pp.market,
               pp.odds, pp.stake, pp.match_date, pp.match_datetime,
               tm.id as tennis_match_id
        FROM paper_portfolio pp
        LEFT JOIN tennis_matches tm ON pp.player1 LIKE '%' || tm.winner_id || '%'
        WHERE pp.status = 'pending'
        ORDER BY pp.match_date DESC
    """).fetchall()

    if not pending:
        log("  Nessuna bet pending da risolvere.")
        db.close()
        return 0

    log(f"  Bet pending trovate: {len(pending)}")

    # Raggruppa per match (coppia giocatori + data)
    from collections import defaultdict
    by_match = defaultdict(list)
    for p in pending:
        key = (p["player1"], p["player2"], str(p["match_date"] or ""))
        by_match[key].append(p)

    resolved = 0
    errors = 0

    for (p1, p2, mdate), bets in by_match.items():
        try:
            # Prendi fixture OddsPapi per questo match
            # Costruisci query nomi (OddsPapi usa formato "Cognome, Nome")
            # Converti "Nome Cognome" in "Cognome, Nome"
            def to_oddspapi_name(name):
                parts = name.strip().split()
                if len(parts) >= 2:
                    return f"{parts[-1]}, {' '.join(parts[:-1])}"
                return name

            p1_api = to_oddspapi_name(p1)
            p2_api = to_oddspapi_name(p2)

            # Cerca fixture OddsPapi per questi giocatori
            from datetime import date
            today = date.today()
            fixtures = odds_api_request("fixtures", {
                "sportId": "12",
                "from": "2026-06-25",  # Ampio range per prendere match passati
                "to": today.isoformat(),
            })

            if not fixtures or not isinstance(fixtures, list):
                errors += 1
                continue

            # Matcha per nomi giocatori
            match_fixture = None
            for f in fixtures:
                if not isinstance(f, dict):
                    continue
                fp1 = f.get("participant1Name", "")
                fp2 = f.get("participant2Name", "")
                if (p1_api in fp1 and p2_api in fp2) or (p1_api in fp2 and p2_api in fp1):
                    match_fixture = f
                    break
                # Fallback: match parziale per cognome
                p1_surname = p1.split()[-1] if p1.split() else ""
                p2_surname = p2.split()[-1] if p2.split() else ""
                if p1_surname and p2_surname:
                    if (p1_surname in fp1 and p2_surname in fp2) or (p1_surname in fp2 and p2_surname in fp1):
                        match_fixture = f
                        break

            if not match_fixture:
                log(f"  [SKIP] Match non trovato su OddsPapi: {p1} vs {p2}")
                errors += 1
                continue

            status_id = match_fixture.get("statusId")
            if status_id != 2:  # Non ancora finito
                continue

            # Match finito! Prendi settlements
            fid = str(match_fixture["fixtureId"])
            time.sleep(0.5)
            settlements = odds_api_request("settlements", {"fixtureId": fid})
            if not settlements or "markets" not in settlements:
                log(f"  [SKIP] Nessun settlement per {p1} vs {p2}")
                errors += 1
                continue

            # Prendi anche scores per conferma e statistiche
            time.sleep(0.3)
            scores_data = odds_api_request("scores", {"fixtureId": fid})

            # Determina vincitore dal settlement market 121 (H2H)
            market_h2h = settlements.get("markets", {}).get("121", {})
            if not market_h2h:
                continue

            outcomes = market_h2h.get("outcomes", {})
            outcome_121 = outcomes.get("121", {}).get("players", {}).get("0", {}).get("result", "")
            outcome_122 = outcomes.get("122", {}).get("players", {}).get("0", {}).get("result", "")

            # outcome 121 = participant 1 (OddsPapi), outcome 122 = participant 2
            # OddsPapi nomi sono in formato "Cognome, Nome" quindi dobbiamo mappare
            api_p1 = match_fixture.get("participant1Name", "")
            api_p2 = match_fixture.get("participant2Name", "")

            # Chi ha vinto secondo OddsPapi?
            if outcome_121 == "WIN":
                winner = api_p1
            elif outcome_122 == "WIN":
                winner = api_p2
            else:
                log(f"  [SKIP] Settlement ambiguo per {p1} vs {p2}: {outcome_121}/{outcome_122}")
                continue

            # Prendi il punteggio per il log
            score_str = ""
            if scores_data and "scores" in scores_data:
                periods = scores_data["scores"].get("periods", {})
                result = periods.get("result", {})
                s1 = result.get("participant1Score", "?")
                s2 = result.get("participant2Score", "?")
                score_str = f"{s1}-{s2}"

            # Per ogni bet di questo match, determina se è won/lost
            for bet in bets:
                selection = bet["selection"]
                market_type = bet["market"]

                # Determina se la selezione corrisponde al vincitore
                is_won = False
                if market_type == "match_winner":
                    # La selezione contiene il nome del giocatore
                    if winner and (winner.split(",")[0].strip() in selection or
                                   any(w in selection for w in winner.replace(",", "").split())):
                        is_won = True
                elif market_type == "over_under":
                    # Per O/U non possiamo determinarlo senza i total games effettivi
                    # Per ora skippiamo — richiederebbe i games totali da scores
                    continue
                elif market_type == "game_handicap":
                    # Per handicap servirebbe il margine di games — skippiamo
                    continue

                if is_won:
                    profit = bet["stake"] * (bet["odds"] - 1)
                    db.conn.execute("""
                        UPDATE paper_portfolio
                        SET status = 'won', result = ?, settled_at = datetime('now')
                        WHERE id = ?
                    """, (profit, bet["id"]))
                    log(f"  ✅ {p1[:18]:18} vs {p2[:18]:18} — {selection[:25]:25} → VINTA (+${profit:.2f}) [{score_str}]")
                else:
                    profit = -bet["stake"]
                    db.conn.execute("""
                        UPDATE paper_portfolio
                        SET status = 'lost', result = ?, settled_at = datetime('now')
                        WHERE id = ?
                    """, (profit, bet["id"]))
                    log(f"  ❌ {p1[:18]:18} vs {p2[:18]:18} — {selection[:25]:25} → PERSA (-${bet['stake']:.2f}) [{score_str}]")

                resolved += 1

        except Exception as e:
            log(f"  [ERRORE] {p1} vs {p2}: {e}")
            errors += 1
            continue

    db.conn.commit()
    db.close()
    log(f"\n[OK] Bet risolte: {resolved}, errori: {errors}")
    return resolved


def test_connection():
    """Test rapido connessione OddsPapi."""
    log("=== Test OddsPapi ===\n")

    keys = get_oddspapi_key()
    log(f"[INFO] Chiavi trovate: {len(keys)}")
    log(f"[INFO] Prima chiave: {keys[0][:12]}...")

    log("\n[TEST 1] Mercati tennis:")
    markets = load_market_map()
    log(f"  {len(markets)} mercati caricati")

    log("\n[TEST 2] Match ATP:")
    matches = get_upcoming_matches(days_ahead=3)
    if matches:
        log(f"  {len(matches)} match con odds:")
        for m in matches[:5]:
            ct = m.get("commence_time", "?")[:16]
            bm = m.get("odds_data", {}).get("bookmaker", "?")
            sp = len(m.get("odds_data", {}).get("spreads", []))
            tt = len(m.get("odds_data", {}).get("totals", []))
            log(f"  • {ct}  {m['player1'][:22]:22} vs {m['player2'][:22]:22}  [{bm}] spreads={sp} totals={tt}")
    else:
        log("  Nessun match trovato")


if __name__ == "__main__":
    if "--test" in sys.argv:
        test_connection()
    elif "--settle" in sys.argv:
        resolve_pending_bets()
    elif "--report" in sys.argv:
        report = generate_odds_report()
        print(report)
        os.makedirs("/opt/data/jbe-topspin-webapp/data/delivery", exist_ok=True)
        fpath = f"/opt/data/jbe-topspin-webapp/data/delivery/odds_report_{date.today().isoformat()}.txt"
        with open(fpath, "w") as f:
            f.write(report)
        log(f"\n[OK] Report salvato: {fpath}")
    else:
        log("Uso: python3 scripts/odds_api.py [--test|--report]")
        log("  --test     Test connessione API OddsPapi")
        log("  --report   Genera report value bet completo")
