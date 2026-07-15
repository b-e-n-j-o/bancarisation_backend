#!/usr/bin/env python3
"""
lier_echeances_actions.py — Liaison déterministe échéances ↔ actions (0 LLM).

Matche par code normalisé (TU1, SE1…) entre echeances.json et actions.json.

Usage :
    python lier_echeances_actions.py
    python lier_echeances_actions.py echeances_mistral.json actions.json --out echeances_liees.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .models import ActionsResult, EcheanceLiee, EcheancesLieesResult, ExtractionResult

_SCRIPT_DIR = Path(__file__).resolve().parent

log = logging.getLogger("pipeline.liaison")

DEFAULT_ECHEANCES = _SCRIPT_DIR / "echeances_mistral.json"
DEFAULT_ACTIONS = _SCRIPT_DIR / "actions.json"
DEFAULT_OUTPUT = _SCRIPT_DIR / "echeances_liees.json"


def _resoudre_chemin(chemin: str | Path) -> Path:
    p = Path(chemin)
    if p.is_absolute():
        return p
    if p.exists():
        return p.resolve()
    candidat = _SCRIPT_DIR / p
    return candidat if candidat.exists() else p


def _index_actions(actions: ActionsResult) -> dict[str, str]:
    """code normalisé → action id."""
    idx: dict[str, str] = {}
    for a in actions.actions:
        idx[a.code] = a.id
        idx[a.id] = a.id
    return idx


def lier(
    echeances: ExtractionResult,
    actions: ActionsResult,
) -> EcheancesLieesResult:
    idx = _index_actions(actions)
    liees: list[EcheanceLiee] = []
    liaisons: dict[str, str] = {}
    orphelines: list[str] = []

    for e in echeances.echeances:
        code = e.code_operation  # déjà normalisé TU1
        action_id = idx.get(code)
        if not action_id:
            orphelines.append(e.id)
            log.warning("Échéance orpheline %s (code %s)", e.id, code)
        else:
            liaisons[e.id] = action_id

        liees.append(EcheanceLiee(**e.model_dump(), action_id=action_id))

    return EcheancesLieesResult(
        echeances=liees,
        liaisons=liaisons,
        orphelines=orphelines,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Lie échéances et actions par code.")
    p.add_argument("echeances", nargs="?", default=str(DEFAULT_ECHEANCES))
    p.add_argument("actions", nargs="?", default=str(DEFAULT_ACTIONS))
    p.add_argument("--out", default=str(DEFAULT_OUTPUT))
    args = p.parse_args()

    src_e = _resoudre_chemin(args.echeances)
    src_a = _resoudre_chemin(args.actions)
    if not src_e.exists():
        sys.exit(f"❌ Échéances introuvables : {src_e}")
    if not src_a.exists():
        sys.exit(f"❌ Actions introuvables : {src_a}")

    echeances = ExtractionResult.model_validate_json(src_e.read_text(encoding="utf-8"))
    actions = ActionsResult.model_validate_json(src_a.read_text(encoding="utf-8"))

    resultat = lier(echeances, actions)

    out = _resoudre_chemin(args.out)
    if not out.is_absolute() and not Path(args.out).exists():
        out = _SCRIPT_DIR / args.out
    out.write_text(resultat.model_dump_json(indent=2), encoding="utf-8")

    print(f"🔗 {len(resultat.liaisons)} liaison(s) · {len(resultat.orphelines)} orpheline(s)")
    for eid, aid in sorted(resultat.liaisons.items()):
        print(f"   {eid:<42} → {aid}")
    if resultat.orphelines:
        print("   Orphelines :")
        for oid in resultat.orphelines:
            print(f"   · {oid}")
    print(f"✅ → {out}")


if __name__ == "__main__":
    main()
