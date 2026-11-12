"""Deterministic exact branch-and-bound load-shedding optimizer."""
from decimal import Decimal


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def flatten_transformers(topology):
    """Flatten nested feeder/lateral payloads into stable transformer records."""
    flattened = []
    roots = topology if isinstance(topology, (list, tuple)) else [topology]

    def visit(node):
        if not isinstance(node, dict):
            return

        if node.get("node_type") == "transformer" or "load_kw" in node:
            load = node.get("load_kw", node.get("load", 0))
            if isinstance(load, dict):
                load = load.get("value", 0)

            recovery = node.get("recovery_ratio")
            if recovery is None:
                bill_recovery = node.get("bill_recovery", 0)
                if isinstance(bill_recovery, dict):
                    recovery = bill_recovery.get("value", 0)
                    if bill_recovery.get("unit") == "%":
                        recovery = _decimal(recovery) / Decimal(100)
                else:
                    recovery = bill_recovery

            constraint = node.get("constraint") or {}
            priority = str(node.get("priority", "")).strip().upper()
            critical = bool(node.get("is_critical", False)) or priority == "HARD CRITICAL"
            critical = critical or (
                str(constraint.get("type", "")).lower() == "hard"
                and constraint.get("value") == 1
            )
            load = _decimal(load)
            recovery = _decimal(recovery)
            if load < 0:
                raise ValueError("Transformer load cannot be negative")
            if recovery < 0:
                raise ValueError("Recovery ratio cannot be negative")

            flattened.append({
                "id": str(node["id"]),
                "load_kw": load,
                "recovery_ratio": recovery,
                "is_critical": critical,
            })
            return

        for child in sorted(node.get("children", []), key=lambda item: str(item.get("id", ""))):
            visit(child)

    for root in sorted(roots, key=lambda item: str(item.get("id", ""))):
        visit(root)
    return sorted(flattened, key=lambda item: item["id"])


def exact_dispatch(deficit_kw, transformers, outage_counts=None, fairness_penalty=15.0):
    """Globally minimize adjusted shed cost subject to the exact deficit floor.

    All load and cost comparisons use Decimal-scaled integers. Ties prefer
    fewer transformers shed, then lexicographically smaller transformer IDs.
    """
    transformers = flatten_transformers(transformers)
    outage_counts = outage_counts or {}
    discretionary = [item for item in transformers if not item["is_critical"]]
    requested = max(Decimal(0), _decimal(deficit_kw))
    maximum_safe = sum((item["load_kw"] for item in discretionary), Decimal(0))
    target = min(requested, maximum_safe)
    result = {item["id"]: True for item in transformers}

    if target == 0 or not discretionary:
        return {
            "result": result,
            "info": {
                "requested_shed_kw": float(requested),
                "achievable_shed_kw": 0.0,
                "requested_deficit_met": requested == 0,
                "maximum_safe_shed_kw": float(maximum_safe),
                "revenue_lost": 0.0,
                "solver_status": "Exact: no discretionary shedding required",
            },
        }

    penalty = _decimal(fairness_penalty)
    if penalty < 0:
        raise ValueError("Fairness penalty cannot be negative")

    load_values = [item["load_kw"] for item in discretionary] + [target]
    load_precision = max(max(0, -value.as_tuple().exponent) for value in load_values)
    load_scale = 10 ** load_precision
    loads = [int(item["load_kw"] * load_scale) for item in discretionary]
    target_units = int(target * load_scale)

    adjusted_costs = []
    revenue_costs = []
    for item in discretionary:
        revenue_cost = item["load_kw"] * item["recovery_ratio"]
        historical_outages = int(outage_counts.get(item["id"], 0))
        if historical_outages < 0:
            raise ValueError("Outage counts cannot be negative")
        revenue_costs.append(revenue_cost)
        adjusted_costs.append(revenue_cost + penalty * historical_outages)

    cost_precision = max(max(0, -value.as_tuple().exponent) for value in adjusted_costs)
    cost_scale = 10 ** cost_precision
    costs = [int(value * cost_scale) for value in adjusted_costs]
    n = len(discretionary)

    best_cost = None
    best_bits = None
    best_shed = None
    best_ids = None
    visited = 0

    def search(index, shed_units, cost_units, bits, selected_ids):
        nonlocal best_cost, best_bits, best_shed, best_ids, visited
        visited += 1

        if best_cost is not None and cost_units > best_cost:
            return
        if index == n:
            if shed_units < target_units:
                return
            selected_key = tuple(selected_ids)
            selected_count = len(selected_ids)
            best_count = len(best_ids) if best_ids is not None else None
            if (
                best_cost is None
                or cost_units < best_cost
                or (cost_units == best_cost and selected_count < best_count)
                or (
                    cost_units == best_cost
                    and selected_count == best_count
                    and selected_key < best_ids
                )
            ):
                best_cost = cost_units
                best_bits = tuple(bits)
                best_shed = shed_units
                best_ids = selected_key
            return

        if shed_units + sum(loads[index:]) < target_units:
            return

        # Visit OFF first to find a low-cost feasible upper bound early.
        bits.append(1)
        selected_ids.append(discretionary[index]["id"])
        search(index + 1, shed_units + loads[index], cost_units + costs[index], bits, selected_ids)
        selected_ids.pop()
        bits.pop()

        bits.append(0)
        search(index + 1, shed_units, cost_units, bits, selected_ids)
        bits.pop()

    search(0, 0, 0, [], [])
    if best_bits is None:
        raise RuntimeError("No feasible load-shedding schedule found")

    for index, item in enumerate(discretionary):
        result[item["id"]] = best_bits[index] == 0

    selected_revenue_lost = sum(
        (revenue_costs[index] for index, bit in enumerate(best_bits) if bit),
        Decimal(0),
    )
    achieved_kw = Decimal(best_shed) / Decimal(load_scale)
    objective_cost = Decimal(best_cost) / Decimal(cost_scale)
    info = {
        "requested_shed_kw": float(requested),
        "achievable_shed_kw": float(achieved_kw),
        "requested_deficit_met": achieved_kw >= requested,
        "maximum_safe_shed_kw": float(maximum_safe),
        "revenue_lost": float(selected_revenue_lost),
        "adjusted_objective_cost": float(objective_cost),
        "historical_outage_counts": {
            item["id"]: int(outage_counts.get(item["id"], 0)) for item in discretionary
        },
        "fairness_penalty_per_prior_outage": float(penalty),
        "selected_shed_bits": list(best_bits),
        "selected_transformers": list(best_ids),
        "visited_search_nodes": visited,
        "solver_status": "Exact global optimum (Decimal branch and bound)",
    }
    if requested > maximum_safe:
        info["note"] = "Requested deficit exceeds discretionary load; critical transformers remain ON"
        info["solver_status"] = "Critical protection applied; requested deficit infeasible"

    return {"result": result, "info": info}
