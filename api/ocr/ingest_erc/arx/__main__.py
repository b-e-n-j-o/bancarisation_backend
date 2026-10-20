"""Analyse un arrêté déjà déposé, hors uvicorn.

Le rechargement du serveur coupe le fil lancé par l'interface. Ce processus
reste en vie tant que Mistral n'a pas répondu, et écrit le même status.json
et arrete.json : la page d'analyse les retrouve ensuite.

Depuis le dossier backend, avec le même environnement que le serveur :

    python -m api.ocr.ingest_erc.arx /chemin/arrete.pdf
    python -m api.ocr.ingest_erc.arx DOCUMENT_ID

Un chemin de PDF ne touche ni la base ni la session : seule la clé Mistral du .env est lue.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[4] / ".env")

from api.ocr.ingest_erc.arx.jobs import WORK_ROOT, _importer, relancer  # noqa: E402


def _run_pdf(pdf: Path) -> int:
    if not pdf.is_file():
        print(f"PDF introuvable : {pdf}", file=sys.stderr)
        return 1
    Settings, run = _importer()
    settings = Settings()
    settings.runs_dir = WORK_ROOT / "local"
    logging.info("arx_lite PDF local %s", pdf)
    _res, run_dir = run(pdf, settings, log=lambda message: logging.info("arx_lite — %s", message))
    print(f"terminé — {run_dir / 'arrete.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] in {"-h", "--help"}:
        print("usage: python -m api.ocr.ingest_erc.arx PDF | DOCUMENT_ID", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cible = Path(args[0])
    if cible.suffix.lower() == ".pdf" or cible.exists():
        return _run_pdf(cible)
    try:
        statut = relancer(args[0], arriere_plan=False)
    except (LookupError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    if statut.get("status") != "done":
        print(statut.get("erreur") or statut.get("status"), file=sys.stderr)
        return 1
    print(f"terminé — {statut.get('result_path')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
