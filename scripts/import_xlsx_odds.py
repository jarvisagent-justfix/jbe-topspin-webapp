#!/usr/bin/env python3
"""
Importa quote tennis da tennis-data.co.uk (XLSX) nel DB.
Usa matching SQL LIKE per i nomi (robusto per cognomi composti).
"""
import sys, os, json, zipfile, re, io, urllib.request
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET
from collections import defaultdict

sys.path.insert(0, "/opt/data/jbe-tennis/src")
from database import TennisDatabase
from config import DB_PATH

YEAR_START = 2022
YEAR_END = 2026
BASE_URL = "http://www.tennis-data.co.uk"

def load_xlsx(url: str) -> list:
    """Scarica XLSX, ritorna lista di dict."""
    print(f"  Downloading {url}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"  [ERROR] Download: {e}")
        return []
    
    print(f"  Downloaded {len(raw)} bytes")
    
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        ss_xml = z.read('xl/sharedStrings.xml')
        ss_tree = ET.fromstring(ss_xml)
        shared_strings = [si.find('s:t', ns).text or '' for si in ss_tree.findall('.//s:si', ns)]
        
        sheet_xml = z.read('xl/worksheets/sheet1.xml')
        sheet_tree = ET.fromstring(sheet_xml)
        
        # Parse ALL rows: each row becomes {col_letter: value}
        all_row_cells = []
        for row_elem in sheet_tree.findall('.//s:row', ns):
            cells = {}
            for cell in row_elem.findall('s:c', ns):
                cell_ref = cell.get('r', '')
                col_letter = ''.join(c for c in cell_ref if c.isalpha())
                v = cell.find('s:v', ns)
                val = v.text if v is not None else ''
                if cell.get('t') == 's' and val:
                    idx = int(float(val))
                    val = shared_strings[idx] if idx < len(shared_strings) else val
                cells[col_letter] = val
            all_row_cells.append(cells)
    
        if not all_row_cells:
            return []
    
        # First row = headers. Build col_letter -> header_name mapping
        header_row = all_row_cells[0]
        header_map = {}
        for col_letter in sorted(header_row.keys(), key=lambda c: (len(c), c)):
            header_map[col_letter] = header_row[col_letter]
    
        # Return as [header_map, row_cells...]
        return [header_map] + all_row_cells[1:]


def parse_date(val):
    """Converte data Excel serial o stringa in stringa ISO."""
    try:
        days = int(float(val))
        return (date(1899, 12, 30) + timedelta(days=days)).isoformat()
    except: pass
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]:
        try: return datetime.strptime(val.strip(), fmt).date().isoformat()
        except: pass
    return None


def guess_surname_initial(xlsx_name):
    """
    XLSX: "Ugo Carabelli C." -> surname="Ugo Carabelli", initial="C"
    XLSX: "Tiafoe F." -> surname="Tiafoe", initial="F"
    Returns (surname_parts, initial) where surname_parts is list of words.
    """
    name = xlsx_name.strip()
    if not name or '.' not in name:
        return [name.lower()], ''
    
    # Last token is the initial (e.g., "F." or "F")
    tokens = name.split()
    last = tokens[-1]
    initial = last.replace('.', '').strip()
    
    # Everything before last token is the surname
    surname = ' '.join(tokens[:-1])
    surname_parts = surname.lower().split()
    
    return surname_parts, initial


def import_year(year: int, db):
    """Importa quote per un anno."""
    url = f"{BASE_URL}/{year}/{year}.xlsx"
    xlsx_data = load_xlsx(url)
    
    # Return format: [header_map, rows_data]
    # xlsx_data is now a list where first element is header_map, rest are row cells
    if not xlsx_data or len(xlsx_data) < 2:
        return 0
    
    header_map = xlsx_data[0]  # {col_letter: header_name}
    raw_rows = xlsx_data[1:]   # list of {col_letter: value}
    
    print(f"  {len(raw_rows)} XLSX entries")
    
    imported = 0
    unmatched = 0
    swapped = 0
    skipped = 0
    
    for cells in raw_rows:
        # Build match dict from cells using header_map
        xm = {}
        for col_letter, header_name in header_map.items():
            xm[header_name] = cells.get(col_letter, "")
        try:
            match_date = parse_date(xm.get("Date", ""))
            if not match_date:
                continue
            
            winner_xlsx = xm.get("Winner", "").strip()
            loser_xlsx = xm.get("Loser", "").strip()
            surface = xm.get("Surface", "").strip()
            comment = xm.get("Comment", "").strip()
            
            # Skip non-completed
            if comment and comment.lower() not in ("completed", "", "2-0", "2-1", "3-0", "3-1", "3-2"):
                skipped += 1
                continue
            if not winner_xlsx or not loser_xlsx:
                continue
            
            # Guess surname and initial
            w_parts, w_init = guess_surname_initial(winner_xlsx)
            l_parts, l_init = guess_surname_initial(loser_xlsx)
            
            if not w_parts or not l_parts:
                continue
            
            # Build LIKE patterns for SQL matching
            # Match where: date + surface match AND 
            # winner surname appears in winner name AND initial matches
            # loser surname appears in loser name AND initial matches
            
            w_surname_like = '%' + w_parts[-1] + '%'  # Last word of surname
            l_surname_like = '%' + l_parts[-1] + '%'
            
            # Try direct
            match = db.conn.execute("""
                SELECT m.id, w.name as wn, l.name as ln
                FROM tennis_matches m
                JOIN players w ON w.id=m.winner_id
                JOIN players l ON l.id=m.loser_id
                WHERE m.match_date = ? AND m.surface = ?
                  AND LOWER(w.name) LIKE ? AND w.name LIKE ?
                  AND LOWER(l.name) LIKE ? AND l.name LIKE ?
                LIMIT 1
            """, (match_date, surface, 
                  w_surname_like, f'{w_init}%',
                  l_surname_like, f'{l_init}%')).fetchone()
            
            is_swapped = False
            if not match:
                # Try swapped (winner/loser reversed)
                match = db.conn.execute("""
                    SELECT m.id, w.name as wn, l.name as ln
                    FROM tennis_matches m
                    JOIN players w ON w.id=m.winner_id
                    JOIN players l ON l.id=m.loser_id
                    WHERE m.match_date = ? AND m.surface = ?
                      AND LOWER(w.name) LIKE ? AND w.name LIKE ?
                      AND LOWER(l.name) LIKE ? AND l.name LIKE ?
                    LIMIT 1
                """, (match_date, surface,
                      l_surname_like, f'{l_init}%',
                      w_surname_like, f'{w_init}%')).fetchone()
                is_swapped = True
            
            if not match:
                unmatched += 1
                continue
            
            if is_swapped:
                swapped += 1
                b365w = xm.get("B365L", "")
                b365l = xm.get("B365W", "")
                psw = xm.get("PSL", "")
                psl = xm.get("PSW", "")
            else:
                b365w = xm.get("B365W", "")
                b365l = xm.get("B365L", "")
                psw = xm.get("PSW", "")
                psl = xm.get("PSL", "")
            
            # Insert Bet365 odds
            if b365w and float(b365w) > 0:
                db.conn.execute("""
                    INSERT OR REPLACE INTO tennis_odds 
                    (match_id, bookmaker, odds_winner, odds_loser)
                    VALUES (?, 'Bet365', ?, ?)
                """, (match["id"], float(b365w), 
                      float(b365l) if b365l and float(b365l) > 0 else None))
            
            # Insert Pinnacle odds
            if psw and float(psw) > 0:
                db.conn.execute("""
                    INSERT OR REPLACE INTO tennis_odds 
                    (match_id, bookmaker, odds_winner, odds_loser)
                    VALUES (?, 'Pinnacle', ?, ?)
                """, (match["id"], float(psw),
                      float(psl) if psl and float(psl) > 0 else None))
            
            imported += 1
            
        except Exception as e:
            pass
    
    db.conn.commit()
    print(f"  Imported: {imported}, Unmatched: {unmatched}, Swapped: {swapped}, Skipped: {skipped}")
    return imported


# Main
print(f"=== Import quote tennis-data.co.uk {YEAR_START}-{YEAR_END} ===")
print(f"Fonte: {BASE_URL}")

db = TennisDatabase(DB_PATH)

before = db.conn.execute("SELECT COUNT(*) FROM tennis_odds").fetchone()[0]
print(f"\nQuote nel DB prima: {before}")

total = 0
for year in range(YEAR_START, YEAR_END + 1):
    print(f"\n--- Anno {year} ---")
    total += import_year(year, db)

after = db.conn.execute("SELECT COUNT(*) FROM tennis_odds").fetchone()[0]
print(f"\n=== RIEPILOGO ===")
print(f"Quote totali: {after} (importate: {total})")

for year in range(YEAR_START, YEAR_END + 1):
    r = db.conn.execute("""
        SELECT COUNT(DISTINCT match_id) as m, COUNT(*) as o
        FROM tennis_odds o JOIN tennis_matches m ON o.match_id = m.id
        WHERE m.match_date >= ? AND m.match_date < ?
    """, (f"{year}-01-01", f"{year+1}-01-01")).fetchone()
    print(f"  {year}: {r[0]} match, {r[1]} odds")

r = db.conn.execute("SELECT bookmaker, COUNT(*) FROM tennis_odds GROUP BY bookmaker")
for r2 in r.fetchall():
    print(f"  Bookmaker {r2[0]}: {r2[1]}")

# 2026 coverage
total_26 = db.conn.execute("""
    SELECT COUNT(*) FROM tennis_matches 
    WHERE match_date >= '2026-01-01' AND match_date < '2027-01-01' AND w_sets > 0
""").fetchone()[0]
with_26 = db.conn.execute("""
    SELECT COUNT(DISTINCT m.id) FROM tennis_matches m
    JOIN tennis_odds o ON o.match_id = m.id
    WHERE m.match_date >= '2026-01-01' AND m.match_date < '2027-01-01'
      AND m.w_sets > 0
""").fetchone()[0]
print(f"\nCopertura 2026: {with_26}/{total_26} ({with_26/max(total_26,1)*100:.0f}%)")

db.close()
