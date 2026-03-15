# SatLens

Monitoring Tokyo Bay economic activity with free satellite data.

## What is this?

Free satellite data (Sentinel-2, AIS, VIIRS) to observe and quantify economic activity in Tokyo Bay. Ship detection, nighttime light analysis, and port activity monitoring — all with open data and no machine learning required.

## Data Sources

| Source | Resolution | Cost | Use Case |
|--------|-----------|------|----------|
| Sentinel-2 (ESA) | 10m | Free | Ship detection, port area monitoring |
| AIS (AISStream.io) | Real-time | Free | Ship tracking, vessel identification |
| VIIRS (NASA/NOAA) | 500m | Free | Nighttime light / economic activity |

## Project Structure

```
src/
├── collectors/         # Data acquisition
│   ├── sentinel2.py    # Sentinel-2 image download
│   ├── ais.py          # AIS ship tracking via WebSocket
│   └── viirs.py        # VIIRS nighttime light data
├── analyzers/          # Analysis pipelines
│   ├── detect_ships_sentinel2.py   # Ship detection (NDWI + anomaly)
│   ├── analyze_viirs_nightlight.py # Nighttime light analysis
│   └── visualize_sentinel2.py      # Satellite image visualization
└── utils/
    └── config.py       # Configuration & API settings
```

## Ship Detection

Detects vessels in Sentinel-2 imagery using statistical anomaly detection:

1. **Water mask**: NDWI + NIR absorption + visible light thresholding
2. **Coastal filtering**: 150m erosion to remove port structures
3. **Anomaly detection**: Multi-band brightness (mean + 3σ), ≥2 of 4 bands
4. **Shape filtering**: Length 40-500m, aspect ratio ≥ 1.3

Results on Tokyo Bay:
- 2026-03-07: **358 vessels** detected (Large: 54, Medium: 109, Small: 195)
- 2026-03-12: **320 vessels** detected (Large: 63, Medium: 103, Small: 154)
- Validated against MLIT statistics (estimated 270-560 vessels instantaneous)

## Setup

```bash
# Clone
git clone https://github.com/satlens/satlens.git
cd satlens

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your keys
```

### API Keys Required

| Service | Key | Get it at |
|---------|-----|-----------|
| Copernicus Data Space | `COPERNICUS_CLIENT_ID` / `SECRET` | https://dataspace.copernicus.eu/ |
| AISStream.io | `AISSTREAM_API_KEY` | https://aisstream.io/ |
| NASA Earthdata | `EARTHDATA_USERNAME` / `PASSWORD` | https://urs.earthdata.nasa.gov/ |

## Usage

```bash
# Ship detection on Sentinel-2 imagery
python -m src.analyzers.detect_ships_sentinel2

# AIS data collection (Tokyo Bay, 5 minutes)
python -m src.collectors.ais --duration 300

# VIIRS nighttime light analysis
python -m src.analyzers.analyze_viirs_nightlight
```

## Articles (Japanese)

- [海外ファンドは衛星データで何を見ているのか](https://note.com/sat_lens)
- Sentinel-2で東京湾の船を数えてみた (coming soon)

## License

MIT

## Links

- note: https://note.com/sat_lens
- X: https://x.com/sat_lens
