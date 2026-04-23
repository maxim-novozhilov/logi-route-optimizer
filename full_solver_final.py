"""
Vehicle Routing Problem (VRP) Solver for Fuel Logistics.

This module leverages Google OR-Tools to compute optimal fuel delivery routes
based on telemetry data, distance matrices, and fleet availability. It enforces
strict delivery deadlines and supports a fallback mode to selectively skip
sites using penalty costs if routing constraints are otherwise unsatisfiable.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from ortools.constraint_solver import pywrapcp, routing_enums_pb2


# System Configuration
DATA_DIR: str = "mock_data"
TELEMETRY_FILE: str = os.path.join(DATA_DIR, "telemetry.json")
FLEET_FILE: str = os.path.join(DATA_DIR, "fleet.json")
DISTANCE_MATRIX_FILE: str = os.path.join(DATA_DIR, "distance_matrix.json")
OVERRIDES_FILE: str = os.path.join(DATA_DIR, "dispatch_overrides.json")
ROUTE_SOLUTION_FILE: str = os.path.join(DATA_DIR, "route_solution.json")

# Physical & Time Constants
SPEED_MPS: float = 11.0          # Average vehicle speed in meters per second (~40 km/h)
PLAN_HORIZON_SEC: int = 172800   # Maximum planning horizon (48 hours)
WAIT_SLACK_SEC: int = 7200       # Maximum allowed waiting time at a node (buffer)
TIME_LIMIT_SEC: int = 10         # Maximum execution time limit for the solver

# Routing Policy & Penalty Weights
STRICT_INCLUDE_OPPORTUNISTIC: bool = True
STRICT_OPPORTUNISTIC_PENALTY: int = 500
FALLBACK_PENALTY_CRITICAL: int = 10_000_000
FALLBACK_PENALTY_PLANNED: int = 1_000_000
FALLBACK_PENALTY_OPPORTUNISTIC: int = 500


def save_result(payload: Dict[str, Any]) -> None:
    """
    Saves the computed routing solution or error state to a JSON file.

    Args:
        payload (Dict[str, Any]): The data dictionary to be serialized.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ROUTE_SOLUTION_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)


def load_json(path: str, required: bool = True) -> Optional[Any]:
    """
    Loads JSON data from a given filepath.

    Args:
        path (str): The filepath to the JSON file.
        required (bool): If True, raises FileNotFoundError if the file is missing.
                         If False, returns None instead.

    Returns:
        Optional[Any]: The parsed JSON data or None.
    
    Raises:
        FileNotFoundError: If the file is missing and 'required' is True.
    """
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(path)
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def meters_to_minutes(distance_m: int) -> float:
    """
    Converts distance in meters to travel time in minutes based on SPEED_MPS.

    Args:
        distance_m (int): Distance in meters.

    Returns:
        float: Equivalent travel time in minutes.
    """
    return distance_m / SPEED_MPS / 60.0


def neutral_drop_reason(priority: str, mode: str) -> str:
    """
    Determines the standardized tracking reason for dropping a site from the route.

    Args:
        priority (str): The operational priority of the site (e.g., "critical").
        mode (str): The active solver mode ("STRICT" or "FALLBACK").

    Returns:
        str: A standardized categorical string defining the drop reason.
    """
    if mode == "STRICT":
        return "OPTIONAL_SKIPPED_IN_STRICT"
    if priority == "critical":
        return "CRITICAL_UNASSIGNED_IN_FALLBACK"
    if priority == "planned":
        return "PLANNED_UNASSIGNED_IN_FALLBACK"
    return "OPTIONAL_UNASSIGNED_IN_FALLBACK"


def build_problem() -> Dict[str, Any]:
    """
    Aggregates input datasets (telemetry, fleet, distance matrix, overrides) and
    prepares the routing problem definition.
    
    This function handles input validation, filters out irrelevant sites (e.g., 
    blocked by dispatcher or nominal sites), calculates net demand, and asserts 
    arrival deadlines. It also computes initial diagnostics to identify logically 
    unreachable sites or capacity constraint violations prior to running the solver.

    Returns:
        Dict[str, Any]: A dictionary containing the structured node list, distance
                        matrix, fleet configurations, and diagnostic statuses.
    """
    telemetry = load_json(TELEMETRY_FILE)
    fleet_cfg = load_json(FLEET_FILE)
    dist_raw = load_json(DISTANCE_MATRIX_FILE)
    overrides = load_json(OVERRIDES_FILE, required=False) or {"blocked_towers": [], "force_critical": []}

    if not isinstance(telemetry, list):
        raise ValueError("telemetry.json must contain a list of site objects.")
    if "vehicles" not in fleet_cfg or not isinstance(fleet_cfg["vehicles"], list):
        raise ValueError("fleet.json must contain a 'vehicles' list.")
    if "index_map" not in dist_raw or "distances" not in dist_raw:
        raise ValueError("distance_matrix.json must contain 'index_map' and 'distances'.")

    active_fleet = [v for v in fleet_cfg["vehicles"] if v.get("status") == "active"]
    if not active_fleet:
        return {
            "status": "no_fleet_active",
            "blocked_by_dispatcher": overrides.get("blocked_towers", []),
            "message": "No active vehicles found in fleet configuration."
        }

    blocked = set(overrides.get("blocked_towers", []))
    force_critical = set(overrides.get("force_critical", []))
    matrix_index_map = dist_raw["index_map"]

    towers: List[Dict[str, Any]] = []
    skipped_nominal: List[str] = []
    skipped_by_policy: List[str] = []
    blocked_by_dispatcher: List[str] = []

    for t in telemetry:
        sid = t["site_id"]
        
        if sid in blocked:
            blocked_by_dispatcher.append(sid)
            continue

        priority = "critical" if sid in force_critical else t["solver_priority"]
        if priority == "nominal":
            skipped_nominal.append(sid)
            continue

        if priority == "opportunistic" and not STRICT_INCLUDE_OPPORTUNISTIC:
            skipped_by_policy.append(sid)
            continue

        if sid not in matrix_index_map:
            raise ValueError(f"Site '{sid}' not found in distance matrix index_map.")

        tank_capacity = float(t["tank_capacity_liters"])
        fuel_current = float(t["fuel_current_liters"])
        demand = max(0, int(round(tank_capacity - fuel_current)))
        service_time = int(t.get("service_time_sec", 1800))
        deadline = int(t["tte_deadline_sec"])

        # Operational requirement: Service must be completed before the dry-out deadline.
        # Therefore, the maximum arrival time is (deadline - service_time).
        arrival_deadline = max(0, deadline - service_time)

        towers.append({
            "site_id": sid,
            "priority": priority,
            "demand": demand,
            "deadline": deadline,
            "service_time": service_time,
            "arrival_deadline": arrival_deadline,
        })

    if not towers:
        return {
            "status": "no_work_needed",
            "blocked_by_dispatcher": blocked_by_dispatcher,
            "skipped_nominal": skipped_nominal,
            "skipped_by_policy": skipped_by_policy,
            "message": "No valid sites require routing based on current telemetry."
        }

    node_names = ["DEPOT-0"] + [t["site_id"] for t in towers]
    indices = [matrix_index_map.index(name) for name in node_names]
    dist_matrix = [[int(dist_raw["distances"][i][j]) for j in indices] for i in indices]

    total_capacity = sum(int(v["capacity_liters"]) for v in active_fleet)
    total_mandatory_demand = sum(
        t["demand"] for t in towers if t["priority"] in ("critical", "planned")
    )
    max_vehicle_capacity = max(int(v["capacity_liters"]) for v in active_fleet)

    unreachable_from_depot = []
    overweight_for_single_vehicle = []
    
    for idx, tower in enumerate(towers, start=1):
        direct_arrival_sec = int(dist_matrix[0][idx] / SPEED_MPS)
        if direct_arrival_sec > tower["arrival_deadline"]:
            unreachable_from_depot.append({
                "site_id": tower["site_id"],
                "priority": tower["priority"],
                "direct_arrival_min": round(direct_arrival_sec / 60, 1),
                "arrival_deadline_min": round(tower["arrival_deadline"] / 60, 1)
            })
        if tower["demand"] > max_vehicle_capacity:
            overweight_for_single_vehicle.append({
                "site_id": tower["site_id"],
                "priority": tower["priority"],
                "demand_liters": tower["demand"],
                "max_vehicle_capacity_liters": max_vehicle_capacity,
            })

    return {
        "status": "ok",
        "node_names": node_names,
        "dist_matrix": dist_matrix,
        "towers": towers,
        "fleet": active_fleet,
        "blocked_by_dispatcher": blocked_by_dispatcher,
        "skipped_nominal": skipped_nominal,
        "skipped_by_policy": skipped_by_policy,
        "diagnostics": {
            "total_active_fleet_capacity_liters": total_capacity,
            "total_mandatory_demand_liters": total_mandatory_demand,
            "mandatory_demand_exceeds_total_capacity": total_mandatory_demand > total_capacity,
            "unreachable_from_depot": unreachable_from_depot,
            "overweight_for_single_vehicle": overweight_for_single_vehicle,
        }
    }


def solve_with_mode(data: Dict[str, Any], fallback: bool = False) -> Tuple[Any, Any, Any]:
    """
    Initializes and solves the Vehicle Routing Problem using Google OR-Tools.
    
    This function sets up distance and time dimensions. It defines node-specific 
    time windows (using arrival deadlines) and vehicle capacities. 
    If 'fallback' mode is active, it adds disjunctions to all nodes, allowing 
    the solver to drop nodes (at a penalty cost) to yield a valid partial route.

    Args:
        data (Dict[str, Any]): The structured problem definition.
        fallback (bool): If True, allows the solver to skip nodes using penalties.

    Returns:
        Tuple[Assignment, RoutingModel, RoutingIndexManager]: The solver's solution
        object, alongside the routing model and index manager for value extraction.
    """
    num_nodes = len(data["node_names"])
    num_vehicles = len(data["fleet"])

    manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    # 1. Distance Dimension
    def dist_cb(i: int, j: int) -> int:
        from_index = manager.IndexToNode(i)
        to_index = manager.IndexToNode(j)
        return data["dist_matrix"][from_index][to_index]

    transit_dist_idx = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_dist_idx)

    # 2. Time Dimension
    def time_cb(i: int, j: int) -> int:
        from_node = manager.IndexToNode(i)
        to_node = manager.IndexToNode(j)
        travel_time = data["dist_matrix"][from_node][to_node] / SPEED_MPS
        service_time = 0 if from_node == 0 else data["towers"][from_node - 1]["service_time"]
        return int(travel_time + service_time)

    transit_time_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(
        transit_time_idx,
        WAIT_SLACK_SEC,
        PLAN_HORIZON_SEC,
        True,
        "Time"
    )
    time_dim = routing.GetDimensionOrDie("Time")

    for v in range(num_vehicles):
        time_dim.CumulVar(routing.Start(v)).SetRange(0, 0)
        time_dim.CumulVar(routing.End(v)).SetRange(0, PLAN_HORIZON_SEC)
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.Start(v)))
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(v)))

    # Enforce time windows based on calculated arrival deadlines
    for node_idx in range(1, num_nodes):
        tower = data["towers"][node_idx - 1]
        time_dim.CumulVar(manager.NodeToIndex(node_idx)).SetRange(0, int(tower["arrival_deadline"]))

    # 3. Capacity Dimension
    def demand_cb(i: int) -> int:
        node = manager.IndexToNode(i)
        return 0 if node == 0 else data["towers"][node - 1]["demand"]

    routing.AddDimensionWithVehicleCapacity(
        routing.RegisterUnaryTransitCallback(demand_cb),
        0,  # null capacity slack
        [int(v["capacity_liters"]) for v in data["fleet"]],
        True,
        "Capacity",
    )

    # 4. Disjunctions (Node skipping logic)
    for node_idx in range(1, num_nodes):
        tower = data["towers"][node_idx - 1]
        index = manager.NodeToIndex(node_idx)

        if fallback:
            if tower["priority"] == "critical":
                penalty = FALLBACK_PENALTY_CRITICAL
            elif tower["priority"] == "planned":
                penalty = FALLBACK_PENALTY_PLANNED
            else:
                penalty = FALLBACK_PENALTY_OPPORTUNISTIC
            routing.AddDisjunction([index], penalty)
        else:
            if tower["priority"] == "opportunistic":
                routing.AddDisjunction([index], STRICT_OPPORTUNISTIC_PENALTY)
            # Critical and Planned remain strictly mandatory in this mode.

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.FromSeconds(TIME_LIMIT_SEC)

    solution = routing.SolveWithParameters(search_params)
    return solution, routing, manager


def build_success_result(
    data: Dict[str, Any], 
    solution: Any, 
    routing: Any, 
    manager: Any, 
    mode: str
) -> Dict[str, Any]:
    """
    Parses the OR-Tools solution assignment and constructs a standardized JSON
    payload detailing the optimal vehicle routes, dropped nodes, and system warnings.

    Args:
        data (Dict[str, Any]): The original problem definition.
        solution (Assignment): The assigned solution output from OR-Tools.
        routing (RoutingModel): The OR-Tools routing model.
        manager (RoutingIndexManager): The node index manager.
        mode (str): The execution mode ("STRICT" or "FALLBACK").

    Returns:
        Dict[str, Any]: The finalized output structure ready for serialization.
    """
    time_dim = routing.GetDimensionOrDie("Time")
    seen_sites = set()

    result: Dict[str, Any] = {
        "status": "success",
        "optimization_mode": mode,
        "vehicles": [],
        "dropped_by_solver": [],
        "blocked_by_dispatcher": data["blocked_by_dispatcher"],
        "skipped_nominal": data["skipped_nominal"],
        "skipped_by_policy": data["skipped_by_policy"],
        "diagnostics": data["diagnostics"],
        "warnings": []
    }

    for v_idx, vehicle in enumerate(data["fleet"]):
        route_steps: List[Dict[str, Any]] = []
        route_distance_m = 0
        route_delivered_liters = 0

        index = routing.Start(v_idx)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            site_id = data["node_names"][node]
            arrival_sec = solution.Value(time_dim.CumulVar(index))

            if node == 0:
                departure_sec = arrival_sec
                deadline_min = None
                priority = "DEPOT"
                delivered = 0
                service_time_sec = 0
                completion_deadline_min = None
                arrival_slack_min = None
            else:
                tower = data["towers"][node - 1]
                priority = tower["priority"]
                delivered = tower["demand"]
                service_time_sec = tower["service_time"]
                departure_sec = arrival_sec + service_time_sec
                
                deadline_min = round(tower["arrival_deadline"] / 60, 1)
                completion_deadline_min = round(tower["deadline"] / 60, 1)
                arrival_slack_min = round((tower["arrival_deadline"] - arrival_sec) / 60, 1)
                
                seen_sites.add(site_id)
                route_delivered_liters += delivered

            route_steps.append({
                "site_id": site_id,
                "priority": priority,
                "arrival_min": round(arrival_sec / 60, 1),
                "departure_min": round(departure_sec / 60, 1),
                "arrival_deadline_min": deadline_min,
                "completion_deadline_min": completion_deadline_min,
                "arrival_slack_min": arrival_slack_min,
                "service_time_min": round(service_time_sec / 60, 1),
                "delivered_liters": delivered,
            })

            prev_index = index
            index = solution.Value(routing.NextVar(index))
            route_distance_m += routing.GetArcCostForVehicle(prev_index, index, v_idx)

        end_arrival_sec = solution.Value(time_dim.CumulVar(index))
        route_steps.append({
            "site_id": "DEPOT-0",
            "priority": "DEPOT",
            "arrival_min": round(end_arrival_sec / 60, 1),
            "departure_min": round(end_arrival_sec / 60, 1),
            "arrival_deadline_min": None,
            "completion_deadline_min": None,
            "arrival_slack_min": None,
            "service_time_min": 0.0,
            "delivered_liters": 0,
        })

        result["vehicles"].append({
            "vehicle_id": vehicle["vehicle_id"],
            "total_distance_km": round(route_distance_m / 1000, 3),
            "total_delivered_liters": route_delivered_liters,
            "route_duration_min": round(end_arrival_sec / 60, 1),
            "steps": route_steps,
        })

    planned_sites = {tower["site_id"] for tower in data["towers"]}
    dropped_sites = sorted(planned_sites - seen_sites)
    
    for sid in dropped_sites:
        tower = next(t for t in data["towers"] if t["site_id"] == sid)
        result["dropped_by_solver"].append({
            "site_id": sid,
            "priority": tower["priority"],
            "status": neutral_drop_reason(tower["priority"], mode),
            "arrival_deadline_min": round(tower["arrival_deadline"] / 60, 1),
            "completion_deadline_min": round(tower["deadline"] / 60, 1),
            "demand_liters": tower["demand"],
        })

    if mode == "FALLBACK" and result["dropped_by_solver"]:
        result["warnings"].append(
            "Strict constraints failed. Evaluated a compromise route; some sites remain unassigned."
        )

    if data["diagnostics"]["mandatory_demand_exceeds_total_capacity"]:
        result["warnings"].append(
            "Total mandatory demand exceeds the cumulative capacity of the active fleet."
        )

    if data["diagnostics"]["unreachable_from_depot"]:
        result["warnings"].append(
            "Several targets are unreachable due to absolute time/distance constraints."
        )

    return result


def main() -> None:
    """
    Main entry point. Orchestrates the pipeline: reads dependencies, executes 
    routing optimization under STRICT enforcement, attempts FALLBACK evaluation 
    upon failure, and flushes results sequentially to storage.
    """
    try:
        data = build_problem()

        if data["status"] != "ok":
            save_result(data)
            return

        solution, routing, manager = solve_with_mode(data, fallback=False)
        mode = "STRICT"

        if not solution:
            solution, routing, manager = solve_with_mode(data, fallback=True)
            mode = "FALLBACK"

        if not solution:
            save_result({
                "status": "no_solution",
                "optimization_mode": "STRICT+FALLBACK",
                "blocked_by_dispatcher": data["blocked_by_dispatcher"],
                "skipped_nominal": data["skipped_nominal"],
                "skipped_by_policy": data["skipped_by_policy"],
                "diagnostics": data["diagnostics"],
                "message": "Critical routing failure: Unable to compute a valid route even in fallback mode."
            })
            return

        save_result(build_success_result(data, solution, routing, manager, mode))
        print(f"[✓] Computation finished. Mode: {mode}. Output file: {ROUTE_SOLUTION_FILE}")

    except Exception as e:
        save_result({
            "status": "critical_error",
            "message": str(e)
        })
        print(f"[!] Error: {e}")


if __name__ == "__main__":
    main()