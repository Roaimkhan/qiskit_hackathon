#!/usr/bin/env python3
"""Compare QAOA, exact branch-and-bound, and classical MILP dispatch solvers.

Run from the project environment with ``python benchmark_three_algorithms.py``.
Revenue loss is reported as the model proxy ``load_kw * recovery_ratio``;
the project does not provide tariff data for converting this to currency.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

try:
    from quantum_solver import quantum_dispatch
except Exception as exc:  # Keep other algorithms usable if Qiskit is unavailable.
    quantum_dispatch = None
    QUANTUM_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    QUANTUM_IMPORT_ERROR = None

try:
    from exact_algorithm import exact_dispatch
except Exception as exc:
    exact_dispatch = None
    EXACT_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    EXACT_IMPORT_ERROR = None

try:
    from classical_solver import classical_dispatch
except Exception as exc:
    classical_dispatch = None
    CLASSICAL_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    CLASSICAL_IMPORT_ERROR = None


FAIRNESS_PENALTY = 15.0
ALGORITHMS = ("QAOA", "Exact", "Classical MILP")


def _transformers(loads, recoveries, critical_indices=()):
    critical_indices = set(critical_indices)
    return [
        {
            "id": f"T{i + 1}",
            "load_kw": float(load),
            "recovery_ratio": float(recovery),
            "is_critical": i in critical_indices,
        }
        for i, (load, recovery) in enumerate(zip(loads, recoveries))
    ]


def _case(case_id, tier, description, loads, recoveries, deficit_kw,
          critical_indices=(), outage_counts=None):
    return {
        "case_id": case_id,
        "tier": tier,
        "description": description,
        "deficit_kw": float(deficit_kw),
        "transformers": _transformers(loads, recoveries, critical_indices),
        "outage_counts": outage_counts or {},
    }


def build_test_suite():
    """Return exactly 18 deterministic cases; histories cover 11 cases."""
    cases = [
        _case("S1", "Tier 1: small", "balanced residential", [80, 95, 110, 125], [.65, .72, .80, .55], 100,
              outage_counts={"T1": 1, "T4": 2}),
        _case("S2", "Tier 1: small", "one critical node", [100, 70, 130, 90], [.98, .32, .61, .44], 120,
              critical_indices=[0]),
        _case("S3", "Tier 1: small", "mixed recovery", [60, 140, 85, 115], [.25, .91, .48, .73], 160,
              outage_counts={"T1": 3, "T3": 1}),
        _case("S4", "Tier 1: small", "high target", [105, 75, 125, 95], [.88, .40, .67, .30], 250,
              critical_indices=[2], outage_counts={"T2": 1}),
    ]

    # Representative six-transformer slice modeled after the project's main
    # feeder: two protected nodes and four discretionary customer groups.
    main_loads = [180, 120, 250, 160, 300, 140]
    main_recovery = [.98, .95, .86, .92, .28, .48]
    main_histories = [
        {"T5": 1}, {}, {"T5": 2, "T6": 1},
        {"T4": 1, "T5": 1}, {}, {"T5": 3, "T6": 2},
    ]
    for i, deficit in enumerate([100, 200, 388, 500, 700, 850], start=1):
        cases.append(_case(
            f"M{i}", "Tier 2: standard", "main-feeder six-node slice",
            main_loads, main_recovery, deficit, critical_indices=[0, 1],
            outage_counts=main_histories[i - 1],
        ))

    cases.extend([
        _case("L1", "Tier 3: scaled", "eight-node synthetic", [90, 110, 130, 150, 170, 190, 210, 230],
              [.4, .6, .8, .3, .7, .5, .9, .35], 388, critical_indices=[0], outage_counts={"T4": 1, "T7": 2}),
        _case("L2", "Tier 3: scaled", "nine-node synthetic", [75, 95, 115, 135, 155, 175, 195, 215, 235],
              [.55, .25, .82, .44, .67, .31, .91, .48, .72], 500, critical_indices=[2], outage_counts={"T2": 1, "T6": 2}),
        _case("L3", "Tier 3: scaled", "ten-node synthetic", [80, 100, 120, 140, 160, 180, 200, 220, 240, 260],
              [.33, .7, .52, .88, .4, .62, .29, .95, .47, .76], 700, critical_indices=[7]),
        _case("L4", "Tier 3: scaled", "ten-node rural mix", [65, 85, 105, 125, 145, 165, 185, 205, 225, 245],
              [.2, .45, .72, .91, .34, .58, .83, .27, .66, .49], 900, critical_indices=[3], outage_counts={"T1": 2, "T8": 1}),
    ])

    cases.extend([
        _case("E1", "Edge case", "zero deficit", [80, 100, 120, 140], [.5, .6, .7, .8], 0,
              outage_counts={"T2": 1}),
        _case("E2", "Edge case", "target exceeds discretionary capacity", [180, 120, 250, 160], [.98, .95, .86, .92], 500,
              critical_indices=[0, 1], outage_counts={"T3": 1}),
    ])

    # The requested tier sizes plus two edge cases total 16; these two
    # supplemental cases make the required total 18 and exercise fairness.
    cases.extend([
        _case("F1", "Supplemental fairness", "repeat-outage rotation sample", [100, 130, 160, 190, 220, 250],
              [.3, .4, .5, .6, .7, .8], 300, critical_indices=[0],
              outage_counts={"T2": 4, "T3": 2, "T4": 1}),
        _case("F2", "Supplemental fairness", "larger fairness history", [90, 110, 130, 150, 170, 190, 210, 230],
              [.75, .32, .58, .91, .43, .68, .25, .82], 420, critical_indices=[3],
              outage_counts={"T2": 2, "T5": 3, "T7": 1}),
    ])
    assert len(cases) == 18
    assert sum(bool(case["outage_counts"]) for case in cases) >= 9
    return cases


def _normalize_output(output):
    """Accept the tuple shape of QAOA/MILP and dict shape of exact solver."""
    if isinstance(output, tuple) and len(output) == 2:
        states, info = output
    elif isinstance(output, dict) and "result" in output:
        states, info = output["result"], output.get("info", {})
    else:
        raise TypeError(f"Unrecognized solver result shape: {type(output).__name__}")
    if not isinstance(states, dict):
        raise TypeError("Solver state result must be a mapping of transformer IDs to states")
    return {str(key): bool(value) for key, value in states.items()}, info or {}


def _score(case, states, exact_objective=None):
    transformers = case["transformers"]
    discretionary = [item for item in transformers if not item["is_critical"]]
    critical_on = all(states.get(item["id"], False) for item in transformers if item["is_critical"])
    shed = [item for item in discretionary if not states.get(item["id"], False)]
    shed_kw = sum(item["load_kw"] for item in shed)
    revenue_lost = sum(item["load_kw"] * item["recovery_ratio"] for item in shed)
    fairness_cost = FAIRNESS_PENALTY * sum(case["outage_counts"].get(item["id"], 0) for item in shed)
    objective_cost = revenue_lost + fairness_cost
    feasible = critical_on and shed_kw + 1e-9 >= case["deficit_kw"]
    if exact_objective is None:
        gap = None
    elif exact_objective == 0:
        gap = 0.0 if objective_cost == 0 else None
    else:
        gap = (objective_cost - exact_objective) / exact_objective * 100.0
    return {
        "shed_kw": shed_kw,
        "revenue_lost_proxy": revenue_lost,
        "fairness_adjusted_objective_cost": objective_cost,
        "critical_infrastructure_protected": critical_on,
        "feasible": feasible,
        "optimality_gap_percent": gap,
    }


def _run_one(solver, case, algorithm, options):
    started = time.perf_counter()
    try:
        output = solver(
            case["deficit_kw"], case["transformers"],
            outage_counts=case["outage_counts"], fairness_penalty=FAIRNESS_PENALTY,
            **options,
        )
        states, info = _normalize_output(output)
        elapsed = time.perf_counter() - started
        return {"algorithm": algorithm, "runtime_seconds": elapsed,
                "states": states, "solver_info": info, "error": None}
    except Exception as exc:
        return {"algorithm": algorithm, "runtime_seconds": time.perf_counter() - started,
                "states": None, "solver_info": {},
                "error": f"{type(exc).__name__}: {exc}"}


def evaluate(cases, qaoa_maxiter=60, qaoa_shots=2048, seed=7):
    solvers = {
        "QAOA": (quantum_dispatch, {"reps": 1, "restarts": 1, "seed": seed,
                                      "maxiter": qaoa_maxiter, "shots": qaoa_shots}, QUANTUM_IMPORT_ERROR),
        "Exact": (exact_dispatch, {}, EXACT_IMPORT_ERROR),
        "Classical MILP": (classical_dispatch, {}, CLASSICAL_IMPORT_ERROR),
    }
    records = []
    for case in cases:
        run_results = {}
        exact_objective = None
        # Exact establishes the reference objective before calculating gaps.
        for algorithm in ("Exact", "QAOA", "Classical MILP"):
            solver, options, import_error = solvers[algorithm]
            if solver is None:
                run = {"algorithm": algorithm, "runtime_seconds": 0.0, "states": None,
                       "solver_info": {}, "error": import_error or "Solver import unavailable"}
            else:
                run = _run_one(solver, case, algorithm, options)
            if run["states"] is not None and algorithm == "Exact":
                score = _score(case, run["states"])
                exact_objective = score["fairness_adjusted_objective_cost"]
            run_results[algorithm] = run

        algorithm_results = {}
        for algorithm in ALGORITHMS:
            run = run_results[algorithm]
            if run["states"] is None:
                result = {"algorithm": algorithm, "runtime_seconds": run["runtime_seconds"],
                          "error": run["error"], "feasible": False}
            else:
                result = {"algorithm": algorithm, "runtime_seconds": run["runtime_seconds"],
                          **_score(case, run["states"], exact_objective),
                          "states": run["states"], "solver_info": run["solver_info"], "error": None}
            algorithm_results[algorithm] = result
        records.append({
            "case_id": case["case_id"], "tier": case["tier"],
            "description": case["description"], "transformer_count": len(case["transformers"]),
            "deficit_target_kw": case["deficit_kw"], "outage_counts": case["outage_counts"],
            "transformers": case["transformers"],
            "exact_reference_objective_cost": exact_objective,
            "results": algorithm_results,
        })
    return records


def _fmt(value, digits=2):
    return "—" if value is None else f"{value:,.{digits}f}"


def print_report(records):
    headers = ["Test", "Tier (n)", "Target kW", "Algorithm", "Time s", "Shed kW",
               "Revenue lost*", "Objective cost*", "Gap %", "Feasible"]
    rows = []
    for record in records:
        for algorithm in ALGORITHMS:
            result = record["results"][algorithm]
            rows.append([
                record["case_id"], f"{record['tier']} (n={record['transformer_count']})",
                _fmt(record["deficit_target_kw"], 0), algorithm,
                _fmt(result["runtime_seconds"], 4), _fmt(result.get("shed_kw"), 1),
                _fmt(result.get("revenue_lost_proxy")), _fmt(result.get("fairness_adjusted_objective_cost")),
                _fmt(result.get("optimality_gap_percent")),
                ("Yes" if result.get("feasible") else "No") if not result.get("error") else "Error",
            ])
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    print("| " + " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers))) + " |")
    print("| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |")
    for row in rows:
        print("| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |")

    print("\n### Aggregate summary")
    print("| Algorithm | Avg time (s) | Avg gap (%) | Feasibility success |")
    print("|---|---:|---:|---:|")
    for algorithm in ALGORITHMS:
        results = [record["results"][algorithm] for record in records]
        times = [item["runtime_seconds"] for item in results]
        gaps = [item["optimality_gap_percent"] for item in results
                if item.get("optimality_gap_percent") is not None and math.isfinite(item["optimality_gap_percent"])]
        successes = sum(bool(item.get("feasible")) for item in results)
        print(f"| {algorithm} | {_fmt(statistics.mean(times) if times else None, 4)} | "
              f"{_fmt(statistics.mean(gaps) if gaps else None)} | {successes}/{len(results)} ({successes/len(results)*100:.1f}%) |")
    print("\n* Revenue lost is the model proxy `load_kw × recovery_ratio`; objective cost adds the configured historical-outage fairness penalty.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="three_algorithm_evaluation_results.json",
                        help="JSON output path (default: %(default)s)")
    parser.add_argument("--qaoa-maxiter", type=int, default=60,
                        help="COBYLA iteration limit per QAOA case (default: %(default)s)")
    parser.add_argument("--qaoa-shots", type=int, default=2048,
                        help="QAOA sampler shots per case (default: %(default)s)")
    parser.add_argument("--seed", type=int, default=7, help="QAOA random seed")
    args = parser.parse_args()

    cases = build_test_suite()
    records = evaluate(cases, args.qaoa_maxiter, args.qaoa_shots, args.seed)
    print_report(records)
    payload: dict[str, Any] = {
        "description": "18-case comparison of QAOA, exact branch-and-bound, and classical MILP load shedding",
        "objective_note": "Revenue lost is load_kw * recovery_ratio proxy; objective cost adds fairness penalty for prior outages.",
        "configuration": {"fairness_penalty": FAIRNESS_PENALTY, "qaoa_maxiter": args.qaoa_maxiter,
                          "qaoa_shots": args.qaoa_shots, "seed": args.seed},
        "summary": {
            algorithm: {
                "average_runtime_seconds": statistics.mean([case["results"][algorithm]["runtime_seconds"] for case in records]),
                "average_optimality_gap_percent": (
                    statistics.mean(gaps) if (gaps := [case["results"][algorithm].get("optimality_gap_percent")
                        for case in records if case["results"][algorithm].get("optimality_gap_percent") is not None]) else None
                ),
                "feasible_count": sum(bool(case["results"][algorithm].get("feasible")) for case in records),
                "success_rate_percent": 100 * sum(bool(case["results"][algorithm].get("feasible")) for case in records) / len(records),
            } for algorithm in ALGORITHMS
        },
        "cases": records,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"\nJSON results written to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
