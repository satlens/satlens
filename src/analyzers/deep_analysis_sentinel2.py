"""
Sentinel-2 Deep Analysis for Tokyo Port Area.

Usage:
    cd satellite-pipeline
    .venv/bin/python -m src.analyzers.deep_analysis_sentinel2

Generates:
1. Tokyo Port cropped RGB (zoom into container yards)
2. NDWI (Normalized Difference Water Index) - water/land boundary
3. NDVI (Normalized Difference Vegetation Index) - green areas vs built-up
4. SWIR False Color (B12/B11/B04) - industrial/built-up area emphasis
5. SCL (Scene Classification Layer) analysis - cloud coverage stats
6. Port area pixel statistics report

All outputs saved to data/outputs/<date>/sentinel2/
"""

import json
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from src.utils.config import OUTPUT_DIR

# === CONFIGURATION ===

# Sentinel-2 product path
PRODUCT_BASE = Path("data/raw/sentinel2/"
    "S2C_MSIL2A_20260312T012701_N0512_R074_T54SUE_20260312T053509.SAFE/"
    "S2C_MSIL2A_20260312T012701_N0512_R074_T54SUE_20260312T053509.SAFE")
GRANULE = PRODUCT_BASE / "GRANULE/L2A_T54SUE_A007909_20260312T013546"
R10M = GRANULE / "IMG_DATA/R10m"
R20M = GRANULE / "IMG_DATA/R20m"

# Tokyo Port area in UTM54N (approximate pixel coordinates for 10m resolution)
# The tile T54SUE covers a 109.8km x 109.8km area at 10m resolution = 10980x10980 pixels
# Tokyo Port (Oi/Aomi container terminals) is roughly at:
#   Lat: 35.60-35.65, Lon: 139.75-139.82
# We need to convert from geo coords to pixel coords using the raster transform

# Key port locations (lat, lon) for reference:
TOKYO_PORT_LOCATIONS = {
    "Oi Container Terminal": (35.607, 139.779),
    "Aomi Container Terminal": (35.618, 139.786),
    "Shinagawa Pier": (35.622, 139.753),
    "Tokyo Gate Bridge": (35.601, 139.815),
    "Harumi Pier": (35.643, 139.778),
}

# Output
TODAY = datetime.now().strftime("%Y-%m-%d")
OUT_DIR = OUTPUT_DIR / TODAY / "sentinel2"


def load_band(filepath: Path) -> np.ndarray:
    """Load a single band from JP2 file."""
    import rasterio
    with rasterio.open(filepath) as src:
        return src.read(1).astype(np.float32), src.transform, src.crs, src.bounds


def find_band(directory: Path, band_name: str) -> Path:
    """Find a band file by name."""
    matches = list(directory.glob(f"*_{band_name}_*.jp2"))
    if not matches:
        raise FileNotFoundError(f"Band {band_name} not found in {directory}")
    return matches[0]


def geo_to_pixel(transform, lon, lat):
    """Convert geographic coordinates to pixel coordinates."""
    from rasterio.transform import rowcol
    # For UTM projected data, we need to convert lat/lon to UTM first
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32654", always_xy=True)
    x, y = transformer.transform(lon, lat)
    row, col = rowcol(transform, x, y)
    return int(row), int(col)


def crop_to_port(data: np.ndarray, transform, padding: int = 200):
    """Crop array to Tokyo Port area with padding."""
    # Tokyo Port bounding box
    port_bbox = {
        "west": 139.72, "east": 139.85,
        "south": 35.58, "north": 35.67,
    }

    row_north, col_west = geo_to_pixel(transform, port_bbox["west"], port_bbox["north"])
    row_south, col_east = geo_to_pixel(transform, port_bbox["east"], port_bbox["south"])

    r_min = max(0, min(row_north, row_south) - padding)
    r_max = min(data.shape[0], max(row_north, row_south) + padding)
    c_min = max(0, min(col_west, col_east) - padding)
    c_max = min(data.shape[1], max(col_west, col_east) + padding)

    return data[r_min:r_max, c_min:c_max], (r_min, r_max, c_min, c_max)


def normalize(band: np.ndarray, plow: float = 2, phigh: float = 98) -> np.ndarray:
    """Percentile-based normalization to 0-1."""
    valid = band[band > 0]
    if len(valid) == 0:
        return np.zeros_like(band)
    low = np.percentile(valid, plow)
    high = np.percentile(valid, phigh)
    return np.clip((band - low) / (high - low + 1e-10), 0, 1)


def analyze_scl(scl_path: Path, transform_20m) -> dict:
    """Analyze Scene Classification Layer for cloud/quality stats."""
    import rasterio
    with rasterio.open(scl_path) as src:
        scl = src.read(1)

    total = scl.size
    # SCL classes: 0=No Data, 1=Saturated, 2=Dark/Shadow, 3=Cloud Shadow,
    # 4=Vegetation, 5=Bare Soil, 6=Water, 7=Cloud Low Prob,
    # 8=Cloud Medium Prob, 9=Cloud High Prob, 10=Thin Cirrus, 11=Snow

    classes = {
        0: "No Data",
        1: "Saturated/Defective",
        2: "Dark Area/Shadow",
        3: "Cloud Shadow",
        4: "Vegetation",
        5: "Bare Soil/Desert",
        6: "Water",
        7: "Cloud Low Probability",
        8: "Cloud Medium Probability",
        9: "Cloud High Probability",
        10: "Thin Cirrus",
        11: "Snow/Ice",
    }

    stats = {}
    for val, name in classes.items():
        count = int(np.sum(scl == val))
        pct = count / total * 100
        stats[name] = {"count": count, "percentage": round(pct, 2)}

    # Cloud-free percentage (classes 4, 5, 6 are usable)
    usable = sum(np.sum(scl == v) for v in [4, 5, 6])
    cloud_affected = sum(np.sum(scl == v) for v in [3, 7, 8, 9, 10])

    stats["_summary"] = {
        "total_pixels": int(total),
        "usable_pct": round(usable / total * 100, 2),
        "cloud_affected_pct": round(cloud_affected / total * 100, 2),
    }

    return stats, scl


def main():
    import rasterio

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    print("=" * 60)
    print("Sentinel-2 Deep Analysis: Tokyo Port (2026-03-12)")
    print("=" * 60)

    # === 1. Load 10m bands ===
    print("\n[1/6] Loading 10m bands (B02, B03, B04, B08)...")
    b02_data, transform, crs, bounds = load_band(find_band(R10M, "B02"))
    b03_data, _, _, _ = load_band(find_band(R10M, "B03"))
    b04_data, _, _, _ = load_band(find_band(R10M, "B04"))
    b08_data, _, _, _ = load_band(find_band(R10M, "B08"))

    print(f"  Image shape: {b02_data.shape}")
    print(f"  CRS: {crs}")
    print(f"  Bounds: {bounds}")
    print(f"  Pixel size: 10m x 10m")

    results["image_info"] = {
        "shape": list(b02_data.shape),
        "crs": str(crs),
        "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
        "acquisition_date": "2026-03-12",
        "tile": "T54SUE",
        "resolution_m": 10,
    }

    # === 2. Crop to Tokyo Port area ===
    print("\n[2/6] Cropping to Tokyo Port area...")
    b02_port, crop_bounds = crop_to_port(b02_data, transform)
    b03_port, _ = crop_to_port(b03_data, transform)
    b04_port, _ = crop_to_port(b04_data, transform)
    b08_port, _ = crop_to_port(b08_data, transform)

    print(f"  Cropped shape: {b02_port.shape}")
    print(f"  Crop pixel bounds: {crop_bounds}")
    area_km2 = b02_port.shape[0] * 10 * b02_port.shape[1] * 10 / 1e6
    print(f"  Covered area: ~{area_km2:.1f} km2")

    # Port RGB
    rgb_port = np.clip(np.dstack([
        normalize(b04_port) * 1.8,
        normalize(b03_port) * 1.8,
        normalize(b02_port) * 1.8,
    ]), 0, 1)

    fig, ax = plt.subplots(1, 1, figsize=(14, 14))
    ax.imshow(rgb_port)
    ax.set_title("Tokyo Port - True Color Zoom (10m, 2026-03-12)", fontsize=14)
    ax.axis("off")
    plt.tight_layout()
    out_path = OUT_DIR / "tokyo_port_zoom_rgb.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")

    # === 3. NDWI (Normalized Difference Water Index) ===
    print("\n[3/6] Computing NDWI (Green - NIR) / (Green + NIR)...")
    # NDWI = (B03 - B08) / (B03 + B08)
    # Values > 0 = water, < 0 = land
    ndwi_port = (b03_port - b08_port) / (b03_port + b08_port + 1e-10)

    water_pixels = np.sum(ndwi_port > 0.0)
    land_pixels = np.sum(ndwi_port <= 0.0)
    total_port = ndwi_port.size
    print(f"  Water pixels (NDWI > 0): {water_pixels} ({water_pixels/total_port*100:.1f}%)")
    print(f"  Land pixels (NDWI <= 0): {land_pixels} ({land_pixels/total_port*100:.1f}%)")

    results["ndwi"] = {
        "water_pct": round(water_pixels / total_port * 100, 2),
        "land_pct": round(land_pixels / total_port * 100, 2),
        "mean": round(float(np.mean(ndwi_port)), 4),
        "std": round(float(np.std(ndwi_port)), 4),
    }

    fig, axes = plt.subplots(1, 2, figsize=(20, 10))

    # NDWI heatmap
    im = axes[0].imshow(ndwi_port, cmap="RdYlBu", vmin=-0.5, vmax=0.5)
    axes[0].set_title("NDWI: Water Index (Blue=Water, Red=Land)", fontsize=13)
    axes[0].axis("off")
    plt.colorbar(im, ax=axes[0], fraction=0.046, pad=0.04)

    # Water mask overlay on RGB
    water_mask = ndwi_port > 0.0
    rgb_with_water = rgb_port.copy()
    rgb_with_water[water_mask] = [0.0, 0.3, 0.9]  # Blue overlay for water
    axes[1].imshow(rgb_with_water)
    axes[1].set_title("Water Mask (NDWI > 0) Overlay on RGB", fontsize=13)
    axes[1].axis("off")

    plt.suptitle("Tokyo Port - NDWI Water Analysis (2026-03-12)", fontsize=15)
    plt.tight_layout()
    out_path = OUT_DIR / "tokyo_port_ndwi.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")

    # === 4. NDVI (Vegetation Index) ===
    print("\n[4/6] Computing NDVI (NIR - Red) / (NIR + Red)...")
    # NDVI = (B08 - B04) / (B08 + B04)
    ndvi_port = (b08_port - b04_port) / (b08_port + b04_port + 1e-10)

    veg_pixels = np.sum(ndvi_port > 0.3)
    built_pixels = np.sum((ndvi_port > -0.1) & (ndvi_port <= 0.3))
    water_ndvi = np.sum(ndvi_port <= -0.1)
    print(f"  Vegetation (NDVI > 0.3): {veg_pixels} ({veg_pixels/total_port*100:.1f}%)")
    print(f"  Built-up/Bare (NDVI -0.1~0.3): {built_pixels} ({built_pixels/total_port*100:.1f}%)")
    print(f"  Water (NDVI < -0.1): {water_ndvi} ({water_ndvi/total_port*100:.1f}%)")

    results["ndvi"] = {
        "vegetation_pct": round(veg_pixels / total_port * 100, 2),
        "builtup_pct": round(built_pixels / total_port * 100, 2),
        "water_pct": round(water_ndvi / total_port * 100, 2),
        "mean": round(float(np.mean(ndvi_port)), 4),
    }

    fig, ax = plt.subplots(1, 1, figsize=(14, 14))
    im = ax.imshow(ndvi_port, cmap="RdYlGn", vmin=-0.3, vmax=0.7)
    ax.set_title("Tokyo Port - NDVI: Vegetation Index\n(Green=Vegetation, Yellow=Bare/Built-up, Red=Water)", fontsize=13)
    ax.axis("off")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    plt.tight_layout()
    out_path = OUT_DIR / "tokyo_port_ndvi.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")

    # === 5. SCL Cloud Analysis (full tile) ===
    print("\n[5/6] Analyzing Scene Classification Layer (cloud coverage)...")
    scl_path = find_band(R20M, "SCL")
    scl_stats, scl_data = analyze_scl(scl_path, None)

    results["scl_stats"] = scl_stats

    print(f"  === Full Tile Cloud Analysis ===")
    summary = scl_stats["_summary"]
    print(f"  Total pixels: {summary['total_pixels']:,}")
    print(f"  Usable (vegetation+soil+water): {summary['usable_pct']:.1f}%")
    print(f"  Cloud-affected: {summary['cloud_affected_pct']:.1f}%")
    print(f"  Key classes:")
    for name, vals in scl_stats.items():
        if name.startswith("_"):
            continue
        if vals["percentage"] > 1.0:
            print(f"    {name}: {vals['percentage']:.1f}%")

    # SCL visualization
    scl_colors = {
        0: [0, 0, 0],       # No Data - Black
        1: [1, 0, 0],       # Saturated - Red
        2: [0.2, 0.2, 0.2], # Dark/Shadow - Dark Gray
        3: [0.5, 0.3, 0.1], # Cloud Shadow - Brown
        4: [0, 0.8, 0],     # Vegetation - Green
        5: [0.8, 0.7, 0.3], # Bare Soil - Tan
        6: [0, 0, 0.8],     # Water - Blue
        7: [0.7, 0.7, 0.7], # Cloud Low - Light Gray
        8: [0.85, 0.85, 0.85], # Cloud Med - Gray
        9: [1, 1, 1],       # Cloud High - White
        10: [0.8, 0.8, 1],  # Thin Cirrus - Light Blue
        11: [0.9, 1, 1],    # Snow - Cyan
    }

    scl_rgb = np.zeros((*scl_data.shape, 3), dtype=np.float32)
    for val, color in scl_colors.items():
        mask = scl_data == val
        scl_rgb[mask] = color

    fig, ax = plt.subplots(1, 1, figsize=(12, 12))
    ax.imshow(scl_rgb)
    ax.set_title(f"Scene Classification (SCL) - Cloud: {summary['cloud_affected_pct']:.1f}%, Usable: {summary['usable_pct']:.1f}%", fontsize=13)
    ax.axis("off")

    # Legend
    legend_items = [
        mpatches.Patch(color=scl_colors[4], label="Vegetation"),
        mpatches.Patch(color=scl_colors[5], label="Bare Soil"),
        mpatches.Patch(color=scl_colors[6], label="Water"),
        mpatches.Patch(color=scl_colors[9], label="Cloud High"),
        mpatches.Patch(color=scl_colors[8], label="Cloud Medium"),
        mpatches.Patch(color=scl_colors[3], label="Cloud Shadow"),
        mpatches.Patch(color=scl_colors[2], label="Dark/Shadow"),
    ]
    ax.legend(handles=legend_items, loc="lower left", fontsize=10)

    plt.tight_layout()
    out_path = OUT_DIR / "tokyo_tile_scl_classification.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")

    # === 6. SWIR False Color (B12/B11/B04) for built-up areas ===
    print("\n[6/6] Creating SWIR false color composite (B12/B11/B04)...")
    print("  Loading 20m SWIR bands...")
    b11_data, t20m, _, _ = load_band(find_band(R20M, "B11"))
    b12_data, _, _, _ = load_band(find_band(R20M, "B12"))
    # Need B04 at 20m
    b04_20m, _, _, _ = load_band(find_band(R20M, "B04"))

    # Crop to port area at 20m resolution
    b11_port, _ = crop_to_port(b11_data, t20m, padding=100)
    b12_port, _ = crop_to_port(b12_data, t20m, padding=100)
    b04_20m_port, _ = crop_to_port(b04_20m, t20m, padding=100)

    swir_rgb = np.clip(np.dstack([
        normalize(b12_port) * 1.5,
        normalize(b11_port) * 1.5,
        normalize(b04_20m_port) * 1.5,
    ]), 0, 1)

    fig, ax = plt.subplots(1, 1, figsize=(14, 14))
    ax.imshow(swir_rgb)
    ax.set_title("Tokyo Port - SWIR False Color (B12/B11/B04)\nBright=Built-up/Industrial, Cyan=Vegetation, Dark=Water", fontsize=13)
    ax.axis("off")
    plt.tight_layout()
    out_path = OUT_DIR / "tokyo_port_swir_false_color.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")

    # === 7. Band reflectance statistics for port area ===
    print("\n[STATS] Port area reflectance statistics:")
    for name, data in [("B02 (Blue)", b02_port), ("B03 (Green)", b03_port),
                        ("B04 (Red)", b04_port), ("B08 (NIR)", b08_port)]:
        valid = data[data > 0]
        print(f"  {name}: mean={np.mean(valid):.1f}, std={np.std(valid):.1f}, "
              f"min={np.min(valid):.0f}, max={np.max(valid):.0f}, "
              f"median={np.median(valid):.0f}")

    # === Save results JSON ===
    results_path = OUT_DIR / "analysis_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults JSON: {results_path}")

    # === Summary ===
    print("\n" + "=" * 60)
    print("ANALYSIS COMPLETE")
    print("=" * 60)
    print(f"Date: 2026-03-12 (Sentinel-2C, Tile T54SUE)")
    print(f"Cloud affected: {summary['cloud_affected_pct']:.1f}% of full tile")
    print(f"Usable area: {summary['usable_pct']:.1f}% of full tile")
    print(f"Port area water: {results['ndwi']['water_pct']:.1f}%")
    print(f"Port area vegetation: {results['ndvi']['vegetation_pct']:.1f}%")
    print(f"Port area built-up: {results['ndvi']['builtup_pct']:.1f}%")
    print(f"\nOutputs in: {OUT_DIR}")


if __name__ == "__main__":
    main()
