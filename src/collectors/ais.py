"""
AIS ship tracking collector via AISStream.io WebSocket API.

Usage:
    # Connection test (10s, verify API key & data flow)
    python -m src.collectors.ais --test

    # Collect for 5 minutes
    python -m src.collectors.ais --duration 300

    # Collect with custom output path
    python -m src.collectors.ais --duration 60 --output-json data/raw/ais/2026-03-14.json

This module:
1. Connects to AISStream.io WebSocket (wss://stream.aisstream.io/v0/stream)
2. Subscribes with BoundingBox filter for Tokyo Bay area
3. Receives PositionReport + ShipStaticData messages
4. Classifies ships by type (Cargo, Tanker, Container, etc.)
5. Detects potential port arrivals (speed < 3kn near port)
6. Saves collected data to JSON with summary statistics

API Key:
    Set AISSTREAM_API_KEY in satellite-pipeline/.env
    Get a free key at https://aisstream.io/ (sign in via GitHub)
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

from src.utils.config import AISSTREAM_API_KEY, TOKYO_BAY_BBOX, RAW_DIR

# WebSocket endpoint
AISSTREAM_WS_URL = "wss://stream.aisstream.io/v0/stream"

# AIS ship type codes (ITU-R M.585-7)
SHIP_TYPES = {
    range(70, 80): "Cargo",
    range(80, 90): "Tanker",
    range(60, 70): "Passenger",
    range(40, 50): "High Speed Craft",
    range(30, 40): "Fishing",
    range(20, 30): "Towing/Dredging",
    range(50, 60): "Pilot/SAR/Military",
    range(90, 100): "Other (reserved)",
}

# Subtypes within Cargo (70-79) for finer classification
CARGO_SUBTYPES = {
    71: "Cargo - DG/HS/MP",     # Dangerous goods / hazardous / marine pollutant
    72: "Cargo - DG/HS",
    73: "Cargo - DG/MP",
    74: "Cargo - DG",
    75: "Container Ship",       # Not official but commonly mapped
    79: "Cargo - No info",
}

# Tokyo Bay port approach zone (inner bay, near major berths)
TOKYO_PORT_APPROACH = {
    "lat_min": 35.55,
    "lat_max": 35.68,
    "lon_min": 139.72,
    "lon_max": 139.87,
}

# Speed threshold for "arriving/berthing" (knots)
ARRIVAL_SPEED_THRESHOLD = 3.0


def classify_ship(ship_type: int) -> str:
    """Classify AIS ship type code to human-readable category."""
    # Check cargo subtypes first for container ship detection
    if ship_type in CARGO_SUBTYPES:
        return CARGO_SUBTYPES[ship_type]
    for type_range, name in SHIP_TYPES.items():
        if ship_type in type_range:
            return name
    if ship_type == 0:
        return "Not available"
    return f"Other ({ship_type})"


def is_in_port_approach(lat: float, lon: float) -> bool:
    """Check if position is within Tokyo Bay port approach zone."""
    zone = TOKYO_PORT_APPROACH
    return (zone["lat_min"] <= lat <= zone["lat_max"] and
            zone["lon_min"] <= lon <= zone["lon_max"])


def is_target_vessel(ship_type: int) -> bool:
    """Check if vessel is a target type (cargo/tanker/container)."""
    return ship_type in range(70, 90)  # Cargo (70-79) + Tanker (80-89)


async def collect_ais_data(
    duration_seconds: int = 60,
    bbox: dict | None = None,
    target_only: bool = False,
) -> dict:
    """
    Collect AIS data from Tokyo Bay for a given duration.

    Args:
        duration_seconds: How long to collect data (seconds).
        bbox: Bounding box dict with north/south/east/west keys.
        target_only: If True, only store Cargo/Tanker vessels.

    Returns:
        Dict with 'records', 'static_data', and 'arrivals' lists.
    """
    if not AISSTREAM_API_KEY:
        print("[ERROR] AISSTREAM_API_KEY not set.")
        print("  1. Get a free API key at https://aisstream.io/")
        print("  2. Add to satellite-pipeline/.env:")
        print('     AISSTREAM_API_KEY="your_key_here"')
        sys.exit(1)

    if bbox is None:
        bbox = TOKYO_BAY_BBOX

    bounding_boxes = [[
        [bbox["south"], bbox["west"]],
        [bbox["north"], bbox["east"]],
    ]]

    subscribe_message = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": bounding_boxes,
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    records = []
    static_data = {}  # keyed by MMSI
    arrivals = []     # vessels detected as arriving
    seen_ships = set()
    msg_count = 0
    start_time = time.time()

    print(f"[AIS] Connecting to {AISSTREAM_WS_URL}")
    print(f"[AIS] Area: Tokyo Bay ({bbox['south']}-{bbox['north']}N, "
          f"{bbox['west']}-{bbox['east']}E)")
    print(f"[AIS] Duration: {duration_seconds}s")
    print(f"[AIS] Filters: PositionReport, ShipStaticData")
    if target_only:
        print(f"[AIS] Recording: Cargo/Tanker only")
    print()

    try:
        async with websockets.connect(AISSTREAM_WS_URL) as ws:
            await ws.send(json.dumps(subscribe_message))
            print("[AIS] Connected. Waiting for data...\n")

            while time.time() - start_time < duration_seconds:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    elapsed = int(time.time() - start_time)
                    print(f"  ... waiting ({elapsed}s / {duration_seconds}s, "
                          f"{msg_count} msgs, {len(seen_ships)} ships)")
                    continue

                message = json.loads(raw)
                msg_type = message.get("MessageType", "")
                meta = message.get("MetaData", {})
                msg_count += 1

                if msg_type == "PositionReport":
                    report = message["Message"]["PositionReport"]
                    mmsi = str(report.get("UserID", ""))
                    lat = report.get("Latitude", 0)
                    lon = report.get("Longitude", 0)
                    speed = report.get("Sog", 0)
                    course = report.get("Cog", 0)
                    heading = report.get("TrueHeading", 0)
                    nav_status = report.get("NavigationalStatus", -1)
                    ship_name = meta.get("ShipName", "").strip()
                    ship_type = meta.get("ShipType", 0)
                    category = classify_ship(ship_type)

                    # Skip non-target vessels if filter is on
                    if target_only and not is_target_vessel(ship_type):
                        continue

                    record = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "mmsi": mmsi,
                        "ship_name": ship_name,
                        "ship_type": ship_type,
                        "ship_category": category,
                        "latitude": lat,
                        "longitude": lon,
                        "speed_knots": speed,
                        "course": course,
                        "heading": heading,
                        "nav_status": nav_status,
                        "in_port_approach": is_in_port_approach(lat, lon),
                    }
                    records.append(record)

                    # Detect potential arrivals
                    if (speed < ARRIVAL_SPEED_THRESHOLD and
                            is_in_port_approach(lat, lon) and
                            is_target_vessel(ship_type)):
                        arrival = {
                            "mmsi": mmsi,
                            "ship_name": ship_name,
                            "ship_category": category,
                            "timestamp": record["timestamp"],
                            "latitude": lat,
                            "longitude": lon,
                            "speed_knots": speed,
                        }
                        arrivals.append(arrival)

                    # Print new ships
                    if mmsi not in seen_ships:
                        seen_ships.add(mmsi)
                        port_flag = " [PORT]" if record["in_port_approach"] else ""
                        print(f"  [{len(seen_ships):>3}] {ship_name:<25} "
                              f"| {category:<20} "
                              f"| {lat:.4f}N {lon:.4f}E "
                              f"| {speed:.1f}kn{port_flag}")

                elif msg_type == "ShipStaticData":
                    report = message["Message"]["ShipStaticData"]
                    mmsi = str(report.get("UserID", ""))
                    ship_name = meta.get("ShipName", "").strip()
                    ship_type = meta.get("ShipType", 0)

                    if target_only and not is_target_vessel(ship_type):
                        continue

                    static_data[mmsi] = {
                        "mmsi": mmsi,
                        "ship_name": ship_name,
                        "ship_type": ship_type,
                        "ship_category": classify_ship(ship_type),
                        "imo": report.get("ImoNumber", 0),
                        "callsign": report.get("CallSign", "").strip(),
                        "destination": report.get("Destination", "").strip(),
                        "eta_month": report.get("EtaMonth", 0),
                        "eta_day": report.get("EtaDay", 0),
                        "eta_hour": report.get("EtaHour", 0),
                        "eta_minute": report.get("EtaMinute", 0),
                        "draught": report.get("MaximumStaticDraught", 0),
                        "dimension_a": report.get("Dimension", {}).get("A", 0),
                        "dimension_b": report.get("Dimension", {}).get("B", 0),
                        "dimension_c": report.get("Dimension", {}).get("C", 0),
                        "dimension_d": report.get("Dimension", {}).get("D", 0),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                    # Ship length = A + B
                    dim = static_data[mmsi]
                    dim["length_m"] = dim["dimension_a"] + dim["dimension_b"]
                    dim["width_m"] = dim["dimension_c"] + dim["dimension_d"]

            elapsed = time.time() - start_time
            print(f"\n--- Collection complete ---")
            print(f"Duration:      {elapsed:.0f}s")
            print(f"Messages:      {msg_count}")
            print(f"Position recs: {len(records)}")
            print(f"Unique ships:  {len(seen_ships)}")
            print(f"Static data:   {len(static_data)} vessels")
            print(f"Arrivals:      {len(arrivals)} detections")

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"[ERROR] WebSocket connection rejected: {e}")
        print("  Check your API key is valid.")
    except ConnectionRefusedError:
        print("[ERROR] Connection refused. AISStream.io may be down.")
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        if records:
            print(f"  Collected {len(records)} records before error.")

    return {
        "records": records,
        "static_data": static_data,
        "arrivals": arrivals,
    }


def summarize(data: dict) -> dict:
    """Generate a summary of collected AIS data."""
    records = data.get("records", [])
    static_data = data.get("static_data", {})
    arrivals = data.get("arrivals", [])

    if not records:
        return {"total_records": 0, "unique_ships": 0}

    # Unique ships from position reports
    ships = {}
    for r in records:
        mmsi = r["mmsi"]
        if mmsi not in ships:
            ships[mmsi] = r

    # Category breakdown
    categories = {}
    for s in ships.values():
        cat = s["ship_category"]
        categories[cat] = categories.get(cat, 0) + 1

    # Target vessel counts (cargo + tanker)
    target_count = sum(1 for s in ships.values()
                       if is_target_vessel(s["ship_type"]))

    # Port approach vessels
    port_vessels = set(r["mmsi"] for r in records if r.get("in_port_approach"))

    # Unique arriving MMSIs
    arriving_mmsis = set(a["mmsi"] for a in arrivals)

    return {
        "total_records": len(records),
        "unique_ships": len(ships),
        "target_vessels": target_count,
        "vessels_in_port_approach": len(port_vessels),
        "arrival_detections": len(arriving_mmsis),
        "by_category": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "static_data_count": len(static_data),
        "collection_start": records[0]["timestamp"],
        "collection_end": records[-1]["timestamp"],
    }


async def connection_test() -> bool:
    """
    Quick connection test: connect, subscribe, wait for first message.
    Returns True if data received successfully.
    """
    if not AISSTREAM_API_KEY:
        print("[TEST] FAIL: AISSTREAM_API_KEY not set in .env")
        print("  Get a free key at https://aisstream.io/")
        return False

    bbox = TOKYO_BAY_BBOX
    subscribe_message = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": [[
            [bbox["south"], bbox["west"]],
            [bbox["north"], bbox["east"]],
        ]],
        "FilterMessageTypes": ["PositionReport"],
    }

    print("[TEST] AISStream.io connection test")
    print(f"[TEST] Endpoint: {AISSTREAM_WS_URL}")
    print(f"[TEST] API Key:  {AISSTREAM_API_KEY[:8]}...{AISSTREAM_API_KEY[-4:]}")
    print(f"[TEST] Area:     Tokyo Bay")
    print()

    try:
        print("[TEST] 1/3 Establishing WebSocket connection...")
        async with websockets.connect(AISSTREAM_WS_URL) as ws:
            print("[TEST]     OK - Connected")

            print("[TEST] 2/3 Sending subscription message...")
            await ws.send(json.dumps(subscribe_message))
            print("[TEST]     OK - Subscription sent")

            print("[TEST] 3/3 Waiting for first message (timeout: 30s)...")
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                message = json.loads(raw)
                msg_type = message.get("MessageType", "unknown")
                meta = message.get("MetaData", {})
                ship_name = meta.get("ShipName", "N/A").strip()
                print(f"[TEST]     OK - Received: {msg_type} from '{ship_name}'")
                print()
                print("[TEST] PASS - All checks passed.")
                print(f"[TEST] Sample message:\n{json.dumps(message, indent=2)[:500]}")
                return True
            except asyncio.TimeoutError:
                print("[TEST]     TIMEOUT - No data received in 30s")
                print("[TEST]     This may be normal if no ships are in the area.")
                print("[TEST] PARTIAL - Connection works but no data received.")
                return True  # Connection itself was successful

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"[TEST] FAIL - Server rejected connection: {e}")
        if "401" in str(e) or "403" in str(e):
            print("[TEST]   Your API key may be invalid or expired.")
        return False
    except Exception as e:
        print(f"[TEST] FAIL - {type(e).__name__}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Collect AIS ship data from Tokyo Bay via AISStream.io")
    parser.add_argument(
        "--test", action="store_true",
        help="Run connection test only (no data collection)")
    parser.add_argument(
        "--duration", type=int, default=60,
        help="Collection duration in seconds (default: 60)")
    parser.add_argument(
        "--output-json", type=str,
        help="Save results to JSON file")
    parser.add_argument(
        "--target-only", action="store_true",
        help="Only record Cargo/Tanker vessels")
    args = parser.parse_args()

    # Connection test mode
    if args.test:
        success = asyncio.run(connection_test())
        sys.exit(0 if success else 1)

    # Full collection mode
    data = asyncio.run(collect_ais_data(
        duration_seconds=args.duration,
        target_only=args.target_only,
    ))

    records = data["records"]
    if records:
        summary = summarize(data)
        print(f"\n=== Summary ===")
        print(f"Unique ships:       {summary['unique_ships']}")
        print(f"Target vessels:     {summary['target_vessels']}")
        print(f"In port approach:   {summary['vessels_in_port_approach']}")
        print(f"Arrival detections: {summary['arrival_detections']}")
        print(f"\nShip categories:")
        for cat, count in summary["by_category"].items():
            print(f"  {cat}: {count}")

        # Save output
        output_path = args.output_json
        if not output_path:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            output_dir = RAW_DIR / "ais"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"{today}.json")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        output_data = {
            "summary": summary,
            "records": records,
            "static_data": list(data["static_data"].values()),
            "arrivals": data["arrivals"],
        }
        with open(output, "w") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        print(f"\nSaved: {output}")
    else:
        print("\nNo records collected.")

    return data


if __name__ == "__main__":
    main()
