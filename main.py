from pathlib import Path

from dotenv import load_dotenv

# Charger backend/.env avant tout import qui lit os.environ
load_dotenv(Path(__file__).resolve().parent / ".env")

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import Error as PsycopgError

from api.bilan.router import router as bilan_suivi_router
from api.budget.router import router as budget_router
from api.dialogue.router import router as dialogue_router
from api.documents.route import router as documents_router
from api.ocr.router import router as ocr_router
from api.ocr.arrete.router import router as ocr_arrete_router
from api.ocr.match_prescriptions.router import router as match_prescriptions_router
from api.ocr.multidocs.router import router as multidocs_router
from api.ocr.ingest_erc.router import router as ingest_erc_router
from api.geomce.router import router as geomce_router
from api.geomce.router import router_exports as geomce_exports_router
from api.parc.router import router as parc_router
from api.controle.router import router as controle_router
from api.planning.router import router as planning_router
from api.prestataires.router import router as prestataires_router
from api.projets.geometries.router import router as geometries_router
from api.projets.router import router as projets_router
from api.cadastre.router import router as cadastre_router
from api.foncier.router import router as foncier_router
from api.satellite.router import router as satellite_router
from api.annotations.router import router as annotations_router
from api.journal_actions.router import router as journal_actions_router
from api.carto.router import router as cao_router
from api.admin.router import router as admin_router
from api.acces.router import router as acces_router
from api.diagnostic.router import router as diagnostic_router
from api.transfert.router import router as transfert_router
from auth.deps import JwtAuthMiddleware
from auth.errors import http_from_db
from security import IpDenylistMiddleware, docs_enabled

_docs = docs_enabled()

app = FastAPI(
    title="Bancarisation API",
    version="0.1.0",
    description="API backend pour la gestion des projets de bancarisation.",
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")

app.add_middleware(JwtAuthMiddleware)
app.add_middleware(IpDenylistMiddleware)
# Ajouté en dernier : exécuté en premier, pour que les pré-vols OPTIONS
# et les réponses 401 portent les en-têtes CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PsycopgError)
async def psycopg_http_handler(_request: Request, exc: PsycopgError) -> JSONResponse:
    mapped = http_from_db(exc)
    return JSONResponse({"detail": mapped.detail}, status_code=mapped.status_code)


@app.get("/health", tags=["health"])
def healthcheck() -> dict[str, bool]:
    return {"ok": True}


app.include_router(projets_router, prefix="/api", tags=["projets"])
app.include_router(geometries_router, prefix="/api", tags=["geometries"])
app.include_router(cadastre_router, prefix="/api", tags=["cadastre"])
app.include_router(foncier_router, prefix="/api", tags=["foncier"])
app.include_router(budget_router, prefix="/api", tags=["budget"])
app.include_router(bilan_suivi_router, prefix="/api", tags=["bilan-suivi"])
app.include_router(prestataires_router, prefix="/api", tags=["prestataires"])
app.include_router(documents_router, prefix="/api", tags=["documents"])
app.include_router(planning_router, prefix="/api", tags=["planning"])
app.include_router(ocr_router, prefix="/api", tags=["ocr"])
app.include_router(multidocs_router, prefix="/api", tags=["ocr-multidocs"])
app.include_router(ingest_erc_router, prefix="/api")
app.include_router(ocr_arrete_router, prefix="/api", tags=["ocr-arrete"])
app.include_router(match_prescriptions_router, prefix="/api", tags=["match-prescriptions"])
app.include_router(parc_router, prefix="/api", tags=["parc"])
app.include_router(controle_router, prefix="/api", tags=["controle"])
app.include_router(dialogue_router, prefix="/api", tags=["dialogue"])
app.include_router(geomce_router, prefix="/api", tags=["geomce"])
app.include_router(geomce_exports_router, prefix="/api", tags=["geomce"])
app.include_router(satellite_router, prefix="/api", tags=["satellite"])
app.include_router(annotations_router, prefix="/api", tags=["annotations"])
app.include_router(journal_actions_router, prefix="/api", tags=["journal"])
app.include_router(cao_router, prefix="/api", tags=["cao"])
app.include_router(admin_router, prefix="/api", tags=["admin"])
app.include_router(transfert_router, prefix="/api", tags=["transfert"])
app.include_router(acces_router, prefix="/api", tags=["acces"])
app.include_router(diagnostic_router, prefix="/api")
