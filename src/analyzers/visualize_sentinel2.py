"""
Sentinel-2 RGB composite visualization.

Usage:
    python -m src.analyzers.visualize_sentinel2 <path_to_sentinel2_folder_or_zip>

Takes a Sentinel-2 L2A product (folder or zip) and generates:
1. True color RGB (B4, B3, B2) composite
2. False color (B8, B4, B3) composite for vegetation/water contrast
3. Saves as PNG in data/outputs/
"""

import argparse
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np

from src.utils.config import OUTPUT_DIR


def find_band_files(product_path: Path, resolution: str = "10m") -> dict[str, Path]:
    """
    Find band files within a Sentinel-2 product.

    Args:
        product_path: Path to .SAFE folder or extracted directory
        resolution: Target resolution folder (10m, 20m, 60m)

    Returns:
        Dict mapping band names (B02, B03, B04, B08) to file paths
    """
    bands = {}
    # Search for JP2 files in the IMG_DATA directory
    patterns = [
        f"**/*_{resolution}/*_B*.jp2",
        f"**/*_B*.jp2",
        f"**/IMG_DATA/**/*_B*.jp2",
    ]

    for pattern in patterns:
        for f in product_path.glob(pattern):
            # Extract band name (e.g., B02, B03, B04, B08)
            name = f.stem
            for band in ["B02", "B03", "B04", "B08"]:
                if band in name:
                    bands[band] = f
                    break

    return bands


def load_band(filepath: Path) -> np.ndarray:
    """Load a single band from a JP2 or TIF file using rasterio."""
    import rasterio

    with rasterio.open(filepath) as src:
        return src.read(1).astype(np.float32)


def normalize_band(band: np.ndarray, percentile_low: float = 2, percentile_high: float = 98) -> np.ndarray:
    """Normalize band values to 0-1 range using percentile stretching."""
    low = np.percentile(band[band > 0], percentile_low)
    high = np.percentile(band[band > 0], percentile_high)
    band_norm = np.clip((band - low) / (high - low + 1e-10), 0, 1)
    return band_norm


def create_rgb_composite(
    red: np.ndarray,
    green: np.ndarray,
    blue: np.ndarray,
    title: str = "Sentinel-2 RGB",
    output_path: Path | None = None,
    brightness: float = 1.5,
) -> Path | None:
    """
    Create and save an RGB composite image.

    Args:
        red, green, blue: 2D arrays for each channel
        title: Plot title
        output_path: Path to save the PNG
        brightness: Brightness multiplier (default 1.5 for satellite imagery)

    Returns:
        Path to saved image
    """
    import matplotlib.pyplot as plt

    # Normalize each band
    r = normalize_band(red) * brightness
    g = normalize_band(green) * brightness
    b = normalize_band(blue) * brightness

    # Stack into RGB
    rgb = np.clip(np.dstack([r, g, b]), 0, 1)

    fig, ax = plt.subplots(1, 1, figsize=(12, 12))
    ax.imshow(rgb)
    ax.set_title(title, fontsize=14)
    ax.axis("off")
    plt.tight_layout()

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
        plt.close()
        return output_path
    else:
        plt.show()
        plt.close()
        return None


def process_product(product_path: Path, output_dir: Path | None = None) -> list[Path]:
    """
    Process a Sentinel-2 product and generate RGB composites.

    Args:
        product_path: Path to .SAFE folder, zip, or extracted directory
        output_dir: Output directory for PNGs

    Returns:
        List of generated image paths
    """
    if output_dir is None:
        today = datetime.now().strftime("%Y-%m-%d")
        output_dir = OUTPUT_DIR / today / "sentinel2"

    # Handle zip files
    if product_path.suffix == ".zip":
        extract_dir = product_path.parent / product_path.stem
        if not extract_dir.exists():
            print(f"Extracting {product_path}...")
            with zipfile.ZipFile(product_path, "r") as z:
                z.extractall(extract_dir)
        product_path = extract_dir

    # Find bands
    bands = find_band_files(product_path)
    required = ["B02", "B03", "B04"]

    missing = [b for b in required if b not in bands]
    if missing:
        print(f"Error: Missing bands: {missing}")
        print(f"Found bands: {list(bands.keys())}")
        sys.exit(1)

    print(f"Found bands: {list(bands.keys())}")
    for name, path in bands.items():
        print(f"  {name}: {path}")

    # Load bands
    print("Loading bands...")
    b02 = load_band(bands["B02"])  # Blue
    b03 = load_band(bands["B03"])  # Green
    b04 = load_band(bands["B04"])  # Red

    outputs = []

    # 1. True Color RGB (B4=Red, B3=Green, B2=Blue)
    print("Creating true color RGB composite...")
    rgb_path = create_rgb_composite(
        b04, b03, b02,
        title="Tokyo Bay - True Color (B4/B3/B2)",
        output_path=output_dir / "tokyo_bay_true_color.png",
    )
    if rgb_path:
        outputs.append(rgb_path)

    # 2. False Color (B8=NIR, B4=Red, B3=Green) - if B08 available
    if "B08" in bands:
        print("Loading NIR band...")
        b08 = load_band(bands["B08"])  # NIR

        print("Creating false color composite...")
        fc_path = create_rgb_composite(
            b08, b04, b03,
            title="Tokyo Bay - False Color (B8/B4/B3) - Vegetation=Red, Water=Dark",
            output_path=output_dir / "tokyo_bay_false_color.png",
            brightness=1.3,
        )
        if fc_path:
            outputs.append(fc_path)

    return outputs


def main():
    parser = argparse.ArgumentParser(description="Generate RGB composites from Sentinel-2 products")
    parser.add_argument("product_path", type=str, help="Path to Sentinel-2 .SAFE folder or .zip file")
    parser.add_argument("--output-dir", type=str, help="Output directory (default: data/outputs/<date>/sentinel2/)")
    args = parser.parse_args()

    product_path = Path(args.product_path)
    if not product_path.exists():
        print(f"Error: {product_path} not found")
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else None
    outputs = process_product(product_path, output_dir)

    print(f"\nGenerated {len(outputs)} images:")
    for p in outputs:
        print(f"  {p}")


if __name__ == "__main__":
    main()
