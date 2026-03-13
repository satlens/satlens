"""
AIS ship tracking collector via AISStream.io WebSocket API.

Usage:
    python -m src.collectors.ais [--duration 60] [--output-json data/raw/ais/today.json]

This module:
1. Connects to AISStream.io WebSocket
2. Receives real-time ship position reports in Tokyo Bay
3. Filters by ship type (cargo, tanker, container)
4. Saves collected data to JSON
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import websockets

from src.utils.config import AISSTREAM_API_KEY, TOKYO_BAY_BBOX, RAW_DIR


# AIS ship type codes (from ITU-R M.585)
SHIP_TYPES = {
    range(70, 80): "Cargo",
    range(80, 90): "Tanker",
    range(60, 70): "Passenger",
    range(40, 50): "High Speed Craft",
    range(30, 40): "Fishing",
    range(20, 30): "Towing/Dredging",
}


def classify_ship(ship_type: int) -> str:
    """Classify ship type code to human-readable category."""
    for type_range, name in SHIP_TYPES.items():
        if ship_type in type_range:
            return name
    return f"Other ({ship_type})"


async def collect_ais_data(
    duration_seconds: int = 60,
    bbox: dict | None = None,
) -> list[dict]:
    """
    Collect AIS data from Tokyo Bay for a given duration.

    Args:
        duration_seconds: How long to collect data (seconds)
        bbox: Bounding box (default: Tokyo Bay)

    Returns:
        List of ship position records
    """
    if not AISSTREAM_API_KEY:
        print("Error: AISSTREAM_API_KEY not set in .env")
        sys.exit(1)

    if bbox is None:
        bbox = TOKYO_BAY_BBOX

    # AISStream expects [[lat_min, lon_min], [lat_max, lon_max]]
    bounding_boxes = [[
        [bbox["south"], bbox["west"]],
        [bbox["north"], bbox["east"]],
    ]]

    subscribe_message = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": bounding_boxes,
    }

    records = []
    seen_ships = set()
    start_time = time.time()

    print(f"Connecting to AISStream.io...")
    print(f"Area: Tokyo Bay ({bbox['south']}-{bbox['north']}N, {bbox['west']}-{bbox['east']}E)")
    print(f"Duration: {duration_seconds}s")
    print()

    try:
        async with websockets.connect("wss://stream.aisstream.io/v0/stream") as ws:
            await ws.send(json.dumps(subscribe_message))
            print("Connected. Receiving ship data...\n")

            while time.time() - start_time < duration_seconds:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue

                message = json.loads(raw)
                msg_type = message.get("MessageType", "")

                if msg_type == "PositionReport":
                    report = message["Message"]["PositionReport"]
                    meta = message.get("MetaData", {})

                    mmsi = str(report.get("UserID", ""))
                    lat = report.get("Latitude", 0)
                    lon = report.get("Longitude", 0)
                    speed = report.get("Sog", 0)  # Speed over ground
                    course = report.get("Cog", 0)  # Course over ground
                    heading = report.get("TrueHeading", 0)
                    ship_name = meta.get("ShipName", "").strip()
                    ship_type = meta.get("ShipType", 0)

                    record = {
                        "timestamp": datetime.now().isoformat(),
                        "mmsi": mmsi,
                        "ship_name": ship_name,
                        "ship_type": ship_type,
                        "ship_category": classify_ship(ship_type),
                        "latitude": lat,
                        "longitude": lon,
                        "speed_knots": speed,
                        "course": course,
                        "heading": heading,
                    }
                    records.append(record)

                    if mmsi not in seen_ships:
                        seen_ships.add(mmsi)
                        print(f"  [{len(seen_ships):>3}] {ship_name:<25} | {classify_ship(ship_type):<15} | "
                              f"{lat:.4f}N {lon:.4f}E | {speed:.1f}kn")

            elapsed = time.time() - start_time
            print(f"\n--- Collection complete ---")
            print(f"Duration: {elapsed:.0f}s")
            print(f"Total records: {len(records)}")
            print(f"Unique ships: {len(seen_ships)}")

    except Exception as e:
        print(f"Connection error: {e}")
        if records:
            print(f"Collected {len(records)} records before error")

    return records


def summarize(records: list[dict]) -> dict:
    """Generate a summary of collected AIS data."""
    if not records:
        return {"total_records": 0, "unique_ships": 0}

    ships = {}
    for r in records:
        mmsi = r["mmsi"]
        if mmsi not in ships:
            ships[mmsi] = r

    categories = {}
    for s in ships.values():
        cat = s["ship_category"]
        categories[cat] = categories.get(cat, 0) + 1

    return {
        "total_records": len(records),
        "unique_ships": len(ships),
        "by_category": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "collection_start": records[0]["timestamp"],
        "collection_end": records[-1]["timestamp"],
    }


def main():
    parser = argparse.ArgumentParser(description="Collect AIS ship data from Tokyo Bay")
    parser.add_argument("--duration", type=int, default=60, help="Collection duration in seconds (default: 60)")
    parser.add_argument("--output-json", type=str, help="Save results to JSON file")
    args = parser.parse_args()

    records = asyncio.run(collect_ais_data(duration_seconds=args.duration))

    if records:
        summary = summarize(records)
        print(f"\nShip categories:")
        for cat, count in summary["by_category"].items():
            print(f"  {cat}: {count}")

        # Save
        output_path = args.output_json
        if not output_path:
            today = datetime.now().strftime("%Y-%m-%d")
            output_dir = RAW_DIR / "ais"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"{today}.json")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w") as f:
            json.dump({"summary": summary, "records": records}, f, indent=2, ensure_ascii=False)
        print(f"\nSaved: {output}")

    return records


if __name__ == "__main__":
    main()
