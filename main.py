from importlib import util
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def _load_router(module_name: str, route_path: Path):
    spec = util.spec_from_file_location(module_name, route_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Impossible de charger la route: {route_path}")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.router


projets_router = _load_router(
    "projets_route_module",
    Path(__file__).parent / "api" / "projets" / "router.py",
)
documents_router = _load_router(
    "documents_route_module",
    Path(__file__).parent / "api" / "documents" / "route.py",
)
planning_router = _load_router(
    "planning_route_module",
    Path(__file__).parent / "api" / "planning.py" / "router.py",
)


app = FastAPI(
    title="Bancarisation API",
    version="0.1.0",
    description="API backend pour la gestion des projets de bancarisation.",
)

# A ajuster avec l'URL de prod du front plus tard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def healthcheck() -> dict[str, bool]:
    return {"ok": True}


app.include_router(projets_router, prefix="/api", tags=["projets"])
app.include_router(documents_router, prefix="/api", tags=["documents"])
app.include_router(planning_router, prefix="/api", tags=["planning"])
