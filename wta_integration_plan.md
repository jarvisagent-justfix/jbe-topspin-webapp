# WTA Integration Plan for JBE TopSpin

## 1. Executive Summary

Add WTA (women's) tennis to JBE TopSpin, currently ATP-only. WTA has distinct match dynamics (always best-of-3, different ELO distributions, separate rankings) that require modeling as a separate domain while reusing the existing pipeline infrastructure.

**Status:** No WTA data exists in the DB today. All 76,066 matches are ATP (source: TML).

---

## 2. Data Sources — Availability Verified

### 2.1 Tennis-Data.co.uk — WTA Historical Data ✅

| Property | Value |
|----------|-------|
| **URL pattern** | `http://www.tennis-data.co.uk/{year}w/{year}.xlsx` |
| **Example** | `http://www.tennis-data.co.uk/2025w/2025.xlsx` ✅ works |
| **Years covered** | 2007–2026 |
| **Columns** | Same as ATP (Winner, Loser, WRank, LRank, B365W, B365L, PSW, PSL, etc.) |
| **Differences** | Uses `Tier` column instead of ATP's `Series`; has `ATP` (men's #) and `WTA` (women's #) columns |
| **CSV format** | Available via tournament-specific links (not bulk year CSVs for WTA) |
| **Import method** | XLSX parsing (already exists for ATP in `daily_report.py` / `import_odds_xlsx.py`) |

**Conclusion:** WTA historical data is available from 2007–2026 in the same format as ATP, just at a different URL path.

### 2.2 The Odds API — WTA Live/Upcoming Matches ✅

| Property | Value |
|----------|-------|
| **Sport key** | `tennis_wta` ✅ (confirmed active) |
| **Also available** | Tournament-specific: `tennis_wta_cincinnati_open`, `tennis_wta_dubai`, `tennis_wta_french_open`, etc. |
| **Current code** | Already auto-discovers `tennis_wta` via `get_active_tennis_sports()` (line 119-130, `odds_api.py`) |
| **Fallback list** | Already includes `["tennis_atp", "tennis_wta", "tennis"]` (line 152) |
| **Gap** | Player matching (`match_players_to_db`) only searches the DB, which currently has only ATP players |

**Conclusion:** The Odds API already returns WTA matches. The code already queries them. The only blocker is the DB has no WTA players to match against.

### 2.3 TML (TennisMyLife) — Current Import Source

Current DB uses TML data (`stats.tennismylife.org`). The TML dataset format covers both ATP and WTA in its CSV schema (has `tourney_level`, surface, player info). Need to verify if the existing `tml_all.zip` contains WTA data. If not, a separate WTA TML import would be needed.

---

## 3. Current Schema Analysis

### 3.1 `players` table

| Column | WTA Issue |
|--------|-----------|
| `atp_id` TEXT UNIQUE | ATP-specific naming. WTA players have separate IDs. **Need `wta_id` or make generic.** |
| No gender column | Cannot distinguish ATP from WTA players. Players with the same name could exist in both tours. |
| No tour column | No way to filter queries by ATP/WTA. |

### 3.2 `tennis_matches` table

| Column | WTA Issue |
|--------|-----------|
| No `tour` column | Cannot distinguish ATP vs WTA matches. |
| `tour_level` CHECK `('G','M','A','C','F')` | WTA uses WTA1000, WTA500, WTA250 — maps to same levels conceptually but needs tour context. |
| `best_of` | WTA is always best-of-3. Predictable, but current model treats bo5 as a feature. |
| `winner_rank` / `loser_rank` | ATP rankings (0-~2000). WTA rankings are 0-~2000 too, but NOT comparable. |
| `source` | Currently "TML" only. Would add "TML-WTA" or "tennis-data-wta". |

### 3.3 `tennis_odds` table

No changes needed — bookmaker odds are format-agnostic.

### 3.4 `elo_ratings`, `serve_return_params`, `prediction_errors`

No schema changes needed for these tables, but query logic and model training needs tour-awareness.

---

## 4. Minimum Changes Required

### 4.1 Database Schema Changes

**Option A: Minimal (recommended) — Add gender/tour column**

```sql
-- players table: add tour and wta_id
ALTER TABLE players ADD COLUMN tour TEXT CHECK(tour IN ('ATP','WTA')) DEFAULT 'ATP';
ALTER TABLE players ADD COLUMN wta_id TEXT UNIQUE;

-- tennis_matches table: add tour
ALTER TABLE tennis_matches ADD COLUMN tour TEXT CHECK(tour IN ('ATP','WTA')) DEFAULT 'ATP';

-- Create index for tour filtering
CREATE INDEX IF NOT EXISTS idx_match_tour ON tennis_matches(tour);
CREATE INDEX IF NOT EXISTS idx_players_tour ON players(tour);
```

**Rationale:** Tour is a fundamental distinguishing attribute. Adding it as a column (with default 'ATP' for backward compatibility) is cleaner than relying on source strings or tournament names. It also enables simple filtering in all queries.

**Option B: Separate tables** — More work, cleaner separation but overengineered for this use case.

### 4.2 Import Changes

#### New script: `import_tennis_data_wta.py` (based on existing `import_tennis_data.py`)

- WTA XLSX URLs: `http://www.tennis-data.co.uk/{year}w/{year}.xlsx`
- Map `Tier` column instead of `Series` column
- Set `tour='WTA'` for all imported matches
- Import WTA players with `tour='WTA'` (separate namespace from ATP)
- Same odds import logic (B365W, B365L, PSW, PSL)

#### Update `daily_report.py`

- Add WTA XLSX download/import step for the current year
- Add WTA-specific player matching in XLSX import (surname matching works same way)

#### Update `odds_api.py`

- Add `tour='WTA'` flag when inserting match/player data from API
- The API returns player names — currently matched only against ATP DB. Add fallback for WTA players.

### 4.3 Model Training — Separate Models (Recommended)

**Why separate models:**
1. **Different ELO dynamics** — WTA has higher variance, different serve hold rates, different favorite/underdog dynamics
2. **Best-of-3 always** — No bo5 complexity, simpler Markov model
3. **Different ranking ranges** — ATP and WTA rankings are not directly comparable (different points systems)
4. **Feature distributions differ** — Combined training would learn tour-specific patterns anyway

**Changes needed:**

#### ELO (`elo_tennis.py`)
- Add optional `tour` parameter to `SurfaceELOEngine`
- Maintain separate ELO rating pools for ATP and WTA
- When querying by tour, use the correct pool
- Shared code structure — just separate state

#### XGBoost (`xgboost_tennis.py`)
- `TopSpinEngine` and `XGBoostTrainer` can accept `tour` parameter
- Separate model files: `topspin_winner_wta.json`, `topspin_games_wta.json`
- Separate feature extraction — same features, but trained only on WTA data
- Tour-level can be added as a feature (one-hot: ATP, WTA) for unified model approach

#### Markov (`markov_tennis.py`)
- No changes needed — Markov model is parameter-based (serve/return probabilities) and works per player+surface regardless of tour
- WTA serve/return defaults will differ (lower serve %, higher return %)

### 4.4 Training Pipeline Changes

#### `train_topspin.py`
- Add separate walk-forward training for WTA
- Train window: 2016–2021 (WTA), etc. — WTA has fewer years (2007+) but enough data
- Save models with `_wta` prefix

### 4.5 Reporting Changes

#### `daily_report.py`
- After ATP analysis, run WTA analysis with WTA models
- Generate WTA-specific report section or separate report
- Add tour label to value bet output

#### `odds_api.py`
- Generate WTA value bet report when WTA matches are found
- Show tour (ATP/WTA) in report header

---

## 5. Implementation Order

### Phase 1: Data Import (Estimated: 2-4 hours)
1. Add `tour` column to `players` and `tennis_matches` tables
2. Create `import_tennis_data_wta.py` script
3. Import WTA historical data (2007–2025) from tennis-data.co.uk XLSX
4. Update `daily_report.py` to import current-year WTA XLSX
5. Verify: run queries showing WTA match counts, player counts, surface distribution

### Phase 2: Odds API Integration (Estimated: 1-2 hours)
6. Update `odds_api.py` to handle WTA player matching (create new WTA players on the fly)
7. Add `tour` field to match records in value bet pipeline
8. Test with `--test` flag to verify WTA matches appear

### Phase 3: Model Training (Estimated: 2-3 hours)
9. Update ELO engine to support separate tour pools (or separate engine instances)
10. Train WTA ELO ratings from historical data
11. Train WTA XGBoost models (walk-forward + final)
12. Train WTA Markov serve/return params
13. Validate: compare ATP vs WTA accuracy metrics

### Phase 4: Reporting & Go-Live (Estimated: 1-2 hours)
14. Update daily report to include WTA analysis
15. Add tour indicator to all value bet outputs
16. Test full pipeline end-to-end
17. Deploy cron job updates

**Total estimated effort: 6-11 hours**

---

## 6. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| WTA tennis-data.co.uk XLSX format differs | Low | Medium | Parse first 100 rows, compare columns before bulk import |
| WTA player name collisions with ATP | Medium | Low | Add `tour` column to player table, names unique per tour |
| WTA model accuracy lower than ATP | Medium | Medium | More conservative value bet thresholds (MIN_EDGE 7% vs 5%) |
| The Odds API rate limit (500 req/mo) | High | Medium | WTA and ATP share same limit. Already using 2 API keys. May need 3rd. |
| TML dataset already has WTA (unused) | Medium | High | Check `tml_all.zip` contents — could save re-importing from tennis-data.co.uk |
| WTA has fewer historical matches (~20K vs 76K ATP) | Medium | Low | Can still train reasonable models with 5+ years of data |

---

## 7. Quick Win: Check TML for Existing WTA Data

Before implementing Phase 1, check if the existing `tml_all.zip` contains WTA data:

```bash
cd /opt/data/jbe-tennis/data/import
unzip -l tml_all.zip | head -50  # Check if CSV filenames suggest WTA
# Also check: are there women-specific tournament names in the existing DB?
sqlite3 ../tennis.db "SELECT DISTINCT tournament FROM tennis_matches 
  WHERE tournament LIKE '%WTA%' OR tournament LIKE '%Women%' LIMIT 20"
```

If TML already includes WTA, the import could be as simple as re-running `import_tml.py` with WTA data enabled, saving significant effort.

---

## 8. Key Files to Modify

| File | Change |
|------|--------|
| `src/database.py` | Add `tour` columns to schema, update `get_or_create_player` |
| `scripts/import_tennis_data.py` | (reference) Create WTA version |
| `scripts/import_tennis_data_wta.py` | **NEW** — WTA XLSX import |
| `scripts/daily_report.py` | Add WTA import step and WTA analysis section |
| `scripts/odds_api.py` | Handle WTA player creation, add tour to reports |
| `src/engine/elo_tennis.py` | Add optional tour parameter, separate rating pools |
| `src/engine/xgboost_tennis.py` | Support tour in training, separate model files |
| `scripts/train_topspin.py` | Add WTA walk-forward training |
| `src/config.py` | Add WTA-specific model paths and defaults |

---

## 9. Appendix: Verified WTA URL Pattern

```
ATP:    http://www.tennis-data.co.uk/2025/2025.xlsx   ✅ (confirmed working)
WTA:    http://www.tennis-data.co.uk/2025w/2025.xlsx   ✅ (confirmed working)
```

Directory structure: `/{year}w/{year}.xlsx` for women's data.

The ATP URLs used by current code are HTTPS (`https://tennis-data.co.uk/...`). Both HTTP and HTTPS should work with the WTA pattern.
