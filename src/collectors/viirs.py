"""
VIIRS Nighttime Lights collector via NASA Earthdata.

Usage:
    python -m src.collectors.viirs [--months 3] [--output-dir data/raw/viirs]

This module:
1. Authenticates with NASA Earthdata
2. Searches for VIIRS Black Marble (VNP46A2) daily nighttime lights
3. Downloads data for Tokyo Bay area
4. Generates nighttime light visualization
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import earthaccess
import numpy as np

from src.utils.config import (
    NASA_EARTHDATA_USERNAME,
    NASA_EARTHDATA_PASSWORD,
    TOKYO_BAY_BBOX,
    RAW_DIR,
    OUTPUT_DIR,
)


def authenticate() -> bool:
    """Authenticate with NASA Earthdata."""
    if not NASA_EARTHDATA_USERNAME or not NASA_EARTHDATA_PASSWORD:
        print("Error: NASA_EARTHDATA_USERNAME and NASA_EARTHDATA_PASSWORD must be set in .env")
        sys.exit(1)

    try:
        earthaccess.login(
            strategy="environment",
        )
        print("NASA Earthdata authentication: OK")
        return True
    except Exception:
        # Try direct login
        try:
            earthaccess.login(
                username=NASA_EARTHDATA_USERNAME,
                password=NASA_EARTHDATA_PASSWORD,
            )
            print("NASA Earthdata authentication: OK")
            return True
        except Exception as e:
            print(f"Authentication failed: {e}")
            return False


def search_viirs(
    start_date: str,
    end_date: str,
    bbox: dict | None = None,
    dataset: str = "VNP46A2",
) -> list:
    """
    Search VIIRS nighttime lights data.

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        bbox: Bounding box dict
        dataset: VNP46A2 (daily) or VNP46A4 (yearly)

    Returns:
        List of granule results
    """
    if bbox is None:
        bbox = TOKYO_BAY_BBOX

    print(f"Searching {dataset}: {start_date} to {end_date}...")
    results = earthaccess.search_data(
        short_name=dataset,
        bounding_box=(bbox["west"], bbox["south"], bbox["east"], bbox["north"]),
        temporal=(start_date, end_date),
    )
    print(f"Found {len(results)} granules")
    return results


def download_viirs(results: list, output_dir: Path | None = None, max_files: int = 5) -> list[Path]:
    """
    Download VIIRS data files.

    Args:
        results: Search results from earthaccess
        output_dir: Directory to save files
        max_files: Maximum number of files to download

    Returns:
        List of downloaded file paths
    """
    if output_dir is None:
        output_dir = RAW_DIR / "viirs"
    output_dir.mkdir(parents=True, exist_ok=True)

    to_download = results[:max_files]
    print(f"Downloading {len(to_download)} files to {output_dir}...")

    files = earthaccess.download(to_download, str(output_dir))
    print(f"Downloaded {len(files)} files")
    return [Path(f) for f in files]


def visualize_nightlight(filepath: Path, output_dir: Path | None = None) -> Path | None:
    """
    Visualize a VIIRS nighttime light HDF5 file.

    Args:
        filepath: Path to the HDF5 file
        output_dir: Output directory for PNG

    Returns:
        Path to saved image
    """
    import matplotlib.pyplot as plt

    if output_dir is None:
        today = datetime.now().strftime("%Y-%m-%d")
        output_dir = OUTPUT_DIR / today / "viirs"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import h5py
    except ImportError:
        print("h5py not installed. Installing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "h5py"])
        import h5py

    print(f"Reading {filepath.name}...")

    with h5py.File(filepath, "r") as f:
        # List available datasets
        print("Available datasets:")
        def print_datasets(name, obj):
            if isinstance(obj, h5py.Dataset):
                print(f"  {name}: {obj.shape} {obj.dtype}")
        f.visititems(print_datasets)

        # Try to find nighttime lights data
        # VNP46A2 structure: HDFEOS/GRIDS/VNP_Grid_DNB/Data Fields/
        possible_paths = [
            "HDFEOS/GRIDS/VNP_Grid_DNB/Data Fields/DNB_BRDF-Corrected_NTL",
            "HDFEOS/GRIDS/VNP_Grid_DNB/Data Fields/Gap_Filled_DNB_BRDF-Corrected_NTL",
            "HDFEOS/GRIDS/VNP_Grid_DNB/Data Fields/DNB_At_Sensor_Radiance_500m",
        ]

        data = None
        used_path = None
        for path in possible_paths:
            if path in f:
                data = f[path][:]
                used_path = path
                print(f"Using: {path}")
                print(f"Shape: {data.shape}, dtype: {data.dtype}")
                break

        if data is None:
            print("Could not find nighttime light data in file")
            return None

    # Process: mask fill values, apply scale
    data = data.astype(np.float32)
    data[data >= 65535] = np.nan  # Fill value

    # Log scale for better visualization
    data_vis = np.log1p(np.clip(data, 0, None))

    fig, ax = plt.subplots(1, 1, figsize=(12, 10))
    im = ax.imshow(data_vis, cmap="hot", interpolation="nearest")
    ax.set_title(f"VIIRS Nighttime Lights\n{filepath.stem}", fontsize=12)
    ax.axis("off")
    plt.colorbar(im, ax=ax, label="Log(radiance + 1)", shrink=0.7)
    plt.tight_layout()

    output_path = output_dir / f"{filepath.stem}_nightlight.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Collect VIIRS nighttime lights data")
    parser.add_argument("--start", type=str, default="2026-03-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-03-13", help="End date (YYYY-MM-DD)")
    parser.add_argument("--max-files", type=int, default=3, help="Max files to download")
    parser.add_argument("--visualize", action="store_true", help="Generate visualization")
    args = parser.parse_args()

    # Authenticate
    if not authenticate():
        sys.exit(1)

    # Search
    results = search_viirs(args.start, args.end)

    if not results:
        print("No data found. Try a wider date range.")
        return

    # Download
    files = download_viirs(results, max_files=args.max_files)

    # Visualize
    if args.visualize and files:
        for f in files:
            if f.suffix in (".h5", ".hdf", ".nc"):
                visualize_nightlight(f)


if __name__ == "__main__":
    main()
