"""Feeder topology and transformer load data for frontend visualization.

Topology assumption: T1, T2, T5, T6, T7, and T8 connect directly to the
main feeder. T3 and T4 connect on the same lateral, as specified in the input.
"""

PRIORITY_LABELS = {
    "HARD CRITICAL": "Critical - Hard constraint",
    "Honest payers": "Reliable payers",
    "High revenue, high load": "High load, high revenue",
    "Moderate line losses / hookups (kunda)": "Moderate loss risk / informal connections",
    "Severe theft / chronically defaulted": "High loss risk / chronic default risk",
}


def normalize_priority(priority):
    return PRIORITY_LABELS.get(priority, priority)


def standardize_topology(node):
    if not isinstance(node, dict):
        return node

    normalized = dict(node)

    if "priority" in normalized:
        normalized["priority"] = normalize_priority(normalized["priority"])

    if "children" in normalized and isinstance(normalized["children"], list):
        normalized["children"] = [standardize_topology(child) for child in normalized["children"]]

    return normalized


def generate_topology():
    return standardize_topology(
        {
            "id": "F1",
            "name": "Distribution Feeder",
            "node_type": "feeder",
            "children": [
                {
                    "id": "T1",
                    "node_type": "transformer",
                    "load": {"value": 180, "unit": "kW"},
                    "customer_type": "District Hospital (ER & ICU)",
                    "bill_recovery": {"value": 99, "unit": "%"},
                    "priority": "HARD CRITICAL",
                    "constraint": {"type": "hard", "variable": "x1", "value": 1},
                },
                {
                    "id": "T2",
                    "node_type": "transformer",
                    "load": {"value": 90, "unit": "kW"},
                    "customer_type": "Municipal Water Tube-well",
                    "bill_recovery": {"value": 95, "unit": "%"},
                    "priority": "HARD CRITICAL",
                    "constraint": {"type": "hard", "variable": "x2", "value": 1},
                },
                {
                    "id": "LATERAL_T3_T4",
                    "node_type": "lateral",
                    "name": "Shared lateral for T3 and T4",
                    "children": [
                        {
                            "id": "T3",
                            "node_type": "transformer",
                            "load": {"value": 160, "unit": "kW"},
                            "customer_type": "Model Town Residential (Block A)",
                            "bill_recovery": {"value": 94, "unit": "%"},
                            "priority": "Honest payers",
                            "constraint": None,
                        },
                        {
                            "id": "T4",
                            "node_type": "transformer",
                            "load": {"value": 210, "unit": "kW"},
                            "customer_type": "Commercial Plaza & Cold Storage",
                            "bill_recovery": {"value": 88, "unit": "%"},
                            "priority": "High revenue, high load",
                            "constraint": None,
                        },
                    ],
                },
                {
                    "id": "T5",
                    "node_type": "transformer",
                    "load": {"value": 140, "unit": "kW"},
                    "customer_type": "Middle-Class Housing Society",
                    "bill_recovery": {"value": 85, "unit": "%"},
                    "priority": "Honest payers",
                    "constraint": None,
                },
                {
                    "id": "T6",
                    "node_type": "transformer",
                    "load": {"value": 130, "unit": "kW"},
                    "customer_type": "Bazaar / Mixed Retail Strip",
                    "bill_recovery": {"value": 42, "unit": "%"},
                    "priority": "Moderate line losses / hookups (kunda)",
                    "constraint": None,
                },
                {
                    "id": "T7",
                    "node_type": "transformer",
                    "load": {"value": 150, "unit": "kW"},
                    "customer_type": "Industrial Workshop Cluster",
                    "bill_recovery": {"value": 28, "unit": "%"},
                    "priority": "Severe theft / chronically defaulted",
                    "constraint": None,
                },
                {
                    "id": "T8",
                    "node_type": "transformer",
                    "load": {"value": 90, "unit": "kW"},
                    "customer_type": "High-Loss Informal Settlement",
                    "bill_recovery": {"value": 15, "unit": "%"},
                    "priority": "Severe theft / chronically defaulted",
                    "constraint": None,
                },
            ],
        }
    )
