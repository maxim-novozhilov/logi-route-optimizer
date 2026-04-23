"""
Telemetry Parser and SLA Simulator.

This module reads raw infrastructure data from a CSV file, calculates simulated
fuel consumption rates based on defined SLA rules, and computes the absolute
Time To Empty (TTE) in seconds for each site. It classifies sites into strict
priority tiers and outputs a normalized JSON file for the routing solver.
"""

import json
import os
from typing import Any, Dict, List

import pandas as pd

# --- System Configuration ---
INPUT_CSV: str = "PATH_TO/mock_data/sites_100.csv"
OUTPUT_JSON: str = "PATH_TO/mock_data/telemetry.json"

# Simulated consumption rule: 50% of tank capacity is consumed in this many hours
SLA_HALF_TANK_HOURS: float = 24.0


def parse_csv_to_telemetry(csv_path: str, output_path: str) -> None:
    """
    Parses raw site CSV data, calculates TTE deadlines based on SLA rules,
    assigns priority tiers, and exports the normalized dataset to JSON.

    Args:
        csv_path (str): Absolute path to the input CSV file.
        output_path (str): Absolute path for the generated JSON telemetry.
    """
    if not os.path.exists(csv_path):
        print(f"[!] Error: File not found at {csv_path}")
        return

    df = pd.read_csv(csv_path)
    telemetry: List[Dict[str, Any]] = []
    
    for _, row in df.iterrows():
        cap: float = float(row['tank_capacity'])
        fuel: float = float(row['current_level'])
        fuel_pct: float = fuel / cap
        
        # Calculate simulated burn rate (liters per second) based on SLA
        burn_rate_ext: float = (cap / 2.0) / (SLA_HALF_TANK_HOURS * 3600.0)
        
        # Calculate Time To Empty (TTE) in seconds
        tte_sec: int = int(fuel / burn_rate_ext)
        
        # Priority classification based on remaining fuel percentage
        if fuel_pct < 0.3:
            prio = "critical"
        elif fuel_pct < 0.5:
            prio = "planned"
        elif fuel_pct < 0.8:
            prio = "opportunistic"
        else:
            prio = "nominal"
            
        telemetry.append({
            "site_id": str(row['site_id']),
            "lat": float(row['lat']),
            "lon": float(row['lon']),
            "fuel_current_liters": round(fuel, 2),
            "tank_capacity_liters": cap,
            "fuel_pct": round(fuel_pct, 3),
            "solver_priority": prio,
            "tte_deadline_sec": tte_sec,
            "service_time_sec": int(row['service_time_sec'])
        })
    
    # Ensure the target directory exists before saving
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=4, ensure_ascii=False)

    print(f"[✓] Successfully processed {len(telemetry)} sites. File saved to: {output_path}")


if __name__ == "__main__":
    parse_csv_to_telemetry(INPUT_CSV, OUTPUT_JSON)