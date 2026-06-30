#!/usr/bin/env python3
"""Add match_datetime column to paper_portfolio + restore Odds API logging with Italian time."""
import sys, os, json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from database import TennisDatabase
from config import DB_PATH

ITALY_TZ = timezone(timedelta(hours=2))  # CEST (UTC+2)

def to_italian_time(iso_str):
    """Converte ISO datetime in stringa italiana."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        italian = dt.astimezone(ITALY_TZ)
        return italian.strftime("%d/%m/%Y %H:%M")
    except:
        return None

db = TennisDatabase(DB_PATH)

# Step 1: Add match_datetime column if not exists
try:
    db.conn.execute("ALTER TABLE paper_portfolio ADD COLUMN match_datetime TEXT")
    print("[OK] Added match_datetime column")
except Exception as e:
    print(f"[INFO] Column already exists: {e}")

# Step 2: Update existing records with time from tennis_matches created_at if available
# (tennis_matches has no time, so we leave existing records as-is)

# Step 3: Also add bookmaker_url and odds_source columns for reference
try:
    db.conn.execute("ALTER TABLE paper_portfolio ADD COLUMN odds_source TEXT")
    print("[OK] Added odds_source column")
except:
    pass

db.conn.commit()

# Step 4: Test the Italian time conversion
print("\n=== Italian Time Test ===")
test_times = ["2026-06-25T10:05:00Z", "2026-06-25T12:00:00Z", "2026-06-25T13:30:00Z"]
for t in test_times:
    it = to_italian_time(t)
    utc_raw = t.replace("Z", "+00:00")[:16]
    print(f"  UTC: {utc_raw} -> Italia (CEST): {it}")

# Step 5: Show current portfolio status
count = db.conn.execute("SELECT COUNT(*) FROM paper_portfolio").fetchone()[0]
print(f"\nPortfolio records: {count}")

db.close()
print("\nDone. Now restoring Odds API portfolio logging with Italian time.")
