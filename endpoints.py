from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from main import generate_topology

app = FastAPI()

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