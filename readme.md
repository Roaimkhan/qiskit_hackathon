To win a quantum challenge—especially with Pakistan’s nationwide rollout of DISCO transformer smart meters (APMS) actively in the headlines—your demo shouldn't just spit out a terminal bitstring like `[1, 0, 1, 1, 0]`. Judges want to see **why classical rules fail** and **how quantum optimization saves the day** on a realistic, high-stakes grid topology.

The best demo scenario is **"The Peak-Summer Heatwave Squeeze"** on an **11kV Mixed Urban Feeder** (modeled after an IESCO or LESCO feeder).

---

### 1. The Scenario Setup: "The 2:00 PM Heatwave Crisis"

Set the stage with an interactive dashboard (React/Next.js or Streamlit + FastAPI) simulating a real-time APMS telemetry feed during a severe July heatwave:

* **Feeder Capacity Ceiling:** Normally 1,200 kW, but due to extreme ambient heat and grid generation shortfall, dispatch control drops the feeder's safe thermal limit to **750 kW**.
* **Total Live Demand:** **1,150 kW** across **8 distinct distribution transformers (DTs)** (8 qubits—small enough to simulate cleanly and fast in Qiskit, large enough to create $2^8 = 256$ combinations with non-trivial trade-offs).
* **The Twist (Coupled Sub-Branches):** Two transformers ($T_3$ and $T_4$) share an aging underground lateral cable. Even if the main feeder has headroom, running *both* simultaneously risks blowing the lateral junction.

---

### 2. The 8-Transformer Roster (Designed to Break Greedy Algorithms)

| ID | Node Type | Load (kW) | Bill Recovery | Priority / Constraint |
| --- | --- | --- | --- | --- |
| **T1** | **District Hospital (ER & ICU)** | 180 kW | 99% | **HARD CRITICAL** ($x_1 = 1$) |
| **T2** | **Municipal Water Tube-well** | 90 kW | 95% | **HARD CRITICAL** ($x_2 = 1$) |
| **T3** | **Model Town Residential (Block A)** | 160 kW | 94% | Honest payers (Shares lateral with T4) |
| **T4** | **Commercial Plaza & Cold Storage** | 210 kW | 88% | High revenue, high load (Shares lateral with T3) |
| **T5** | **Middle-Class Housing Society** | 140 kW | 85% | Honest payers |
| **T6** | **Bazaar / Mixed Retail Strip** | 130 kW | 42% | Moderate line losses / hookups (*kunda*) |
| **T7** | **Industrial Workshop Cluster** | 150 kW | 28% | Severe theft / chronically defaulted |
| **T8** | **High-Loss Informal Settlement** | 90 kW | 15% | Severe theft / chronically defaulted |

* **Mandatory Critical Load ($T_1 + T_2$):** $180 + 90 = \mathbf{270\text{ kW}}$ locked ON.
* **Remaining Budget for $T_3 \dots T_8$:** $750 - 270 = \mathbf{480\text{ kW}}$ (out of 880 kW requested).

---

### 3. The Live Side-by-Side Showdown (What You Show the Judges)

Your demo UI should let the judge click a **"Trigger 400 kW Deficit"** button and compare **three dispatch engines side-by-side** in real time:

#### Tab 1: Legacy DISCO Policy (Coarse Feeder Shedding)

* **Action:** Trips the entire 11kV breaker for 4 hours.
* **Result:** **0 kW delivered.** Hospital switches to expensive diesel, honest citizens in $T_3$ and $T_5$ suffer collective punishment, and DISCO loses 100% of hourly revenue.

#### Tab 2: Naive Classical Greedy Heuristic

* **Logic:** Sorts discretionary transformers by highest bill recovery ($T_3 \rightarrow T_4 \rightarrow T_5$) and turns them ON until hitting the 480 kW budget.
* **Action:** Turns ON $T_3$ (160 kW) + $T_4$ (210 kW) = 370 kW. Next is $T_5$ (140 kW), which pushes load to 510 kW (over limit), so it skips $T_5$ and turns ON $T_8$ (90 kW, 15% recovery!) just to fill remaining capacity.
* **The Catastrophe:** Because $T_3$ and $T_4$ are both ON, **the shared underground lateral cable overheats and trips**, knocking out both honest neighborhoods anyway—while rewarding a 15% recovery zone ($T_8$) with power!

#### Tab 3: Your Qiskit QAOA Surgical Optimizer

* **Logic:** Evaluates the QUBO energy landscape—balancing recovery-weighted revenue, capacity penalties, and the quadratic lateral-cable interaction penalty ($+\lambda x_3 x_4$).
* **Action:** Realizes running $T_3$ and $T_4$ together incurs a massive thermal penalty. Instead, it turns ON **$T_4$ (210 kW) + $T_5$ (140 kW) + $T_6$ (130 kW) = 480 kW** (or **$T_3 + T_5 + T_6 = 430\text{ kW}$** depending on your weight tuning).
* **Result:**
* **100% Critical Safety:** Hospital and water pump untouched.
* **Zero Thermal Trips:** Lateral cable stays safe.
* **Maximum Revenue & Fairness:** Isolates the worst defaulters ($T_7, T_8$) while keeping honest payers energized.



---

### 4. Bonus "Judge-Pleaser" Interactive Controls

Add two live sliders on the left panel so judges can stress-test your QUBO live during Q&A:

1. **Feeder Capacity Slider (`500 kW` to `1,150 kW`):** Dragging it down watches QAOA dynamically shed the next-lowest recovery transformer in real time.
2. **"Social Fairness vs. Pure Revenue" Weight Slider:** Shows how tuning the linear coefficients shifts priority between high-load commercial payers ($T_4$) and smaller residential neighborhoods ($T_3, T_5$).
3. **Quantum Diagnostics Drawer:** Display the actual Qiskit `QAOAAnsatz` circuit diagram and the **measurement probability histogram** (showing the optimal bitstring spiking as the tallest bar out of all 256 quantum states).