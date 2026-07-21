from pathlib import Path

from dotenv import load_dotenv

# Charger backend/.env avant tout import qui lit os.environ
load_dotenv(Path(__file__).resolve().parent / ".env")

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.budget.router import router as budget_router
from api.documents.route import router as documents_router
from api.ocr.router import router as ocr_router
from api.planning.router import router as planning_router
from api.prestataires.router import router as prestataires_router
from api.projets.geometries.router import router as geometries_router
from api.projets.router import router as projets_router

app = FastAPI(
    title="Bancarisation API",
    version="0.1.0",
    description="API backend pour la gestion des projets de bancarisation.",
)

origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def healthcheck() -> dict[str, bool]:
    return {"ok": True}


app.include_router(projets_router, prefix="/api", tags=["projets"])
app.include_router(geometries_router, prefix="/api", tags=["geometries"])
app.include_router(budget_router, prefix="/api", tags=["budget"])
app.include_router(prestataires_router, prefix="/api", tags=["prestataires"])
app.include_router(documents_router, prefix="/api", tags=["documents"])
app.include_router(planning_router, prefix="/api", tags=["planning"])
app.include_router(ocr_router, prefix="/api", tags=["ocr"])
