"""QAOA load shedding with critical protection and outage fairness.

Nested feeder data is flattened to transformer leaves. Qiskit Estimator and
Sampler primitives drive parameter optimization and final dispatch selection.
"""
import math
from decimal import Decimal
from functools import reduce
import numpy as np
from scipy.optimize import minimize


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


def build_qubo(items, deficit_units, P):
    """Build a minimum-shed QUBO from (load_units, shed_cost) items."""
    total_weight = sum(weight for weight, _ in items)
    largest_weight = max(weight for weight, _ in items)
    max_slack = max(0, min(total_weight - deficit_units, largest_weight - 1))
    slack, rem, bit = [], max_slack, 1
    while rem > 0:
        value = min(bit, rem)
        slack.append(value)
        rem -= value
        bit *= 2
    coefficients = [weight for weight, _ in items] + [-value for value in slack]
    m = len(coefficients)
    linear = np.zeros(m)
    quadratic = {}
    offset = P * deficit_units ** 2
    for i, coefficient in enumerate(coefficients):
        linear[i] += P * (coefficient ** 2 - 2 * deficit_units * coefficient)
        for j in range(i + 1, m):
            quadratic[(i, j)] = 2 * P * coefficient * coefficients[j]
    for i, (_, shed_cost) in enumerate(items):
        linear[i] += shed_cost
    return linear, quadratic, offset, coefficients, m


def qubo_to_ising(lin, quad, offset, m):
    """x=(1-Z)/2. Returns SparsePauliOp."""
    from qiskit.quantum_info import SparsePauliOp

    h = np.zeros(m); J = {}; const = offset
    for k in range(m):
        const += lin[k] / 2; h[k] -= lin[k] / 2
    for (k, l), q in quad.items():
        const += q / 4; h[k] -= q / 4; h[l] -= q / 4; J[(k, l)] = q / 4
    terms = []
    for k in range(m):
        if h[k] != 0:
            s = ["I"] * m; s[m - 1 - k] = "Z"; terms.append(("".join(s), h[k]))
    for (k, l), q in J.items():
        s = ["I"] * m; s[m - 1 - k] = "Z"; s[m - 1 - l] = "Z"; terms.append(("".join(s), q))
    return SparsePauliOp.from_list(terms), const


def quantum_dispatch(deficit_kw, transformers, outage_counts=None,
                     fairness_penalty=15.0, reps=1, restarts=1, seed=7,
                     maxiter=250, shots=2048):
    """Minimize revenue lost while meeting a minimum shed-load target

    purely through parameterized quantum circuits and statevector optimization.
    """
    rng = np.random.default_rng(seed)
    transformers = flatten_transformers(transformers)
    outage_counts = outage_counts or {}
    discretionary = [t for t in transformers if not t["is_critical"]]
    discretionary_load_decimal = sum((Decimal(str(t["load_kw"])) for t in discretionary), Decimal(0))
    discretionary_load = float(discretionary_load_decimal)
    requested_deficit_decimal = max(Decimal(0), Decimal(str(deficit_kw)))
    requested_deficit = float(requested_deficit_decimal)
    target_deficit_decimal = min(requested_deficit_decimal, discretionary_load_decimal)
    target_deficit = float(target_deficit_decimal)
    result = {t["id"]: (1 if t["is_critical"] else 0) for t in transformers}

    if target_deficit == 0 or not discretionary:
        return {t["id"]: 1 for t in transformers}, {
            "requested_shed_kw": requested_deficit,
            "achievable_shed_kw": 0.0,
            "requested_deficit_met": requested_deficit == 0,
            "maximum_safe_shed_kw": discretionary_load,
            "solver_status": "No discretionary shedding required (Pure Quantum)",
        }

    decimal_values = [Decimal(str(t["load_kw"])) for t in discretionary]
    decimal_values.append(target_deficit_decimal)
    precision = max(max(0, -value.as_tuple().exponent) for value in decimal_values)
    decimal_scale = 10 ** precision
    load_units = [int(value * decimal_scale) for value in decimal_values[:-1]]
    target_units = int(decimal_values[-1] * decimal_scale)
    common_unit = max(reduce(math.gcd, load_units), 1)
    weights = [value // common_unit for value in load_units]
    deficit_units = (target_units + common_unit - 1) // common_unit
    revenue_costs = [float(t["load_kw"]) * float(t["recovery_ratio"]) for t in discretionary]
    adjusted_costs = [
        revenue_cost + fairness_penalty * int(outage_counts.get(t["id"], 0))
        for t, revenue_cost in zip(discretionary, revenue_costs)
    ]
    objective_scale = max(sum(adjusted_costs), 1.0)
    items = list(zip(weights, [cost / objective_scale for cost in adjusted_costs]))
    
    lin, quad, offset, _, m = build_qubo(items, deficit_units, P=2.0)
    H, ising_offset = qubo_to_ising(lin, quad, offset, m)
    n = len(discretionary)

    from qiskit import QuantumCircuit
    from qiskit.circuit import ParameterVector
    from qiskit.primitives import StatevectorEstimator, StatevectorSampler
    gam = ParameterVector("g", reps)
    bet = ParameterVector("b", reps)
    circ = QuantumCircuit(m)
    circ.h(range(m))
    for layer in range(reps):
        for pauli, coef in H.to_list():
            idx = [m - 1 - j for j, ch in enumerate(pauli) if ch == "Z"]
            coef = float(np.real(coef))
            if len(idx) == 1:
                circ.rz(2 * gam[layer] * coef, idx[0])
            else:
                circ.rzz(2 * gam[layer] * coef, idx[0], idx[1])
        circ.rx(2 * bet[layer], range(m))

    estimator = StatevectorEstimator(seed=seed)

    def expval(params):
        pub = (circ, H, np.asarray(params, dtype=float))
        estimate = estimator.run([pub]).result()[0].data.evs
        return float(np.asarray(estimate).reshape(-1)[0]) + float(np.real(ising_offset))

    best = None
    for _ in range(restarts):
        x0 = rng.uniform(0, np.pi, circ.num_parameters)
        optimization = minimize(expval, x0, method="COBYLA", options={"maxiter": maxiter})
        if best is None or optimization.fun < best.fun:
            best = optimization

    measured_circuit = circ.copy()
    measured_circuit.measure_all()
    sampler = StatevectorSampler(seed=seed)
    sampled = sampler.run([(measured_circuit, best.x)], shots=shots).result()[0]
    counts = sampled.data.meas.get_counts()

    quantum_candidates = []
    total_shots = sum(counts.values())
    for bitstring, count in counts.items():
        bits = [int(char) for char in bitstring.replace(" ", "")[::-1]]
        shed_bits = bits[:n]
        shed_units = sum(weights[i] * shed_bits[i] for i in range(n))
        if shed_units < deficit_units:
            continue
        cost = sum(adjusted_costs[i] * shed_bits[i] for i in range(n))
        excess_shed = shed_units - deficit_units
        quantum_candidates.append((cost, excess_shed, -count, shed_bits, count))

    quantum_candidates.sort()
    if not quantum_candidates:
        raise RuntimeError(
            f"QAOA sampler produced no deficit-feasible schedule in {shots} shots; "
            "increase sampler shots or optimizer restarts."
        )

    # QAOA's most economical sampled feasible state directly drives dispatch.
    selected_bits = quantum_candidates[0][3]
    quantum_choice = selected_bits
    best_probability = quantum_candidates[0][4] / total_shots

    for i, transformer in enumerate(discretionary):
        result[transformer["id"]] = 0 if selected_bits[i] else 1

    shed_kw = sum(float(discretionary[i]["load_kw"]) * selected_bits[i] for i in range(n))
    revenue_lost = sum(revenue_costs[i] * selected_bits[i] for i in range(n))
    
    info = {
        "requested_shed_kw": requested_deficit,
        "achievable_shed_kw": shed_kw,
        "requested_deficit_met": shed_kw + 1e-9 >= requested_deficit,
        "maximum_safe_shed_kw": discretionary_load,
        "revenue_lost": revenue_lost,
        "historical_outage_counts": {t["id"]: int(outage_counts.get(t["id"], 0)) for t in discretionary},
        "fairness_penalty_per_prior_outage": fairness_penalty,
        "qubits": m,
        "reps": reps,
        "qaoa_energy": float(best.fun),
        "quantum_choice": quantum_choice,
        "selected_shed_bits": list(selected_bits),
        "P(quantum_choice)": round(best_probability, 4),
        "random_guess": round(1 / 2 ** n, 4),
        "solver_status": "QAOA sampled feasible schedule",
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
    out, info = quantum_dispatch(data["deficit_kw"], data["transformers"])
    print(out)
    print(info)
