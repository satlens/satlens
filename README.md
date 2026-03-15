# SatLens

Observing global economic activity with free satellite data.

## What is this?

An open-source pipeline for monitoring economic activity from space using freely available satellite data. Ship detection, nighttime light analysis, port activity tracking, and more — no machine learning, no paid imagery, no GPU required.

Currently focused on Tokyo Bay as the first target, with plans to expand to major ports and economic zones worldwide.

## Data Sources

| Source | Resolution | Cost | Use Case |
|--------|-----------|------|----------|
| Sentinel-2 (ESA) | 10m | Free | Ship detection, land use change, port monitoring |
| AIS (AISStream.io) | Real-time | Free | Ship tracking, vessel identification, trade flow |
| VIIRS (NASA/NOAA) | 500m | Free | Nighttime light, economic activity proxy |

## Project Structure

```
src/
├── collectors/         # Data acquisition
│   ├── sentinel2.py    # Sentinel-2 image download (global)
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

Detects vessels in Sentinel-2 imagery using statistical anomaly detection. No training data needed — works on any coastal area worldwide.

1. **Water mask**: NDWI + NIR absorption + visible light thresholding
2. **Coastal filtering**: 150m erosion to remove port structures
3. **Anomaly detection**: Multi-band brightness (mean + 3σ), ≥2 of 4 bands
4. **Shape filtering**: Length 40-500m, aspect ratio ≥ 1.3

Results on Tokyo Bay (first validation):
- 2026-03-07: **358 vessels** detected (Large: 54, Medium: 109, Small: 195)
- 2026-03-12: **320 vessels** detected (Large: 63, Medium: 103, Small: 154)
- Validated against MLIT statistics (estimated 270-560 vessels instantaneous)

## Setup

```bash
git clone https://github.com/satlens/satlens.git
cd satlens
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys
```

### API Keys

| Service | Key | Sign up |
|---------|-----|---------|
| Copernicus Data Space | `COPERNICUS_CLIENT_ID` / `SECRET` | https://dataspace.copernicus.eu/ |
| AISStream.io | `AISSTREAM_API_KEY` | https://aisstream.io/ |
| NASA Earthdata | `EARTHDATA_USERNAME` / `PASSWORD` | https://urs.earthdata.nasa.gov/ |

All services offer free API keys.

## Usage

```bash
# Ship detection on Sentinel-2 imagery
python -m src.analyzers.detect_ships_sentinel2

# AIS data collection (5 minutes)
python -m src.collectors.ais --duration 300

# VIIRS nighttime light analysis
python -m src.analyzers.analyze_viirs_nightlight
```

## Articles

- [海外ファンドは衛星データで何を見ているのか](https://note.com/sat_lens)
- Sentinel-2で東京湾の船を数えてみた (coming soon)

## License

MIT

## Links

- note: https://note.com/sat_lens
- X: https://x.com/sat_lens

---

## 日本語

無料の衛星データを使って、世界の経済活動を宇宙から観測するオープンソースパイプラインです。

Sentinel-2（光学衛星画像）、AIS（船舶位置データ）、VIIRS（夜間光データ）を組み合わせ、船舶検知・夜間光解析・港湾活動のモニタリングを行います。機械学習不要、有料画像不要、GPU不要。

現在は東京湾を最初の検証対象として分析を進めており、今後は世界の主要港湾・経済圏に展開予定です。

詳しくは [note](https://note.com/sat_lens) で実験記録を公開しています。
