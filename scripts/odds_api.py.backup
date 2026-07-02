#!/usr/bin/env python3
"""
JBE TopSpin — The Odds API Integration
========================================
Recupera match ATP FUTURI con quote live da 40+ bookmaker.
Confronta con predizioni del modello e trova value bet PRE-MATCH.

API: https://the-odds-api.com (free tier: 500 req/mese)
Documentazione: https://the-odds-api.com/liveapi/guides/v4/

Uso: PYTHONPATH=src /tmp/jbe-venv2/bin/python3 scripts/odds_api.py [--report]

Dipende da: ODDS_API_KEY in /opt/data/.env
"""
import sys, os, json, urllib.request, urllib.error, urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List, Dict

# All debug/error/info output goes to stderr so cron no_agent only delivers clean report
def log(msg: str = ""):
    print(msg, file=sys.stderr)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine.xgboost_tennis import TopSpinEngine
from engine.value_detector import ValueDetector, ValueBet, humanize_stake, KellyCalculator
from paper_portfolio import add_bet
from config import DB_PATH, SURFACES

# --- Config ---
BASE_URL = "https://api.the-odds-api.com/v4"
ATP_SPORT = None  # Auto-detect active tennis sports
REGIONS = "uk,eu,us"       # Bookmaker regioni
MARKETS = "h2h,spreads,totals"    # head-to-head, handicap, over/under
ODDS_FORMAT = "decimal"    # Quote decimali (1.50, 2.00, ...)
DATE_FORMAT = "iso"
CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cache", "odds_matches.json")

# Map The Odds API surfaces to our internal
SURFACE_MAP = {
    "hard": "Hard", "clay": "Clay", "grass": "Grass", "carpet": "Carpet",
}


def get_api_key():
    """Legge ODDS_API_KEY da .env. Usa rotazione automatica."""
    keys = []
    try:
        with open("/opt/data/.env") as f:
            for line in f:
                if line.startswith("ODDS_API_KEY") and "=" in line:
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2 and len(parts[1]) >= 20:
                        keys.append(parts[1])
    except FileNotFoundError:
        pass
    # Fallback: variabili d'ambiente
    if not keys:
        k1 = os.environ.get("ODDS_API_KEY")
        if k1:
            keys.append(k1)
        k2 = os.environ.get("ODDS_API_KEY_2")
        if k2:
            keys.append(k2)
    
    if not keys:
        return None
    
    # Rotazione key: tenta la prima, se fallisce passa alla seconda
    return keys  # Return all keys, caller rotates


def odds_api_request(endpoint: str, params: dict = None) -> Optional[dict]:
    """
    Chiamata generica all'API The Odds API.
    Usa rotazione automatica tra le chiavi disponibili.
    Docs: https://the-odds-api.com/liveapi/guides/v4/
    """
    api_keys = get_api_key()
    if not api_keys:
        log("[ERRORE] ODDS_API_KEY non trovata in /opt/data/.env")
        return None

    for attempt, api_key in enumerate(api_keys):
        url = f"{BASE_URL}/{endpoint}"
        query = [f"apiKey={api_key}"]
        if params:
            for k, v in params.items():
                query.append(f"{k}={urllib.parse.quote(str(v), safe='')}")
        full_url = f"{url}?{'&'.join(query)}"

        try:
            req = urllib.request.Request(full_url, headers={"User-Agent": "JBE-TopSpin/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode()
                # Chiave funzionante → cancella flag exhausted
                try:
                    exhausted_path = os.path.join(os.path.dirname(__file__), "..", "data", "cache", "api_exhausted.json")
                    if os.path.exists(exhausted_path):
                        os.remove(exhausted_path)
                except Exception:
                    pass
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            msg = f"[ERRORE] Odds API HTTP {e.code}: {body[:200]}"
            if e.code == 401:
                msg += f"\n  -> Chiave {attempt+1}/{len(api_keys)} esaurita, provo la prossima..."
                log(msg)
                continue  # Try next key
            else:
                log(msg)
                if e.code == 422:
                    log("  -> Parametri non validi. Verifica sport/market keys.")
                elif e.code == 429:
                    log("  -> Rate limit superato! 500 req/mese sul piano free.")
                return None
        except Exception as e:
            log(f"[ERRORE] Odds API request: {e}")
            return None
    
    log("  -> Tutte le chiavi esaurite.")
    # Scrive flag exhausted per l'alert nell'app
    try:
        exhausted_path = os.path.join(os.path.dirname(__file__), "..", "data", "cache", "api_exhausted.json")
        os.makedirs(os.path.dirname(exhausted_path), exist_ok=True)
        with open(exhausted_path, "w") as f:
            json.dump({
                "exhausted": True,
                "since": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "keys_tried": len(api_keys),
                "last_cache": None
            }, f)
    except Exception:
        pass
    return None


def get_sports() -> list:
    """Recupera lista sport disponibili (utile per debug)."""
    data = odds_api_request("sports")
    if not data:
        return []
    log(f"\n[DEBUG] {len(data)} sport disponibili")
    for s in data:
        if "tennis" in s.get("key", "").lower():
            log(f"  {s['key']:30s} {s.get('title',''):30s} {s.get('active',''):>6}")
    return data


def get_active_tennis_sports() -> list:
    """Recupera tutti gli sport tennis attualmente attivi."""
    data = odds_api_request("sports")
    if not data:
        return []
    tennis_sports = [
        s["key"] for s in data
        if "tennis" in s.get("key", "").lower() and s.get("active", False)
    ]
    if not tennis_sports:
        log("[WARN] Nessuno sport tennis attivo trovato")
    return tennis_sports


def get_upcoming_matches(days_ahead: int = 3) -> list:
    """
    Recupera match tennis in programma nei prossimi giorni con quote.
    Usa sport key fissi (tennis_atp, tennis_wta) invece di auto-detect
    per risparmiare chiamate API.
    """
    tennis_sports = ["tennis"]  # Unica chiave: include ATP+WTA+Wimbledon
    
    all_matches = []
    params_template = {
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": DATE_FORMAT,
    }
    
    for sport_key in tennis_sports:
        data = odds_api_request(f"sports/{sport_key}/odds", params_template)
        if data and isinstance(data, list) and len(data) > 0:
            atp_only = []
            for m in data:
                actual_sport_key = m.get("sport_key", sport_key)
                # Salta match WTA: il sistema ha solo model e DB ATP
                sport_key_match = m.get("sport_key", "")
                if "wta" in sport_key_match.lower():
                    log(f"  [SKIP WTA] {sport_key_match}: {m.get('home_team','?')} vs {m.get('away_team','?')}")
                    continue
                m["player1"] = m.get("home_team", m.get("player1", "?"))
                m["player2"] = m.get("away_team", m.get("player2", "?"))
                # Salva sport_key originale (es. tennis_atp_wimbledon) prima di sovrascrivere
                m["sport_key_original"] = m.get("sport_key", sport_key)
                m["sport_key"] = sport_key
                all_matches.append(m)
                atp_only.append(m)
            log(f"  {sport_key}: {len(atp_only)} match ATP (scartati {len(data) - len(atp_only)} WTA)")
    
    if not all_matches:
        log("[INFO] Nessun match tennis in programma.")
        return []
    
    # Filtra solo match nei prossimi days_ahead giorni
    cutoff = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    matches = []
    
    for m in all_matches:
        try:
            ct_str = m["commence_time"].replace("Z", "+00:00")
            ct = datetime.fromisoformat(ct_str)
            if ct > cutoff:
                continue
        except (ValueError, KeyError):
            continue
        
        # Estrai bookmaker Pinnacle o Bet365 se disponibili
        valid_bms = []
        for bm in m.get("bookmakers", []):
            key = bm.get("key", "").lower()
            if key in ("pinnacle", "bet365", "1xbet", "unibet", "williamhill", "ladbrokes"):
                valid_bms.append(bm)
        
        if not valid_bms and m.get("bookmakers"):
            valid_bms = [m["bookmakers"][0]]
        
        match = {
            "api_id": m["id"],
            "sport_key": m.get("sport_key", sport_key),
            "commence_time": m["commence_time"],
            "player1": m.get("home_team", ""),
            "player2": m.get("away_team", ""),
            "bookmakers": valid_bms,
            "surface": None,  # Da dedurre
        }
        
        matches.append(match)
    
    return matches


def match_players_to_db(db, player1_name: str, player2_name: str) -> tuple:
    """
    Matcha i nomi giocatori dall'API con il DB.
    L'API usa nomi completi ("Novak Djokovic"), il DB ha formato misto.
    
    Returns:
        (player1_id, player2_id) o (None, None) se non trovati
    """
    # Strategia: cerca per nome completo, poi per cognome
    def find_player(name):
        # Prova match esatto
        row = db.conn.execute(
            "SELECT id FROM players WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return row["id"]
        
        # Prova LIKE per cognome
        parts = name.strip().split()
        surname = parts[-1] if parts else name
        row = db.conn.execute(
            "SELECT id FROM players WHERE name LIKE ? ORDER BY id DESC LIMIT 1",
            (f"%{surname}%",)
        ).fetchone()
        if row:
            return row["id"]
        
        return None
    
    p1 = find_player(player1_name)
    p2 = find_player(player2_name)
    return p1, p2


def get_best_odds(bookmakers: list, player1_name: str, player2_name: str) -> dict:
    """
    Estrae le migliori quote dai bookmaker per match winner.
    
    Returns:
        dict con: odds_p1, odds_p2, bookmaker, consensus_avg
    """
    best_odds = {"odds_p1": 0, "odds_p2": 0, "bookmaker": None, "consensus": {},
                 "spreads": None, "totals": None}
    consensus_p1 = []
    consensus_p2 = []
    
    for bm in bookmakers:
        for market in bm.get("markets", []):
            mk = market.get("key")
            outcomes = market.get("outcomes", [])
            
            if mk == "h2h":
                odds = {o["name"]: o.get("price", 0) for o in outcomes}
                p1_odd = odds.get(player1_name, 0)
                p2_odd = odds.get(player2_name, 0)
                
                if p1_odd > 0 and p2_odd > 0:
                    consensus_p1.append(p1_odd)
                    consensus_p2.append(p2_odd)
                    
                    if bm.get("key", "").lower() == "pinnacle":
                        best_odds["odds_p1"] = p1_odd
                        best_odds["odds_p2"] = p2_odd
                        best_odds["bookmaker"] = "Pinnacle"
                    elif bm.get("key", "").lower() == "bet365":
                        if best_odds["bookmaker"] is None:
                            best_odds["odds_p1"] = p1_odd
                            best_odds["odds_p2"] = p2_odd
                            best_odds["bookmaker"] = "Bet365"
                    elif best_odds["odds_p1"] == 0:
                        best_odds["odds_p1"] = p1_odd
                        best_odds["odds_p2"] = p2_odd
                        best_odds["bookmaker"] = bm.get("key", "unknown")
            
            elif mk == "spreads" and best_odds["spreads"] is None:
                # Handicap: {name, price, point}
                best_odds["spreads"] = [{
                    "name": o["name"],
                    "odds": o.get("price", 0),
                    "point": o.get("point", 0)
                } for o in outcomes if o.get("price", 0) > 1.0]
            
            elif mk == "totals" and best_odds["totals"] is None:
                # Over/Under: {name, price, point}
                best_odds["totals"] = [{
                    "name": o["name"],
                    "odds": o.get("price", 0),
                    "point": o.get("point", 0)
                } for o in outcomes if o.get("price", 0) > 1.0]
    
    # Consensus (media di tutti i bookmaker)
    if consensus_p1:
        best_odds["consensus"] = {
            "p1": sum(consensus_p1) / len(consensus_p1),
            "p2": sum(consensus_p2) / len(consensus_p2),
            "n_bookmakers": len(consensus_p1),
        }
    
    return best_odds


def predict_and_find_value(db, engine, match):
    """
    Predice l'esito del match e trova value bet.
    Salva le quote nel DB per riferimento futuro.
    """
    p1_name = match.get("player1") or match.get("home_team", "?")
    p2_name = match.get("player2") or match.get("away_team", "?")
    # Normalizza nel dict per backward compatibility
    match["player1"] = p1_name
    match["player2"] = p2_name
    
    p1_id, p2_id = match_players_to_db(db, p1_name, p2_name)
    if not p1_id or not p2_id:
        return None
    
    match_date = date.today()
    if match.get("commence_time"):
        try:
            ct = datetime.fromisoformat(match["commence_time"].replace("Z", "+00:00"))
            # Converte in ora italiana (UTC+2) prima di estrarre la data
            italian_dt = ct.astimezone(timezone(timedelta(hours=2)))
            match_date = italian_dt.date()
        except (ValueError, AttributeError):
            pass
    
    # Determina superficie (default: Hard se non disponibile)
    surface = match.get("surface") or "Hard"
    
    # Determina best_of: Grand Slam (Wimbledon, US Open, Australian Open, Roland Garros) = Bo5 per uomini
    sport_key = match.get("sport_key_original", match.get("sport_key", "")).lower()
    tournament = match.get("tournament", "").lower()
    best_of = 5 if any(slam in sport_key or slam in tournament 
                        for slam in ["wimbledon", "australian", "roland", "french", "usopen", "grand_slam"]) else 3
    
    # Get best odds from API FIRST (serve per bias correction e best_of decision)
    odds_data = get_best_odds(match["bookmakers"], match["player1"], match["player2"])
    
    if odds_data["odds_p1"] <= 1.0 or odds_data["odds_p2"] <= 1.0:
        return None
    
    # Prediction con ELO + XGBoost calibrato (ora odds_data e' disponibile per bias)
    # Usiamo match_id=0 perche' il match non e' ancora nel DB
    pred = engine.predict(
        0, p1_id, p2_id, surface, match_date, best_of,
        None, None, None, None, None, None,
        odds_p1=odds_data.get("odds_p1"), odds_p2=odds_data.get("odds_p2")
    )
    
    # Clamp: cap probabilità realistiche — nessuna predizione può essere 100%
    # Il Markov tende a dare 1.0 per threshold lontani dalla media (es. Under 42 in Bo3)
    # Platt-like scaling per probabilità estreme
    def clamp_model_prob(p: float) -> float:
        """Calibra probabilità irrealistiche: max 97%, min 3%"""
        if p > 0.97:
            # Scala logistica: 0.999 → ~0.97, 0.99 → ~0.96
            return 0.97 - (1.0 - p) * 20
        if p < 0.03:
            return 0.03 + p * 20
        return p
    
    prob_p1 = clamp_model_prob(pred["prob_player1"])
    prob_p2 = clamp_model_prob(pred["prob_player2"])
    
    bets = []
    # KellyCalculator condiviso per tracciare esposizione giornaliera
    if not hasattr(predict_and_find_value, "kelly"):
        predict_and_find_value.kelly = KellyCalculator(initial_bankroll=200.0)
    kc = predict_and_find_value.kelly
    stakeholder = kc.bankroll
    
    # --- Match Winner (H2H) ---
    implied_p1 = 1.0 / odds_data["odds_p1"]
    edge_p1 = prob_p1 - implied_p1
    
    if prob_p1 >= 0.50 and edge_p1 >= 0.05:
        stake = kc.calculate_stake(edge_p1, odds_data["odds_p1"])
        if stake >= 0.5:
            bets.append(ValueBet(
                match_id=0,
                market="match_winner",
                selection=match["player1"],
                odds=odds_data["odds_p1"],
                model_prob=prob_p1,
                edge=edge_p1,
                stake=stake,
                confidence="HIGH" if edge_p1 > 0.10 else "MEDIUM",
                reason=f"Edge +{edge_p1:.1%} @{odds_data['odds_p1']:.2f} ({odds_data['bookmaker'] or 'API'})"
            ))
    
    implied_p2 = 1.0 / odds_data["odds_p2"]
    edge_p2 = prob_p2 - implied_p2
    
    if prob_p2 >= 0.50 and edge_p2 >= 0.05:
        stake = kc.calculate_stake(edge_p2, odds_data["odds_p2"])
        if stake >= 0.5:
            bets.append(ValueBet(
                match_id=0,
                market="match_winner",
                selection=match["player2"],
                odds=odds_data["odds_p2"],
                model_prob=prob_p2,
                edge=edge_p2,
                stake=stake,
                confidence="HIGH" if edge_p2 > 0.10 else "MEDIUM",
                reason=f"Edge +{edge_p2:.1%} @{odds_data['odds_p2']:.2f} ({odds_data['bookmaker'] or 'API'})"
            ))
    
    # --- Game Handicap (Spreads) via Markov ---
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
                        match_id=0,
                        market="game_handicap",
                        selection=hc_label,
                        odds=odds_val,
                        model_prob=model_prob_hc,
                        edge=edge_hc,
                        stake=stake_hc,
                        confidence="MEDIUM" if edge_hc > 0.12 else "LOW",
                        reason=f"Edge +{edge_hc:.1%} (handicap {point:+.1f}) @{odds_val:.2f}"
                    ))
    
    # --- Over/Under (Totals) via Markov ---
    if odds_data.get("totals") and len(odds_data["totals"]) >= 2:
        if not odds_data.get("spreads"):
            markov_pred = engine.predict_markov(p1_id, p2_id, surface, best_of=best_of)
        
        for outcome in odds_data["totals"]:
            ond_name = outcome["name"].lower()
            threshold = outcome["point"]
            odds_val = outcome["odds"]
            if odds_val <= 1.0:
                continue
            
            if ond_name == "over":
                model_prob_ou = clamp_model_prob(markov_pred["markov_p_over_threshold"](threshold))
            else:
                model_prob_ou = clamp_model_prob(markov_pred["markov_p_under_threshold"](threshold))
            
            implied_ou = 1.0 / odds_val
            edge_ou = model_prob_ou - implied_ou
            
            if model_prob_ou >= 0.40 and edge_ou >= 0.08:
                stake_ou = kc.calculate_stake(edge_ou, odds_val)
                if stake_ou >= 0.5:
                    ou_label = f"O/U {threshold}"
                    bets.append(ValueBet(
                        match_id=0,
                        market="over_under",
                        selection=ou_label,
                        odds=odds_val,
                        model_prob=model_prob_ou,
                        edge=edge_ou,
                        stake=stake_ou,
                        confidence="MEDIUM" if edge_ou > 0.12 else "LOW",
                        reason=f"Edge +{edge_ou:.1%} ({ond_name} {threshold}) @{odds_val:.2f}"
                    ))
    
    return {
        "match": match,
        "match_id": match.get("api_id", 0),
        "p1_name": match["player1"],
        "p2_name": match["player2"],
        "p1_id": p1_id,
        "p2_id": p2_id,
        "surface": surface,
        "match_date": match_date,
        "pred": pred,
        "odds": odds_data,
        "bets": bets,
        "prob_p1": prob_p1,
        "prob_p2": prob_p2,
    }


def generate_odds_report(target_date: date = None) -> str:
    """
    Genera report value bet basato su match FUTURI da The Odds API.
    """
    if target_date is None:
        target_date = date.today()
    
    api_key = get_api_key()
    if not api_key:
        return "⚠️ **The Odds API non configurata.**\nAggiungi ODDS_API_KEY in /opt/data/.env"
    
    log(f"[INFO] Recupero match ATP da The Odds API...")

    from database import TennisDatabase
    db = TennisDatabase(DB_PATH)

    # Try API, fall back to cache on failure
    matches = get_upcoming_matches(days_ahead=3)
    api_ok = bool(matches)
    fonte = "The Odds API"
    
    if not api_ok:
        # API failed — try cache
        cache_dir = os.path.dirname(CACHE_PATH)
        os.makedirs(cache_dir, exist_ok=True)
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH) as f:
                    cached = json.load(f)
                matches = cached.get("matches", [])
                # Filtra WTA dalla cache
                atp_from_cache = []
                for cm in matches:
                    ck = cm.get("sport_key", "")
                    if "wta" in ck.lower() or "wta" in cm.get("player1", "").lower() or "wta" in cm.get("player2", "").lower():
                        log(f"  [CACHE SKIP WTA] {cm.get('player1','?')} vs {cm.get('player2','?')}")
                        continue
                    atp_from_cache.append(cm)
                matches = atp_from_cache
                ts = cached.get("timestamp", "?")
                log(f"[CACHE] Caricati {len(matches)} match ATP da cache (scartati {len(cached.get('matches',[])) - len(atp_from_cache)} WTA, aggiornamento: {ts})")
                fonte = f"Cache (ultimo aggiornamento: {ts})"
                api_ok = True  # Mark as ok so we don't overwrite cache below
            except Exception as e:
                log(f"[CACHE] Errore lettura: {e}")
                matches = []
        else:
            log("[CACHE] Nessun dato in cache.")
    else:
        # API success — save to cache
        try:
            cache_dir = os.path.dirname(CACHE_PATH)
            os.makedirs(cache_dir, exist_ok=True)
            ts = datetime.now(timezone(timedelta(hours=2))).strftime("%d/%m/%Y %H:%M")
            with open(CACHE_PATH, "w") as f:
                json.dump({"timestamp": ts, "matches": matches}, f)
            log(f"[CACHE] Salvati {len(matches)} match (aggiornamento: {ts})")
        except Exception as e:
            log(f"[CACHE] Errore salvataggio: {e}")

    report_lines = []
    report_lines.append(f"🎾 JBE TopSpin — Value Bets Live ({target_date.strftime('%d/%m/%Y')})")
    report_lines.append(f"📡 Fonte: {fonte} | Match trovati: {len(matches)}")
    report_lines.append("")
    
    if not matches:
        report_lines.append("Nessun match ATP in programma nei prossimi 3 giorni.")
        report_lines.append("")
        report_lines.append("💡 Wimbledon inizia il 29/06 — i match compariranno 24-48h prima.")
        report_lines.append("📊 Report retrospettivo disponibile con tennis-data.co.uk.")
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
            
            if result["bets"]:
                value_bets_found += len(result["bets"])
                for bet in result["bets"]:
                    report_lines.append(f"🟢 VALUE BET ({bet.market.replace('_', ' ').title()})")
                    report_lines.append(f"   {ct_display} | {match.get('tournament', match.get('sport_title', '?'))}")
                    report_lines.append(f"   {result['p1_name']} vs {result['p2_name']}")
                    report_lines.append(f"   {bet.to_discord_message(200)}")
                    report_lines.append("")

                    # Auto-log to paper portfolio (settlement via name matching)
                    try:
                        ct = match.get("commence_time", "")
                        italian_time = None
                        if ct:
                            try:
                                dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                                italian_time = dt.astimezone(timezone(timedelta(hours=2))).strftime("%d/%m/%Y %H:%M")
                            except:
                                pass
                        
                        # Deduplicazione: verifica se stessa bet gia' loggata
                        existing = db.conn.execute("""
                            SELECT id FROM paper_portfolio
                            WHERE player1 = ? AND player2 = ? AND market = ? AND selection = ?
                            LIMIT 1
                        """, (result["p1_name"], result["p2_name"], bet.market, bet.selection)).fetchone()
                        if existing:
                            log(f"  [SKIP] Bet gia' loggata (id={existing['id']}): {result['p1_name']} vs {result['p2_name']} | {bet.market} | {bet.selection}")
                        else:
                            add_bet(db, match_id=None,
                                    match_date=result["match_date"], tournament=match.get("tournament", ""),
                                    surface=result.get("surface", ""),
                                    player1=result["p1_name"], player2=result["p2_name"],
                                    selection=bet.selection, odds=bet.odds, model_prob=bet.model_prob,
                                    edge=bet.edge, stake=bet.stake, bankroll_before=200,
                                    market=bet.market,
                                    bookmaker=result["odds"].get("bookmaker", "OddsAPI"),
                                    confidence=bet.confidence, source="odds_api_live",
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
    
    # Summary
    report_lines.append("📊 RIEPILOGO")
    report_lines.append(f"   Match analizzati: {matches_analyzed}")
    report_lines.append(f"   Value bets trovate: {value_bets_found}")
    
    db.close()
    return "\n".join(report_lines)


def test_connection():
    """Test rapido della connessione API."""
    log("=== Test The Odds API ===\n")
    
    key = get_api_key()
    if not key:
        log("[ERRORE] ODDS_API_KEY non trovata.")
        log("  Aggiungi al /opt/data/.env: ODDS_API_KEY=tua_chiave")
        return
    
    log(f"[INFO] API key trovata: {key[:8]}...")
    
    # 1. Test sport list
    log("\n[TEST 1] Sport disponibili tennis:")
    sports = get_sports()
    if sports:
        log("  OK")
    else:
        log("  Nessun dato o errore")
    
    # 2. Test upcoming matches
    log("\n[TEST 2] Match ATP in programma:")
    matches = get_upcoming_matches(days_ahead=7)
    if matches:
        log(f"  {len(matches)} match trovati:")
        for m in matches[:5]:
            ct = m.get("commence_time", "?")[:16]
            bms = [b["key"] for b in m.get("bookmakers", [])[:3]]
            log(f"  • {ct}  {m['player1']:25s} vs {m['player2']:25s}  [{', '.join(bms)}]")
    else:
        log("  Nessun match trovato (potrebbe essere tra stagioni)")
        log("  Check: https://the-odds-api.com/liveapi/guides/v4/#sports")


if __name__ == "__main__":
    import urllib.parse  # needed for quote()
    
    if "--test" in sys.argv:
        test_connection()
    elif "--report" in sys.argv:
        report = generate_odds_report()
        print(report)
        # Save for delivery
        os.makedirs("/opt/data/jbe-topspin-webapp/data/delivery", exist_ok=True)
        fpath = f"/opt/data/jbe-topspin-webapp/data/delivery/odds_report_{date.today().isoformat()}.txt"
        with open(fpath, "w") as f:
            f.write(report)
        log(f"\n[OK] Report salvato: {fpath}")
    else:
        log("Uso: python3 scripts/odds_api.py [--test|--report]")
        log("  --test     Test connessione API e mostra match disponibili")
        log("  --report   Genera report value bet completo")
