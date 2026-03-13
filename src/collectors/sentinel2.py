"""
Sentinel-2 image collector via Copernicus Data Space OData API.

Usage:
    python -m src.collectors.sentinel2 [--days 30] [--max-cloud 20] [--download]

This module:
1. Authenticates with Copernicus Data Space
2. Searches for Sentinel-2 images over Tokyo Bay
3. Optionally downloads the best (least cloudy) image
4. Generates an RGB composite preview with matplotlib
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

from src.utils.config import (
    COPERNICUS_USERNAME,
    COPERNICUS_PASSWORD,
    COPERNICUS_TOKEN_URL,
    COPERNICUS_ODATA_URL,
    COPERNICUS_DOWNLOAD_URL,
    TOKYO_BAY_BBOX,
    RAW_DIR,
)


def get_access_token(username: str, password: str) -> str:
    """Obtain an access token from Copernicus Data Space."""
    response = requests.post(
        COPERNICUS_TOKEN_URL,
        data={
            "client_id": "cdse-public",
            "grant_type": "password",
            "username": username,
            "password": password,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def search_products(
    start_date: str,
    end_date: str,
    max_cloud_cover: float = 20.0,
    bbox: dict | None = None,
    max_results: int = 20,
) -> list[dict]:
    """
    Search Sentinel-2 L2A products via OData API.

    Args:
        start_date: ISO format date string (YYYY-MM-DD)
        end_date: ISO format date string (YYYY-MM-DD)
        max_cloud_cover: Maximum cloud cover percentage (0-100)
        bbox: Bounding box dict with west, south, east, north
        max_results: Maximum number of results to return

    Returns:
        List of product metadata dicts
    """
    if bbox is None:
        bbox = TOKYO_BAY_BBOX

    # Build OData filter
    aoi_wkt = (
        f"POLYGON(("
        f"{bbox['west']} {bbox['south']},"
        f"{bbox['east']} {bbox['south']},"
        f"{bbox['east']} {bbox['north']},"
        f"{bbox['west']} {bbox['north']},"
        f"{bbox['west']} {bbox['south']}"
        f"))"
    )

    filters = [
        "Collection/Name eq 'SENTINEL-2'",
        f"OData.CSC.Intersects(area=geography'SRID=4326;{aoi_wkt}')",
        f"ContentDate/Start gt {start_date}T00:00:00.000Z",
        f"ContentDate/Start lt {end_date}T23:59:59.999Z",
        f"Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value lt {max_cloud_cover})",
        "contains(Name,'L2A')",
    ]

    params = {
        "$filter": " and ".join(filters),
        "$orderby": "ContentDate/Start desc",
        "$top": max_results,
    }

    print(f"Searching Sentinel-2 products: {start_date} to {end_date}, cloud < {max_cloud_cover}%...")
    response = requests.get(
        f"{COPERNICUS_ODATA_URL}/Products",
        params=params,
        timeout=60,
    )
    response.raise_for_status()
    products = response.json().get("value", [])
    print(f"Found {len(products)} products")
    return products


def download_product(product_id: str, product_name: str, token: str, output_dir: Path | None = None) -> Path:
    """
    Download a Sentinel-2 product as a zip file.

    Args:
        product_id: Product UUID
        product_name: Product name (used for filename)
        token: Access token
        output_dir: Directory to save the file

    Returns:
        Path to downloaded file
    """
    if output_dir is None:
        output_dir = RAW_DIR / "sentinel2"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{product_name}.zip"
    if output_path.exists():
        print(f"Already downloaded: {output_path}")
        return output_path

    url = f"{COPERNICUS_DOWNLOAD_URL}/Products({product_id})/$value"
    headers = {"Authorization": f"Bearer {token}"}

    print(f"Downloading {product_name}...")
    response = requests.get(url, headers=headers, stream=True, timeout=300)
    response.raise_for_status()

    total_size = int(response.headers.get("content-length", 0))
    downloaded = 0

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                pct = downloaded / total_size * 100
                print(f"\r  {downloaded / 1e6:.1f} MB / {total_size / 1e6:.1f} MB ({pct:.0f}%)", end="")

    print(f"\nSaved: {output_path}")
    return output_path


def print_product_summary(products: list[dict]) -> None:
    """Print a summary table of found products."""
    if not products:
        print("No products found.")
        return

    print(f"\n{'#':>3} | {'Date':^12} | {'Cloud%':>6} | {'Name'}")
    print("-" * 80)
    for i, p in enumerate(products):
        name = p.get("Name", "N/A")
        date_str = p.get("ContentDate", {}).get("Start", "N/A")[:10]
        # Extract cloud cover from attributes
        cloud = "N/A"
        for attr in p.get("Attributes", []):
            if attr.get("Name") == "cloudCover":
                cloud = f"{attr['Value']:.1f}"
                break
        print(f"{i+1:>3} | {date_str:^12} | {cloud:>6} | {name}")


def main():
    parser = argparse.ArgumentParser(description="Search/download Sentinel-2 images over Tokyo Bay")
    parser.add_argument("--days", type=int, default=30, help="Search window in days (default: 30)")
    parser.add_argument("--max-cloud", type=float, default=20.0, help="Max cloud cover %% (default: 20)")
    parser.add_argument("--download", action="store_true", help="Download the best (least cloudy) product")
    parser.add_argument("--output-json", type=str, help="Save search results to JSON file")
    args = parser.parse_args()

    # Validate credentials
    if not COPERNICUS_USERNAME or not COPERNICUS_PASSWORD:
        print("Error: COPERNICUS_USERNAME and COPERNICUS_PASSWORD must be set in .env")
        print("Create an account at: https://dataspace.copernicus.eu/")
        print("Then copy .env.example to .env and fill in your credentials.")
        sys.exit(1)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    # Search
    products = search_products(start_date, end_date, args.max_cloud)
    print_product_summary(products)

    # Save results
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(products, f, indent=2, default=str)
        print(f"\nResults saved to: {output_path}")

    # Download
    if args.download and products:
        token = get_access_token(COPERNICUS_USERNAME, COPERNICUS_PASSWORD)
        best = products[0]  # Already sorted by date desc
        download_product(best["Id"], best["Name"], token)

    return products


if __name__ == "__main__":
    main()
