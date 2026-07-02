#!/usr/bin/env python3
"""
JBE TopSpin — Historical Odds Fetcher
======================================
Recupera quote storiche da OddsPapi per tutti i match ATP dal 1° Gennaio 2026,
le matcha con tennis_matches e salva in tennis_odds.

Flusso:
  Fase 1: Fetch fixture IDs da OddsPapi, match con tennis_matches, salva fixtureId
  Fase 2: Per ogni match matched, scarica historical-odds (GRATIS), estrae closing odds

Uso:
  PYTHONPATH=src python3 scripts/fetch_historical_odds.py                    # full run
  PYTHONPATH=src python3 scripts/fetch_historical_odds.py --phase 1          # solo matching
  PYTHONPATH=src python3 scripts/fetch_historical_odds.py --phase 2          # solo odds
  PYTHONPATH=src python3 scripts/fetch_historical_odds.py --dry-run          # report senza salvare
  PYTHONPATH=src python3 scripts/fetch_historical_odds.py --status           # report copertura

Richiede: pip install thefuzz python-Levenshtein
"""

import sys, os, json, urllib.request, time, re, sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple
from collections import defaultdict

# --- Path setup ---
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
src_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(src_dir)

try:
    from config import DB_PATH
except ImportError:
    DB_PATH = os.path.join(project_root, "data", "tennis.db")

from thefuzz import fuzz

# --- Config ---
BASE_URL = "https://api.oddspapi.io/v4"
CHECKPOINT_FILE = os.path.join(project_root, "data", "cache", "historical_checkpoint.json")
FIXTURES_CACHE = os.path.join(project_root, "data", "cache", "historical_fixtures.json")

ODDSPAPI_KEYS = [
    "0f8c6e9a-23d9-49df-934e-3222a2566559",
    "dd7cc9b0-84c4-4ce9-9dd7-0939bacce0de",
    "160e7dc6-9667-4d8d-8a7a-70201929c9f5",
    "f5c78387-2025-4fab-b48e-3abbba7ca9e7",
    "3fa463d1-4802-426f-8c6c-f6d8507d2266",
    "ecd8c19a-eb02-42db-bd50-39d91e6bd365",
]

TOUR_LEVELS_ATP = {'A', 'M', 'G', 'F', 'C', 'D'}  # ATP, M1000, GS, Finals, Challenger, Davis
MIN_MATCH_SCORE = 78

# --- Market IDs ---
MARKET_MONEYLINE = "121"

# --- Logging ---
def log(msg: str = ""):
    print(msg, file=sys.stderr)


def get_oddspapi_keys() -> List[str]:
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
    for i in range(1, 10):
        k = os.environ.get(f"ODDSPAPI_KEY_{i}")
        if k and len(k) >= 20:
            keys.append(k)
    if not keys:
        keys = ODDSPAPI_KEYS[:]
    return keys


def odds_api_request(endpoint: str, params: dict = None, timeout: int = 20) -> Optional[dict]:
    """Call OddsPapi endpoint with key rotation. No rotation on 403/404."""
    keys = get_oddspapi_keys()
    if not keys:
        log("[ERRORE] Nessuna chiave configurata.")
        return None
    for attempt in range(len(keys)):
        api_key = keys[attempt % len(keys)]
        url = f"{BASE_URL}/{endpoint}?apiKey={api_key}"
        if params:
            for k, v in params.items():
                url += f"&{k}={urllib.parse.quote(str(v))}"
        req = urllib.request.Request(url, headers={"User-Agent": "JBE-TopSpin/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code in (404, 403):
                return None
            if e.code == 429:
                log(f"  [429] Attempt {attempt+1}, waiting 3s...")
                time.sleep(3)
                continue
            log(f"  [HTTP {e.code}] {body[:100]}")
            return None
        except Exception as ex:
            log(f"  [ERR] {ex}")
            return None
    return None


# --- Name matching ---

def normalize_name(name: str) -> Tuple[str, str]:
    """(first, last) lowercase."""
    name = name.strip()
    if ',' in name:
        parts = [p.strip().lower() for p in name.split(',', 1)]
        if len(parts) == 2:
            return (parts[1], parts[0])
    parts = name.lower().split()
    if len(parts) >= 2:
        return (parts[0], parts[-1])
    return (name.lower(), "")


def match_players(o1: str, o2: str, d1: str, d2: str) -> float:
    """Fuzzy match two player pairs. Returns 0-100."""
    def score_pair(a, b):
        af, al = normalize_name(a)
        bf, bl = normalize_name(b)
        return fuzz.ratio(f"{al} {af}", f"{bl} {bf}")
    
    direct = (score_pair(o1, d1) + score_pair(o2, d2)) / 2
    swapped = (score_pair(o1, d2) + score_pair(o2, d1)) / 2
    
    # Bonus if last names match
    o1l, o2l = normalize_name(o1)[1], normalize_name(o2)[1]
    d1l, d2l = normalize_name(d1)[1], normalize_name(d2)[1]
    last_ok = (o1l == d1l and o2l == d2l) or (o1l == d2l and o2l == d1l)
    
    best = max(direct, swapped)
    return min(best + (15 if last_ok else 0), 100)


# --- DB ---

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def ensure_column(db):
    cur = db.execute("PRAGMA table_info(tennis_matches)")
    cols = [r[1] for r in cur.fetchall()]
    if 'oddspapi_fixture_id' not in cols:
        log("[DB] Aggiungo colonna oddspapi_fixture_id...")
        db.execute("ALTER TABLE tennis_matches ADD COLUMN oddspapi_fixture_id TEXT")
        db.commit()
        log("[DB] Fatto.")


def get_db_matches_for_date(db, match_date: str) -> List[dict]:
    cur = db.execute("""
        SELECT tm.id, p1.name as p1_name, p2.name as p2_name,
               tm.tournament, tm.tour_level, tm.oddspapi_fixture_id
        FROM tennis_matches tm
        JOIN players p1 ON tm.winner_id = p1.id
        JOIN players p2 ON tm.loser_id = p2.id
        WHERE tm.match_date = ? AND tm.tour_level IN ({})
    """.format(','.join('?' * len(TOUR_LEVELS_ATP))), [match_date] + list(TOUR_LEVELS_ATP))
    return [dict(r) for r in cur.fetchall()]


def save_fixture_id(db, match_id: int, fixture_id: str):
    db.execute("UPDATE tennis_matches SET oddspapi_fixture_id = ? WHERE id = ?",
               (fixture_id, match_id))
    db.commit()


# --- Phase 1: Fetch fixtures & match ---

# Tournaments to EXCLUDE (no odds available)
EXCLUDED_TOURNAMENTS = {'united cup', 'next gen atp finals'}

def is_valid_tournament(tname: str) -> bool:
    t = tname.lower()
    if any(e in t for e in EXCLUDED_TOURNAMENTS):
        return False
    # Must be ATP, Challenger, Grand Slam, or Davis Cup Men Singles
    return ('atp' in t or 'grand slam' in t or 'davis cup' in t) and 'singles' in t and 'doubles' not in t


def phase1_fetch_matches(start_date: str, end_date: str, dry_run: bool = False) -> dict:
    """Fetch fixtures from OddsPapi in 10-day windows, match to DB."""
    db = get_db()
    ensure_column(db)
    
    stats = {"fixtures_fetched": 0, "relevant": 0, "matched": 0, "already": 0, "api_calls": 0}
    
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)
    window = timedelta(days=10)
    
    current = s
    key_idx = 0
    max_fixtures_per_call = 0
    
    while current <= e:
        win_end = min(current + window - timedelta(days=1), e)
        from_str = current.isoformat()
        to_str = win_end.isoformat()
        
        log(f"\n  [{from_str} → {to_str}] Fetching fixtures...")
        keys = get_oddspapi_keys()
        api_key = keys[key_idx % len(keys)]
        key_idx += 1
        
        url = f"{BASE_URL}/fixtures?sportId=12&from={from_str}&to={to_str}&apiKey={api_key}"
        req = urllib.request.Request(url, headers={"User-Agent": "JBE-TopSpin/1.0"})
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                fixtures = json.loads(resp.read())
        except Exception as ex:
            log(f"    [ERR] {ex}")
            current = win_end + timedelta(days=1)
            time.sleep(3)
            continue
        
        if not isinstance(fixtures, list):
            log(f"    Unexpected response: {type(fixtures)}")
            current = win_end + timedelta(days=1)
            continue
        
        stats["api_calls"] += 1
        stats["fixtures_fetched"] += len(fixtures)
        
        if len(fixtures) > max_fixtures_per_call:
            max_fixtures_per_call = len(fixtures)
        
        # Filter valid tournaments
        relevant = [f for f in fixtures if is_valid_tournament(f.get('tournamentName', ''))]
        stats["relevant"] += len(relevant)
        
        log(f"    {len(fixtures)} total → {len(relevant)} relevant")
        
        # Group by date for efficient matching
        by_date = defaultdict(list)
        for f in relevant:
            dt = (f.get('startTime') or '')[:10]
            by_date[dt].append(f)
        
        for dt in sorted(by_date.keys()):
            db_matches = get_db_matches_for_date(db, dt)
            db_by_id = {m['id']: m for m in db_matches}
            
            for fx in by_date[dt]:
                fid = fx.get('fixtureId', '')
                p1, p2 = fx.get('participant1Name', ''), fx.get('participant2Name', '')
                
                # Check if already matched
                already = [m for m in db_matches if m['oddspapi_fixture_id'] == fid]
                if already:
                    stats["already"] += 1
                    continue
                
                # Find best match
                best_mid, best_score = None, 0
                for dm in db_matches:
                    if dm['oddspapi_fixture_id'] and dm['oddspapi_fixture_id'] != fid:
                        continue
                    score = match_players(p1, p2, dm['p1_name'], dm['p2_name'])
                    if score > best_score:
                        best_score = score
                        best_mid = dm['id']
                
                if best_mid and best_score >= MIN_MATCH_SCORE:
                    if not dry_run:
                        save_fixture_id(db, best_mid, fid)
                    stats["matched"] += 1
                    if stats["matched"] % 20 == 0:
                        log(f"    ... {stats['matched']} matched so far")
        
        current = win_end + timedelta(days=1)
        time.sleep(2)  # rate limit between windows
    
    db.close()
    
    log(f"\n  Fase 1 completa:")
    log(f"    API calls:        {stats['api_calls']}")
    log(f"    Fixtures totali:  {stats['fixtures_fetched']}")
    log(f"    Rilevanti:        {stats['relevant']}")
    log(f"    Matched nuovi:    {stats['matched']}")
    log(f"    Già matched:      {stats['already']}")
    
    return stats


# --- Phase 2: Fetch historical odds ---

def extract_closing_odds(hist_data: dict, bookmaker: str) -> Optional[dict]:
    """Extract closing odds from historical-odds for the moneyline market."""
    books = hist_data.get('bookmakers', {})
    bk = books.get(bookmaker, {})
    ml = bk.get('markets', {}).get(MARKET_MONEYLINE, {})
    outcomes = ml.get('outcomes', {})
    
    result = {}
    
    for out_id in ['121', '122']:
        players = outcomes.get(out_id, {}).get('players', {}).get('0', [])
        if not isinstance(players, list) or not players:
            continue
        
        # Last active=false snapshot = closing price
        closing = None
        opening = None
        n_snapshots = len(players)
        
        for snap in reversed(players):
            price = snap.get('price')
            active = snap.get('active', True)
            if price:
                if not active and closing is None:
                    closing = price
                if active and opening is None:
                    opening = price
        
        # Fallback: if all active, use last snapshot
        if closing is None and players:
            closing = players[-1].get('price')
        if opening is None and players:
            opening = players[0].get('price')
        
        key = 'winner' if out_id == '121' else 'loser'
        result[f'{key}_closing'] = closing
        result[f'{key}_opening'] = opening
        result['n_snapshots'] = n_snapshots
    
    return result


def phase2_fetch_odds(start_date: str, end_date: str, dry_run: bool = False) -> dict:
    """Fetch historical odds for all matched matches."""
    db = get_db()
    stats = {"need_odds": 0, "have_odds": 0, "saved": 0, "not_found": 0, "api_calls": 0}
    
    # Get matches with fixture ID but without tennis_odds
    cur = db.execute("""
        SELECT tm.id, tm.oddspapi_fixture_id, tm.match_date,
               p1.name as p1_name, p2.name as p2_name, tm.tournament
        FROM tennis_matches tm
        JOIN players p1 ON tm.winner_id = p1.id
        JOIN players p2 ON tm.loser_id = p2.id
        WHERE tm.oddspapi_fixture_id IS NOT NULL AND tm.oddspapi_fixture_id != ''
          AND tm.match_date >= ? AND tm.match_date <= ?
          AND NOT EXISTS (
              SELECT 1 FROM tennis_odds to2 WHERE to2.match_id = tm.id
          )
        ORDER BY tm.match_date
    """, (start_date, end_date))
    
    need_odds = [dict(r) for r in cur.fetchall()]
    
    cur = db.execute("""
        SELECT COUNT(DISTINCT tm.id) FROM tennis_matches tm
        JOIN tennis_odds to2 ON tm.id = to2.match_id
        WHERE tm.match_date >= ? AND tm.match_date <= ?
    """, (start_date, end_date))
    stats["have_odds"] = cur.fetchone()[0]
    stats["need_odds"] = len(need_odds)
    
    log(f"\n  Fase 2: scaricamento odds storici")
    log(f"    Già con odds: {stats['have_odds']}")
    log(f"    Da scaricare: {stats['need_odds']}")
    
    if stats["need_odds"] == 0:
        db.close()
        return stats
    
    if dry_run:
        log(f"    [DRY RUN] Non scarico.")
        db.close()
        return stats
    
    est_minutes = len(need_odds) * 12 // 60  # 12s per match (5s bet365 + 5s pinnacle + 2s overhead)
    log(f"    Tempo stimato: ~{est_minutes} minuti")
    log()
    
    for i, m in enumerate(need_odds):
        pct = (i + 1) / len(need_odds) * 100
        log(f"[{i+1}/{len(need_odds)}] ({pct:.0f}%) {m['match_date']} | {m['p1_name']:25} vs {m['p2_name']:25} | {m['tournament'][:30]}")
        
        saved_bk = []
        
        for bk in ['bet365', 'pinnacle']:
            result = odds_api_request("historical-odds", {
                "fixtureId": m['oddspapi_fixture_id'],
                "bookmakers": bk,
            }, timeout=30)
            
            if result is None:
                continue
            
            odds = extract_closing_odds(result, bk)
            if not odds or (odds.get('winner_closing') is None and odds.get('loser_closing') is None):
                continue
            
            bk_name = bk.capitalize()
            db.execute("""
                INSERT INTO tennis_odds
                (match_id, bookmaker, odds_winner, odds_loser,
                 handicap_line, handicap_odds_fav, handicap_odds_dog,
                 total_line, over_odds, under_odds, timestamp)
                VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, datetime('now'))
            """, (m['id'], bk_name, odds.get('winner_closing'), odds.get('loser_closing')))
            db.commit()
            saved_bk.append(bk_name)
            stats["saved"] += 1
            
            log(f"      ✓ {bk_name}: {odds.get('winner_closing')}/{odds.get('loser_closing')} ({odds.get('n_snapshots',0)} snapshots)")
            time.sleep(6)  # 5s cooldown + 1s buffer
            stats["api_calls"] += 1
        
        if not saved_bk:
            stats["not_found"] += 1
            log(f"      - Nessun odds disponibile")
    
    db.close()
    
    log(f"\n  Fase 2 completa:")
    log(f"    Odds salvate:     {stats['saved']}")
    log(f"    Non trovate:      {stats['not_found']}")
    log(f"    API calls:        {stats['api_calls']}")
    
    return stats


# --- Status report ---

def print_status(start_date: str, end_date: str):
    db = get_db()
    
    log("╔══════════════════════════════════════╗")
    log("║      REPORT COPERTURA ODDS          ║")
    log("╚══════════════════════════════════════╝")
    
    # Count ATP matches in range
    cur = db.execute("""
        SELECT COUNT(*) FROM tennis_matches
        WHERE match_date >= ? AND match_date <= ?
          AND tour_level IN ({})
    """.format(','.join('?' * len(TOUR_LEVELS_ATP))),
        [start_date, end_date] + list(TOUR_LEVELS_ATP))
    total = cur.fetchone()[0]
    
    # With fixture ID
    cur = db.execute("""
        SELECT COUNT(*) FROM tennis_matches
        WHERE match_date >= ? AND match_date <= ?
          AND tour_level IN ({})
          AND oddspapi_fixture_id IS NOT NULL AND oddspapi_fixture_id != ''
    """.format(','.join('?' * len(TOUR_LEVELS_ATP))),
        [start_date, end_date] + list(TOUR_LEVELS_ATP))
    with_fid = cur.fetchone()[0]
    
    # With odds
    cur = db.execute("""
        SELECT COUNT(DISTINCT tm.id) FROM tennis_matches tm
        JOIN tennis_odds to2 ON tm.id = to2.match_id
        WHERE tm.match_date >= ? AND tm.match_date <= ?
          AND tm.tour_level IN ({})
    """.format(','.join('?' * len(TOUR_LEVELS_ATP))),
        [start_date, end_date] + list(TOUR_LEVELS_ATP))
    with_odds = cur.fetchone()[0]
    
    log(f"\nPeriodo: {start_date} → {end_date}")
    log(f"  Match ATP totali:        {total}")
    log(f"  Con fixtureId:           {with_fid} ({with_fid/total*100:.1f}%)")
    log(f"  Con odds storiche:       {with_odds} ({with_odds/total*100:.1f}%)")
    
    # By bookmaker
    cur = db.execute("""
        SELECT to2.bookmaker, COUNT(*) as cnt
        FROM tennis_odds to2
        JOIN tennis_matches tm ON to2.match_id = tm.id
        WHERE tm.match_date >= ? AND tm.match_date <= ?
        GROUP BY to2.bookmaker
    """, (start_date, end_date))
    log(f"\n  Per bookmaker:")
    for r in cur.fetchall():
        log(f"    {r[0]}: {r[1]}")
    
    # Total tennis_odds
    cur = db.execute("SELECT COUNT(*) FROM tennis_odds")
    log(f"\n  Totale tennis_odds: {cur.fetchone()[0]}")
    
    db.close()


# --- Main ---

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch historical odds from OddsPapi")
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--phase", type=int, choices=[1, 2], help="Run only phase 1 (matching) or 2 (odds)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true", help="Report copertura")
    args = parser.parse_args()
    
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    
    if args.status:
        print_status(args.start_date, args.end_date)
        return 0
    
    log(f"JBE TopSpin — Historical Odds Fetcher")
    log(f"Periodo: {args.start_date} → {args.end_date}")
    log(f"Dry run: {args.dry_run}")
    log()
    
    if args.phase != 2:
        log("╔══ FASE 1: Fixture matching ══╗")
        p1 = phase1_fetch_matches(args.start_date, args.end_date, args.dry_run)
        log()
    
    if args.phase != 1:
        log("╔══ FASE 2: Historical odds ══╗")
        p2 = phase2_fetch_odds(args.start_date, args.end_date, args.dry_run)
        log()
    
    # Final report
    print_status(args.start_date, args.end_date)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
