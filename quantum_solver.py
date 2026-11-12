"""QAOA transformer shedding with critical protection and outage fairness.

For discretionary transformers, binary ``y_i`` means shed. The model enforces
``sum(L_i * y_i) >= D`` and minimizes shed revenue loss, with a cumulative
outage penalty to rotate repeated shedding. Critical transformers are fixed ON.
"""
import itertools, math
from decimal import Decimal
from functools import reduce
import numpy as np
from scipy.optimize import minimize
from qiskit.quantum_info import SparsePauliOp, Statevector


def build_qubo(items, deficit_units, P):
    """Build a minimum-shed QUBO from (load_units, shed_cost) items."""
    max_slack = sum(weight for weight, _ in items) - deficit_units
    # Equality: shed load - slack == deficit. Bounded binary slack encodes
    # every integer from zero to max_slack, enforcing shed load >= deficit.
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
    """x=(1-Z)/2.  Returns SparsePauliOp."""
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


def qubo_energy(bits, lin, quad, offset):
    e = offset + sum(lin[k] * bits[k] for k in range(len(bits)))
    e += sum(q * bits[k] * bits[l] for (k, l), q in quad.items())
    return e


def quantum_dispatch(deficit_kw, transformers, outage_counts=None,
                     fairness_penalty=15.0, reps=3, restarts=4, seed=7):
    """Minimize revenue lost while meeting a minimum shed-load target.

    Critical transformers remain ON. Cumulative outage counts add a modest
    equity cost so repeated dispatches can rotate outages across feeders.
    """
    rng = np.random.default_rng(seed)
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
            "solver_status": "No discretionary shedding required",
        }

    # Encode the loads and target as exact integer units. Dividing by their
    # GCD keeps the slack register small without rounding the deficit floor.
    decimal_values = [Decimal(str(t["load_kw"])) for t in discretionary]
    decimal_values.append(target_deficit_decimal)
    precision = max(max(0, -value.as_tuple().exponent) for value in decimal_values)
    decimal_scale = 10 ** precision
    load_units = [int(value * decimal_scale) for value in decimal_values[:-1]]
    target_units = int(decimal_values[-1] * decimal_scale)
    common_unit = max(reduce(math.gcd, load_units + [target_units]), 1)
    weights = [value // common_unit for value in load_units]
    deficit_units = target_units // common_unit
    revenue_costs = [float(t["load_kw"]) * float(t["recovery_ratio"]) for t in discretionary]
    adjusted_costs = [
        revenue_cost + fairness_penalty * int(outage_counts.get(t["id"], 0))
        for t, revenue_cost in zip(discretionary, revenue_costs)
    ]
    objective_scale = max(sum(adjusted_costs), 1.0)
    items = list(zip(weights, [cost / objective_scale for cost in adjusted_costs]))
    # The normalized objective can improve by at most one, so P=2 makes each
    # one-unit equality violation more expensive than every possible gain.
    lin, quad, offset, _, m = build_qubo(items, deficit_units, P=2.0)
    H, _ = qubo_to_ising(lin, quad, offset, m)
    n = len(discretionary)

    from qiskit import QuantumCircuit
    from qiskit.circuit import ParameterVector
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

    energies = np.array([
        qubo_energy([(z >> k) & 1 for k in range(m)], lin, quad, offset)
        for z in range(2 ** m)
    ])

    def expval(params):
        state = Statevector(circ.assign_parameters(params))
        return float(np.dot(state.probabilities(), energies))

    best = None
    for _ in range(restarts):
        x0 = rng.uniform(0, np.pi, circ.num_parameters)
        optimization = minimize(expval, x0, method="COBYLA", options={"maxiter": 250})
        if best is None or optimization.fun < best.fun:
            best = optimization

    probabilities = Statevector(circ.assign_parameters(best.x)).probabilities_dict()
    quantum_candidates = []
    for bitstring, probability in probabilities.items():
        bits = [int(char) for char in bitstring[::-1]]
        shed_bits = bits[:n]
        shed_units = sum(weights[i] * shed_bits[i] for i in range(n))
        if shed_units >= deficit_units:
            cost = sum(adjusted_costs[i] * shed_bits[i] for i in range(n))
            quantum_candidates.append((cost, shed_units - deficit_units, -probability, shed_bits))
    quantum_candidates.sort()
    quantum_choice = quantum_candidates[0][3] if quantum_candidates else None

    # Exhaustively verify the feeder-sized candidate set and enforce the hard
    # floor even if QAOA sampling misses a feasible state.
    feasible = [
        bits for bits in itertools.product([0, 1], repeat=n)
        if sum(weights[i] * bits[i] for i in range(n)) >= deficit_units
    ]
    chosen = min(
        feasible,
        key=lambda bits: (
            sum(adjusted_costs[i] * bits[i] for i in range(n)),
            sum(weights[i] * bits[i] for i in range(n)) - deficit_units,
        ),
    )
    for i, transformer in enumerate(discretionary):
        result[transformer["id"]] = 0 if chosen[i] else 1

    shed_kw = sum(float(discretionary[i]["load_kw"]) * chosen[i] for i in range(n))
    revenue_lost = sum(revenue_costs[i] * chosen[i] for i in range(n))
    optimal_probability = sum(
        probability for bitstring, probability in probabilities.items()
        if [int(char) for char in bitstring[::-1]][:n] == list(chosen)
    )
    quantum_matched = quantum_choice == list(chosen)
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
        "qaoa_energy": best.fun,
        "quantum_choice": quantum_choice,
        "selected_shed_bits": list(chosen),
        "P(optimal x)": round(optimal_probability, 4),
        "random_guess": round(1 / 2 ** n, 4),
        "match": quantum_matched,
        "solver_status": "QAOA Optimal Convergence" if quantum_matched else "QAOA completed; exact optimum selected",
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
