import math
from decimal import Decimal
import numpy as np
from scipy.optimize import milp, Bounds, LinearConstraint


def flatten_transformers(topology):
    """Flatten a feeder/lateral tree to solver-ready transformer records."""
    flattened = []
    roots = topology if isinstance(topology, (list, tuple)) else [topology]

    def visit(node):
        if not isinstance(node, dict):
            return

        is_transformer = node.get("node_type") == "transformer" or "load_kw" in node
        if is_transformer:
            load = node.get("load_kw", node.get("load", 0))
            if isinstance(load, dict):
                load = load.get("value", 0)

            recovery = node.get("recovery_ratio")
            if recovery is None:
                bill_recovery = node.get("bill_recovery", 0)
                if isinstance(bill_recovery, dict):
                    recovery = bill_recovery.get("value", 0)
                    if bill_recovery.get("unit") == "%":
                        recovery = float(recovery) / 100.0
                else:
                    recovery = bill_recovery

            constraint = node.get("constraint") or {}
            priority = str(node.get("priority", "")).strip().upper()
            is_critical = bool(node.get("is_critical", False)) or priority == "HARD CRITICAL"
            is_critical = is_critical or (
                str(constraint.get("type", "")).lower() == "hard"
                and constraint.get("value") == 1
            )
            flattened.append({
                "id": node["id"],
                "load_kw": float(load),
                "recovery_ratio": float(recovery),
                "is_critical": is_critical,
            })
            return

        for child in node.get("children", []):
            visit(child)

    for root in roots:
        visit(root)
    return flattened


def classical_dispatch(deficit_kw, transformers, outage_counts=None, fairness_penalty=15.0):
    """Minimize revenue lost while meeting a minimum shed-load target

    using classical Mixed-Integer Linear Programming (MILP).
    """
    transformers = flatten_transformers(transformers)
    outage_counts = outage_counts or {}
    discretionary = [t for t in transformers if not t["is_critical"]]
    discretionary_load_decimal = sum((Decimal(str(t["load_kw"])) for t in discretionary), Decimal(0))
    discretionary_load = float(discretionary_load_decimal)
    requested_deficit = float(max(Decimal(0), Decimal(str(deficit_kw))))
    target_deficit = float(min(requested_deficit, discretionary_load))
    
    result = {t["id"]: (1 if t["is_critical"] else 0) for t in transformers}

    if target_deficit == 0 or not discretionary:
        return result, {
            "requested_shed_kw": requested_deficit,
            "achievable_shed_kw": 0.0,
            "requested_deficit_met": requested_deficit == 0,
            "maximum_safe_shed_kw": discretionary_load,
            "revenue_lost": 0.0,
            "solver_status": "No discretionary shedding required (Classical Optimal)",
        }

    n = len(discretionary)
    
    # 1. Calculate objective coefficients (Cost = Revenue Lost + Fairness Penalty)
    revenue_costs = [float(t["load_kw"]) * float(t["recovery_ratio"]) for t in discretionary]
    adjusted_costs = np.array([
        revenue_cost + fairness_penalty * int(outage_counts.get(t["id"], 0))
        for t, revenue_cost in zip(discretionary, revenue_costs)
    ])

    # 2. Setup MILP: Minimize dot(adjusted_costs, x) subject to sum(loads * x) >= target_deficit
    # Where x_i in {0, 1} (1 = shed transformer, 0 = keep on)
    loads = np.array([t["load_kw"] for t in discretionary])
    
    # Linear constraint: sum(loads[i] * x[i]) >= target_deficit
    A = np.array([loads])
    constraints = LinearConstraint(A, lb=target_deficit, ub=np.inf)
    
    # Bounds: binary variables (0 or 1)
    integrality = np.ones(n)
    bounds = Bounds(lb=0, ub=1)

    # Solve using SciPy's HiGHS MILP solver
    res = milp(c=adjusted_costs, integrality=integrality, bounds=bounds, constraints=constraints)

    if not res.success:
        raise RuntimeError("Classical MILP solver failed to find a feasible load shedding schedule.")

    selected_bits = np.round(res.x).astype(int)

    for i, transformer in enumerate(discretionary):
        result[transformer["id"]] = 0 if selected_bits[i] else 1

    shed_kw = float(np.dot(loads, selected_bits))
    revenue_lost = float(np.dot(revenue_costs, selected_bits))

    info = {
        "requested_shed_kw": requested_deficit,
        "achievable_shed_kw": shed_kw,
        "requested_deficit_met": shed_kw + 1e-9 >= requested_deficit,
        "maximum_safe_shed_kw": discretionary_load,
        "revenue_lost": revenue_lost,
        "historical_outage_counts": {t["id"]: int(outage_counts.get(t["id"], 0)) for t in discretionary},
        "fairness_penalty_per_prior_outage": fairness_penalty,
        "variables": n,
        "selected_shed_bits": [int(bit) for bit in selected_bits],
        "solver_status": "Classical MILP Optimal Convergence",
    }
    
    if requested_deficit > discretionary_load:
        info["note"] = "Requested deficit exceeds discretionary load; critical transformers remain ON"
        info["solver_status"] = "Critical protection applied; requested deficit infeasible"
        
    return result, info


if __name__ == "__main__":
    data = {
        "deficit_kw": 116.0,
        "transformers": [
            {"id": "T1_Hospital", "load_kw": 150.0, "recovery_ratio": 0.98, "is_critical": True},
            {"id": "T2_Commercial", "load_kw": 200.0, "recovery_ratio": 0.25, "is_critical": False},
            {"id": "T3_HonestRes", "load_kw": 180.0, "recovery_ratio": 0.92, "is_critical": False},
            {"id": "T4_LossZone", "load_kw": 120.0, "recovery_ratio": 0.15, "is_critical": False},
        ],
    }
    out, info = classical_dispatch(data["deficit_kw"], data["transformers"])
    print("Dispatch Result:", out)
    print("Metadata:", info)
