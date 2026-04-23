"""
Distance Matrix Generator using OSRM API.

This module reads site telemetry data, appends a central depot, and queries the
public Open Source Routing Machine (OSRM) API to construct an NxN distance matrix.
Due to URL length and API constraints, requests are divided into chunks and throttled
to comply with public API rate limits.
"""

import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List

# --- Configuration ---
TELEMETRY_FILE: str = "PATH_TO/telemetry.json"
OUTPUT_MATRIX_FILE: str = "PATH_TO/distance_matrix.json"

DEPOT: Dict[str, Any] = {"site_id": "DEPOT-0", "lat": 55.7558, "lon": 37.6173}
OSRM_BASE_URL: str = "http://router.project-osrm.org/table/v1/driving/"

CHUNK_SIZE: int = 50
REQUEST_DELAY_SEC: float = 1.5


def build_osrm_matrix() -> None:
    """
    Builds an NxN driving distance matrix by querying the OSRM API in chunks.
    
    Reads site coordinates from the telemetry file, prepends the depot node,
    and constructs the matrix row by row and column by column. The resulting
    matrix and its index map are serialized to a JSON file for the solver.
    """
    if not os.path.exists(TELEMETRY_FILE):
        print(f"[!] Error: File not found at {TELEMETRY_FILE}")
        return

    with open(TELEMETRY_FILE, "r", encoding="utf-8") as f:
        telemetry: List[Dict[str, Any]] = json.load(f)

    points: List[Dict[str, Any]] = [DEPOT] + telemetry
    index_map: List[str] = [p["site_id"] for p in points]
    num_points: int = len(points)
    
    # Initialize an NxN matrix with zeros
    matrix: List[List[int]] = [[0 for _ in range(num_points)] for _ in range(num_points)]

    print(f"[*] Downloading routing distances (OSRM) for {num_points} nodes...")
    
    for i in range(0, num_points, CHUNK_SIZE):
        for j in range(0, num_points, CHUNK_SIZE):
            sources = points[i:i + CHUNK_SIZE]
            destinations = points[j:j + CHUNK_SIZE]

            request_points = sources + destinations
            coords_str = ";".join([f"{p['lon']},{p['lat']}" for p in request_points])
            
            src_len = len(sources)
            dst_len = len(destinations)
            
            src_indices = ";".join(str(idx) for idx in range(src_len))
            dst_indices = ";".join(str(idx) for idx in range(src_len, src_len + dst_len))

            # Explicitly request distance annotations
            url = f"{OSRM_BASE_URL}{coords_str}?sources={src_indices}&destinations={dst_indices}&annotations=distance"
            
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except Exception as e:
                print(f"\n[!] Network or OSRM API Error: {e}")
                return

            distances = data.get("distances", [])
            
            # Safeguard: Verify that the server returned actual distance data
            if not distances:
                print(f"\n[!] Server did not return distances. Response keys: {list(data.keys())}")
                return

            for row_idx, dist_row in enumerate(distances):
                for col_idx, dist_val in enumerate(dist_row):
                    # Use a large penalty value (999999) for unreachable nodes instead of None
                    val = int(dist_val) if dist_val is not None else 999999
                    matrix[i + row_idx][j + col_idx] = val

            # Throttle requests to prevent IP blocking by the public OSRM server
            time.sleep(REQUEST_DELAY_SEC)
            print(".", end="", flush=True)

    print("\n[*] Matrix construction completed.")
    result: Dict[str, Any] = {"index_map": index_map, "distances": matrix}

    with open(OUTPUT_MATRIX_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4, ensure_ascii=False)


if __name__ == "__main__":
    build_osrm_matrix()