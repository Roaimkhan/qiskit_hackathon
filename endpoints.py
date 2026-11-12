from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List
from threading import Lock
from quantum_solver import quantum_dispatch
from main import generate_topology

app = FastAPI(title="Quantum APMS Dispatch Engine")
_outage_counts_by_feeder: Dict[str, Dict[str, int]] = {}
_outage_counts_lock = Lock()

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

@app.post("/api/optimize-dispatch", response_model=DispatchResponse)
def optimize_grid(data: DispatchRequest):
    try:
        # Preserve historical outage counts between dispatches in this process.
        transformers_dict = [t.dict() for t in data.transformers]
        total_load_kw = sum(transformer["load_kw"] for transformer in transformers_dict)
        if data.load_shedding_kw > total_load_kw:
            raise HTTPException(status_code=422, detail="load_shedding_kw cannot exceed total transformer load")

        with _outage_counts_lock:
            outage_counts = dict(_outage_counts_by_feeder.get(data.feeder_id, {}))
        selected, optimization_info = quantum_dispatch(
            deficit_kw=data.load_shedding_kw,
            transformers=transformers_dict,
            outage_counts=outage_counts,
        )
        with _outage_counts_lock:
            feeder_history = _outage_counts_by_feeder.setdefault(data.feeder_id, {})
            for transformer_id, is_on in selected.items():
                if not is_on:
                    feeder_history[transformer_id] = feeder_history.get(transformer_id, 0) + 1

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
