"""
JBE TopSpin — Database Module
Gestisce connessione, schema e query per il database tennis.
"""
import sqlite3
import os
from config import DB_PATH


class TennisDatabase:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.conn = sqlite3.connect(self.db_path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        """Crea le tabelle se non esistono."""
        schema = """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            atp_id TEXT UNIQUE,
            name TEXT NOT NULL,
            country TEXT,
            hand TEXT CHECK(hand IN ('R','L','A')),
            height_cm INTEGER,
            turned_pro INTEGER,
            birth_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_players_atp ON players(atp_id);
        CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);

        CREATE TABLE IF NOT EXISTS tennis_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date DATE NOT NULL,
            tournament TEXT NOT NULL,
            tour_level TEXT CHECK(tour_level IN ('G','M','A','C','F')),
            surface TEXT CHECK(surface IN ('Hard','Clay','Grass','Carpet')),
            indoor BOOLEAN DEFAULT 0,
            round TEXT,
            best_of INTEGER DEFAULT 3,
            winner_id INTEGER REFERENCES players(id),
            loser_id INTEGER REFERENCES players(id),
            winner_seed INTEGER,
            loser_seed INTEGER,
            winner_rank INTEGER,
            loser_rank INTEGER,
            winner_rank_points INTEGER,
            loser_rank_points INTEGER,
            w_sets INTEGER,
            l_sets INTEGER,
            w_games INTEGER,
            l_games INTEGER,
            score TEXT,
            w_ace INTEGER, w_df INTEGER, w_1st_in INTEGER,
            w_1st_won INTEGER, w_2nd_won INTEGER, w_svpt INTEGER,
            l_ace INTEGER, l_df INTEGER, l_1st_in INTEGER,
            l_1st_won INTEGER, l_2nd_won INTEGER, l_svpt INTEGER,
            retirement BOOLEAN DEFAULT 0,
            walkover BOOLEAN DEFAULT 0,
            retired_player_id INTEGER REFERENCES players(id),
            comment TEXT,
            source TEXT DEFAULT 'tennis-data.co.uk',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_match_date ON tennis_matches(match_date);
        CREATE INDEX IF NOT EXISTS idx_match_winner ON tennis_matches(winner_id);
        CREATE INDEX IF NOT EXISTS idx_match_loser ON tennis_matches(loser_id);
        CREATE INDEX IF NOT EXISTS idx_match_surface ON tennis_matches(surface);
        CREATE INDEX IF NOT EXISTS idx_match_tournament ON tennis_matches(tournament, match_date);

        CREATE TABLE IF NOT EXISTS tennis_odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER REFERENCES tennis_matches(id),
            bookmaker TEXT NOT NULL,
            odds_winner REAL,
            odds_loser REAL,
            handicap_line REAL,
            handicap_odds_fav REAL,
            handicap_odds_dog REAL,
            total_line REAL,
            over_odds REAL,
            under_odds REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_odds_match ON tennis_odds(match_id);

        CREATE TABLE IF NOT EXISTS elo_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER REFERENCES players(id),
            match_id INTEGER REFERENCES tennis_matches(id),
            rating_date DATE NOT NULL,
            rating_overall REAL NOT NULL,
            rating_hard REAL NOT NULL,
            rating_clay REAL NOT NULL,
            rating_grass REAL NOT NULL,
            rating_carpet REAL NOT NULL,
            rating_mov REAL NOT NULL,
            matches_played INTEGER DEFAULT 0,
            matches_hard INTEGER DEFAULT 0,
            matches_clay INTEGER DEFAULT 0,
            matches_grass INTEGER DEFAULT 0,
            matches_carpet INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_elo_player ON elo_ratings(player_id);
        CREATE INDEX IF NOT EXISTS idx_elo_date ON elo_ratings(rating_date);

        CREATE TABLE IF NOT EXISTS serve_return_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER REFERENCES players(id),
            surface TEXT CHECK(surface IN ('Hard','Clay','Grass','Carpet')),
            p_serve REAL NOT NULL,
            q_return REAL NOT NULL,
            matches_on_surface INTEGER DEFAULT 0,
            points_serve_won INTEGER DEFAULT 0,
            points_serve_total INTEGER DEFAULT 0,
            confidence REAL DEFAULT 0.0,
            last_updated DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(player_id, surface)
        );

        CREATE INDEX IF NOT EXISTS idx_serve_player ON serve_return_params(player_id);

        CREATE TABLE IF NOT EXISTS prediction_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER REFERENCES tennis_matches(id),
            pred_winner_id INTEGER REFERENCES players(id),
            pred_prob REAL,
            pred_game_total REAL,
            pred_set_spread INTEGER,
            best_odds_winner REAL,
            best_odds_loser REAL,
            edge_winner REAL,
            actual_winner_id INTEGER REFERENCES players(id),
            actual_game_total INTEGER,
            actual_set_spread INTEGER,
            winner_correct BOOLEAN,
            game_total_error INTEGER,
            set_spread_correct BOOLEAN,
            calibration REAL,
            surface TEXT,
            tour_level TEXT,
            round TEXT,
            best_of INTEGER,
            player1_id INTEGER REFERENCES players(id),
            player2_id INTEGER REFERENCES players(id),
            market TEXT DEFAULT 'match_winner',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_errors_winner ON prediction_errors(winner_correct);
        CREATE INDEX IF NOT EXISTS idx_errors_surface ON prediction_errors(surface);
        CREATE INDEX IF NOT EXISTS idx_errors_created ON prediction_errors(created_at);

        CREATE TABLE IF NOT EXISTS bias_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slice_type TEXT NOT NULL,
            slice_value TEXT NOT NULL,
            bias REAL DEFAULT 0.0,
            n_errors INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(slice_type, slice_value)
        );
        """
        self.conn.executescript(schema)
        self.conn.commit()

    def get_or_create_player(self, name, atp_id=None, country=None):
        """Trova un giocatore o lo crea. Ritorna (id, creato)."""
        cur = self.conn.execute(
            "SELECT id FROM players WHERE name = ?", (name,)
        )
        row = cur.fetchone()
        if row:
            return row["id"], False

        cur = self.conn.execute(
            "INSERT INTO players (name, atp_id, country) VALUES (?, ?, ?)",
            (name, atp_id, country),
        )
        return cur.lastrowid, True

    def get_player_id_by_name(self, name):
        cur = self.conn.execute(
            "SELECT id FROM players WHERE name = ?", (name,)
        )
        row = cur.fetchone()
        return row["id"] if row else None

    def insert_match(self, data):
        """Inserisce un match. data e' un dict con tutti i campi."""
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        values = tuple(data.values())

        # Check for duplicates
        cur = self.conn.execute(
            "SELECT id FROM tennis_matches WHERE match_date=? AND winner_id=? AND loser_id=?",
            (data.get("match_date"), data.get("winner_id"), data.get("loser_id")),
        )
        if cur.fetchone():
            return None

        cur = self.conn.execute(
            f"INSERT INTO tennis_matches ({cols}) VALUES ({placeholders})",
            values,
        )
        return cur.lastrowid

    def insert_odds(self, match_id, bookmaker, odds_winner, odds_loser,
                    handicap_line=None, handicap_odds_fav=None, handicap_odds_dog=None,
                    total_line=None, over_odds=None, under_odds=None):
        self.conn.execute(
            """INSERT INTO tennis_odds 
               (match_id, bookmaker, odds_winner, odds_loser,
                handicap_line, handicap_odds_fav, handicap_odds_dog,
                total_line, over_odds, under_odds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (match_id, bookmaker, odds_winner, odds_loser,
             handicap_line, handicap_odds_fav, handicap_odds_dog,
             total_line, over_odds, under_odds),
        )

    def get_latest_elo(self, player_id):
        cur = self.conn.execute(
            "SELECT * FROM elo_ratings WHERE player_id=? ORDER BY rating_date DESC LIMIT 1",
            (player_id,),
        )
        return cur.fetchone()

    def get_serve_return_params(self, player_id, surface):
        cur = self.conn.execute(
            "SELECT * FROM serve_return_params WHERE player_id=? AND surface=?",
            (player_id, surface),
        )
        return cur.fetchone()

    def get_matches_for_date(self, date):
        cur = self.conn.execute(
            """SELECT m.*, w.name AS winner_name, l.name AS loser_name
               FROM tennis_matches m
               JOIN players w ON w.id=m.winner_id
               JOIN players l ON l.id=m.loser_id
               WHERE m.match_date=?
               ORDER BY m.tournament, m.round""",
            (date,),
        )
        return cur.fetchall()

    def get_matches_in_range(self, start_date, end_date, surface=None):
        """Ritorna i match in un range di date. Opzionalmente filtrati per superficie."""
        if surface:
            cur = self.conn.execute(
                """SELECT m.* FROM tennis_matches m 
                   WHERE m.match_date>=? AND m.match_date<? AND m.surface=?
                   ORDER BY m.match_date""",
                (start_date, end_date, surface),
            )
        else:
            cur = self.conn.execute(
                """SELECT m.* FROM tennis_matches m 
                   WHERE m.match_date>=? AND m.match_date<?
                   ORDER BY m.match_date""",
                (start_date, end_date),
            )
        return cur.fetchall()

    def get_prediction_errors_since(self, since_date, slice_type=None, slice_value=None):
        """Ritorna errori di predizione per analisi self-improvement.
        
        Perché whitelist su slice_type:
          slice_type viene usato come nome di colonna nella query SQL.
          Per prevenire SQL injection, solo valori pre-autorizzati sono accettati.
          La whitelist è definita qui (non in self_improvement.py) perché
          è una responsabilità del database layer, non del chiamante.
        """
        # Whitelist: solo colonne pre-autorizzate possono essere usate come slice
        ALLOWED_SLICE_TYPES = {"surface", "tour_level", "round", "best_of", "market"}
        if slice_type and slice_type not in ALLOWED_SLICE_TYPES:
            raise ValueError(f"slice_type '{slice_type}' non autorizzato. Usa: {ALLOWED_SLICE_TYPES}")
        
        if slice_type and slice_value:
            cur = self.conn.execute(
                f"SELECT * FROM prediction_errors WHERE created_at>=? AND {slice_type}=?",
                (since_date, slice_value),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM prediction_errors WHERE created_at>=?",
                (since_date,),
            )
        return cur.fetchall()

    def get_error_count(self):
        cur = self.conn.execute("SELECT COUNT(*) as cnt FROM prediction_errors")
        return cur.fetchone()["cnt"]

    def get_bias_correction(self, slice_type, slice_value):
        cur = self.conn.execute(
            "SELECT bias FROM bias_corrections WHERE slice_type=? AND slice_value=?",
            (slice_type, slice_value),
        )
        row = cur.fetchone()
        return row["bias"] if row else 0.0

    def update_bias_correction(self, slice_type, slice_value, bias, n_errors):
        self.conn.execute(
            """INSERT INTO bias_corrections (slice_type, slice_value, bias, n_errors, last_updated)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(slice_type, slice_value) DO UPDATE SET
               bias=excluded.bias, n_errors=excluded.n_errors, last_updated=CURRENT_TIMESTAMP""",
            (slice_type, slice_value, bias, n_errors),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()

    def commit(self):
        self.conn.commit()
