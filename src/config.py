"""
JBE TopSpin — Configurazione
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "tennis.db")

# --- ELO Settings ---
ELO_DEFAULT_RATING = 1500.0
ELO_K_FACTOR = 16           # Ridotto da 32 — meno polarizzazione su 73k match
ELO_K_INJURY_MULTIPLIER = 0.1
ELO_DECAY_DAYS = 270        # Decay dopo 270 giorni (era 365)
ELO_BO5_FACTOR = 0.05       # +5% al favorito in Bo5
ELO_SURFACE_MIN_CONF = 50   # Match necessari per confidenza piena

# --- Serve/Return Markov ---
SERVE_INIT_HARD = 0.63
SERVE_INIT_CLAY = 0.60
SERVE_INIT_GRASS = 0.64
SERVE_INIT_CARPET = 0.62
SERVE_ROLLING_ALPHA = 0.05  # Peso nuovi dati nell'update rolling

# --- XGBoost ---
XGB_LEARNING_RATE = 0.05
XGB_MAX_DEPTH = 6
XGB_N_ESTIMATORS = 300
XGB_EARLY_STOPPING = 30
XGB_RETRAIN_EVERY = 100     # Retrain ogni 100 prediction errors

# --- Value Detection ---
MIN_EDGE = 0.05             # Edge minimo 5%
CONSENSUS_THRESHOLD = 0.10  # Se quota > 10% da media, edge dimezzato
MIN_CONFIDENCE = 0.50       # Confidenza minima modello
MIN_PLAYER_MATCHES = 10     # Match minimi per giocatore

# --- Kelly ---
KELLY_FRACTION = 0.125      # 12.5%
MAX_STAKE_PCT = 0.05        # Max 5% del bankroll per scommessa
MAX_DAILY_EXPOSURE_PCT = 0.15  # Max 15% al giorno
MAX_TOURNAMENT_EXPOSURE_PCT = 0.10  # Max 10% per torneo
STOP_LOSS_CONSECUTIVE = 3   # Stop 24h dopo 3 perdite consecutive
DRAWDOWN_STOP = 0.25        # Stop loss al -25% dal picco

# --- Surfaces ---
SURFACES = ["Hard", "Clay", "Grass", "Carpet"]

# --- Tournament Tiers ---
TIER_GRAND_SLAM = 3
TIER_MASTERS = 2
TIER_ATP500 = 1
TIER_ATP250 = 0

# --- Paths ---
IMPORT_DIR = os.path.join(BASE_DIR, "data", "import")
MODEL_DIR = os.path.join(BASE_DIR, "data", "models")
