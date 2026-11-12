from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List
from quantum_solver import quantum_dispatch
from classical_solver import classical_dispatch
from exact_algorithm import exact_dispatch
from main import generate_topology

app = FastAPI(title="Quantum APMS Dispatch Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/get_topology")
def get_topology():
    return generate_topology()


class TransformerPayload(BaseModel):
    id: str
    load_kw: float
    recovery_ratio: float
    is_critical: bool

class DispatchRequest(BaseModel):
    feeder_id: str
    load_shedding_kw: float = Field(ge=0)
    transformers: List[TransformerPayload]

class DispatchResponse(BaseModel):
    feeder_id: str
    success: bool
    transformer_states: Dict[str, bool]
    optimization_info: Dict[str, Any]

def _run_dispatch(data: DispatchRequest, solver):
    try:
        transformers_dict = [t.dict() for t in data.transformers]
        total_load_kw = sum(transformer["load_kw"] for transformer in transformers_dict)
        if data.load_shedding_kw > total_load_kw:
            raise HTTPException(status_code=422, detail="load_shedding_kw cannot exceed total transformer load")

        solver_output = solver(
            deficit_kw=data.load_shedding_kw,
            transformers=transformers_dict,
        )
        if isinstance(solver_output, tuple) and len(solver_output) == 2:
            selected, optimization_info = solver_output
        elif isinstance(solver_output, dict):
            selected = solver_output.get("result", solver_output.get("transformer_states"))
            optimization_info = solver_output.get("info", solver_output.get("optimization_info"))
        else:
            raise TypeError("Solver must return a (states, info) tuple or a result/info mapping")

        if not isinstance(selected, dict) or not isinstance(optimization_info, dict):
            raise TypeError("Solver output must include transformer states and optimization info mappings")

        return {
            "feeder_id": data.feeder_id,
            "success": True,
            "transformer_states": {
                transformer_id: bool(is_on)
                for transformer_id, is_on in selected.items()
            },
            "optimization_info": optimization_info,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/optimize-dispatch", response_model=DispatchResponse)
def optimize_grid(data: DispatchRequest):
    return _run_dispatch(data, quantum_dispatch)


@app.post("/api/classical-dispatch", response_model=DispatchResponse)
def optimize_grid_classical(data: DispatchRequest):
    return _run_dispatch(data, classical_dispatch)


@app.post("/api/exact-dispatch", response_model=DispatchResponse)
def optimize_grid_exact(data: DispatchRequest):
    return _run_dispatch(data, exact_dispatch)
