"""
etape4_extraction.py — Exécute le plan : jobs → claims.jsonl.

Le référentiel d'actions se remplit au fil de l'eau : les jobs 'règles'
passent avant les jobs 'coût', donc le budget reçoit les codes mesure déjà
connus sans qu'aucun extracteur n'ait eu à réconcilier quoi que ce soit.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from . import extracteurs  # noqa: F401  — peuple le REGISTRE
from .utils.claims import Claim, Kind, LotClaims
from .utils.corpus import Corpus
from .utils.plan import Contexte, PlanExtraction, ZoneSigContexte
from .utils.registre import catalogue, get
from .etape2_triage import CarteDossier


def executer(
    plan: PlanExtraction,
    corpus: Corpus,
    carte: CarteDossier,
    sortie: Path,
    debug_dir: Path | None,
    zones_sig: list[ZoneSigContexte] | None = None,
    compteur=None,
    on_job=None,
) -> list[Claim]:
    """Exécute les jobs par ordre de priorité, en propageant le contexte.

    on_job(job, ext, doc) — rappel optionnel avant chaque job (suivi UI).
    """
    from ..mistral_client import DEFAULT_MODEL, PRIX, PRIX_DEFAUT, Compteur

    owned = compteur is None
    if owned:
        compteur = Compteur(DEFAULT_MODEL, *PRIX.get(DEFAULT_MODEL, PRIX_DEFAUT))
    parametres = carte.parametres_dict()
    referentiel: dict[str, str] = {}
    tous: list[Claim] = []
    zones = list(zones_sig or [])

    with (sortie / "claims.jsonl").open("w", encoding="utf-8") as f:
        for job in plan.par_priorite():
            ext = get(job.extracteur)
            doc = corpus.doc(job.doc_id)
            if ext is None or doc is None:
                continue

            if on_job is not None:
                try:
                    on_job(job, ext, doc)
                except Exception:  # noqa: BLE001
                    pass

            ctx = Contexte(
                corpus=corpus,
                document=doc,
                parametres=parametres,
                referentiel_actions=dict(referentiel),
                unites_gestion=[z.ug_id for z in zones if z.ug_id],
                zones_sig=zones,
                claims_amont=list(tous),
                compteur=compteur,
                debug_dir=debug_dir,
            )

            print(f"\n▶ {job.job_id} ({len(job.locators)} bloc(s), {ext.cout})", file=sys.stderr)
            payload = ctx.texte(job)
            n_car = len(payload)
            print(
                f"  📦 payload LLM : {n_car:,} car. texte OCR "
                f"(~{max(1, n_car // 4):,} tokens estimés) "
                f"— pas le PDF binaire",
                file=sys.stderr,
                flush=True,
            )
            t0 = time.time()
            try:
                claims = ext.fn(job, ctx)
                avertissements: list[str] = []
            except Exception as err:  # noqa: BLE001
                claims, avertissements = [], [f"échec : {err}"]
                print(f"  ✗ {err}", file=sys.stderr)

            lot = LotClaims(
                job_id=job.job_id, extracteur=ext.nom, version=ext.version,
                claims=claims, avertissements=avertissements,
                duree_s=round(time.time() - t0, 1),
            )
            for c in lot.claims:
                f.write(c.model_dump_json() + "\n")
            tous.extend(lot.claims)

            for c in lot.claims:
                if c.kind in (Kind.action, Kind.echeance_regle) and c.cle_locale:
                    referentiel.setdefault(
                        c.cle_locale,
                        str(c.donnees.get("libelle") or c.donnees.get("titre") or "")[:120],
                    )

            print(f"  {len(lot.claims)} claim(s) en {lot.duree_s}s", file=sys.stderr)

    if owned:
        print(compteur.rapport(), file=sys.stderr)
    return tous


__all__ = ["executer", "catalogue"]
