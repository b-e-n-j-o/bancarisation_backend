"""
claims_vers_arrete.py — Ingestion des prescriptions (0 LLM).

Lit claims.jsonl (kind=prescription) → INSERT bancarisation.arrete
+ arrete_prescription. Au rejeu, remplace les arrêtés origine='ia'
du projet (les arrêtés saisis manuellement, origine='user', sont conservés).

Usage :
    python -m api.ocr.claims_vers_arrete <projet_id>
    python -m api.ocr.claims_vers_arrete <projet_id> --claims work/<id>/multidocs/claims.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .extractions.extract_arrete import arrete_depuis_claims, vers_lignes_db
from .multidocs.utils.claims import Kind

_OCR_DIR = Path(__file__).resolve().parent


def charger_groupes(chemin: Path) -> dict[str, dict]:
    """doc_id → {meta, prescriptions}."""
    groupes: dict[str, dict] = defaultdict(lambda: {"meta": {}, "prescriptions": []})
    for i, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            claim = json.loads(ligne)
        except json.JSONDecodeError as err:
            raise SystemExit(f"❌ JSONL invalide ligne {i} : {err}") from err
        if claim.get("kind") != Kind.prescription.value:
            continue
        doc_id = str(claim.get("doc_id") or f"doc-{i}")
        donnees = claim.get("donnees") or {}
        meta = donnees.get("arrete_meta")
        if isinstance(meta, dict) and meta:
            groupes[doc_id]["meta"] = meta
        if donnees.get("_meta_only"):
            continue
        propre = {
            k: v for k, v in donnees.items() if k not in ("arrete_meta", "_meta_only")
        }
        if propre:
            groupes[doc_id]["prescriptions"].append(propre)
    return dict(groupes)


def resoudre_claims_path(projet_id: str, claims: str | None) -> Path:
    if claims:
        p = Path(claims)
        if not p.is_absolute():
            candidat = _OCR_DIR / p
            p = candidat if candidat.exists() else p
        return p
    return _OCR_DIR / "work" / projet_id / "multidocs" / "claims.jsonl"


def executer(
    projet_id: str,
    claims_path: Path,
    *,
    replace: bool = True,
) -> dict:
    from api.controle.crud import ingerer_arretes_ia

    groupes = charger_groupes(claims_path)
    if not groupes:
        return {"arretes": 0, "prescriptions": 0, "ids": []}

    lots: list[tuple[dict, list[dict]]] = []
    avertissements: list[str] = []
    for doc_id, g in groupes.items():
        meta = dict(g.get("meta") or {})
        presc = list(g.get("prescriptions") or [])
        try:
            arrete = arrete_depuis_claims(meta, presc)
        except ValidationError as err:
            avertissements.append(f"{doc_id}: reconstruction Arrete KO ({err.error_count()} erreur(s))")
            continue
        arrete_row, presc_rows = vers_lignes_db(
            arrete,
            projet_id=projet_id,
        )
        lots.append((arrete_row, presc_rows))

    recap = ingerer_arretes_ia(
        UUID(projet_id),
        lots,
        replace_ia=replace,
    )
    recap["avertissements"] = avertissements
    recap["docs"] = len(groupes)
    return recap


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projet_id")
    ap.add_argument("--claims", default=None)
    ap.add_argument("--keep-ia", action="store_true", help="Ne pas supprimer les arrêtés IA existants.")
    args = ap.parse_args()
    path = resoudre_claims_path(args.projet_id, args.claims)
    if not path.is_file():
        raise SystemExit(f"claims introuvable : {path}")
    recap = executer(args.projet_id, path, replace=not args.keep_ia)
    print(json.dumps(recap, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
