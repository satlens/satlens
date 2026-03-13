"""Configuration management for satellite-pipeline."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# === Copernicus Data Space ===
COPERNICUS_USERNAME = os.getenv("COPERNICUS_USERNAME", "")
COPERNICUS_PASSWORD = os.getenv("COPERNICUS_PASSWORD", "")
COPERNICUS_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
COPERNICUS_ODATA_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
COPERNICUS_DOWNLOAD_URL = "https://zipper.dataspace.copernicus.eu/odata/v1"

# === AISStream.io ===
AISSTREAM_API_KEY = os.getenv("AISSTREAM_API_KEY", "")

# === NASA Earthdata ===
NASA_EARTHDATA_USERNAME = os.getenv("NASA_EARTHDATA_USERNAME", "")
NASA_EARTHDATA_PASSWORD = os.getenv("NASA_EARTHDATA_PASSWORD", "")

# === Areas of Interest ===
TOKYO_BAY_BBOX = {
    "west": 139.5,
    "south": 35.2,
    "east": 140.1,
    "north": 35.7,
}

TOKYO_PORT_CENTER = {
    "lat": 35.62,
    "lon": 139.78,
}

# === Data Paths ===
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = DATA_DIR / "outputs"

for d in [RAW_DIR, PROCESSED_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)
