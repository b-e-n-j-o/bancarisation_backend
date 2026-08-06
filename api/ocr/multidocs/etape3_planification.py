"""
etape3_planification.py — carte du dossier + registre → plan d'extraction.

100 % DÉTERMINISTE, volontairement. Le LLM a déjà fait la partie qu'il fait
bien (reconnaître ce qu'est un document) ; l'appariement rôle → extracteur est
une jointure, pas un jugement. La faire faire par un modèle ne rendrait que le
plan non rejouable et les erreurs muettes.

Sorties utiles :
  · jobs ordonnés par famille (contexte → règles → coût → obligations → réalisé) ;
  · non_couvert : segments cartographiés qu'AUCUN extracteur ne sait traiter.
    C'est un livrable en soi — c'est la liste de ce qu'il faut coder pour
    prendre en charge complètement un nouveau BE.
"""

from __future__ import annotations

import hashlib

from .etape2_triage import CarteDossier
from .utils.corpus import Corpus
from .utils.familles import ORDRE_FAMILLE, famille
from .utils.plan import Job, PlanExtraction
from .utils.registre import candidats

SEUIL_CONFIANCE_AUTO = 0.5


def planifier(
    carte: CarteDossier,
    corpus: Corpus,
    seuil: float = SEUIL_CONFIANCE_AUTO,
) -> PlanExtraction:
    plan = PlanExtraction()
    # (doc_id, extracteur) → job, pour fusionner les segments de même nature
    par_cle: dict[tuple[str, str], Job] = {}

    for seg in carte.segments:
        doc = corpus.doc(seg.doc_id)
        if doc is None:
            continue

        if seg.confiance < seuil:
            plan.avertissements.append(
                f"{seg.doc_id} / {seg.role.value} : confiance {seg.confiance:.2f} "
                f"< {seuil} → à faire valider avant extraction ({seg.description})"
            )

        possibles = candidats(seg.role, doc.format)
        if not possibles:
            plan.non_couvert.append(
                f"{seg.doc_id} ({doc.format}) / {seg.role.value} : aucun extracteur "
                f"— {seg.description}"
            )
            continue

        choisi = possibles[0]
        cle = (seg.doc_id, choisi.nom)
        locators = seg.locators or [b.locator for b in doc.blocs]

        if cle in par_cle:
            job = par_cle[cle]
            job.locators = _union(job.locators, locators, doc)
            job.raison += f" | {seg.description}"
            continue

        job = Job(
            job_id=_job_id(seg.doc_id, choisi.nom, locators),
            extracteur=choisi.nom,
            doc_id=seg.doc_id,
            role=seg.role,
            locators=locators,
            priorite=ORDRE_FAMILLE.get(famille(seg.role), 9),
            raison=seg.description,
            cout_estime=choisi.cout,
        )
        par_cle[cle] = job
        plan.jobs.append(job)

        # Tous les extracteurs candidats (ex. actions + échéances sur fiches_actions)
        for autre in possibles[1:]:
            cle_a = (seg.doc_id, autre.nom)
            if cle_a in par_cle:
                job_a = par_cle[cle_a]
                job_a.locators = _union(job_a.locators, locators, doc)
                continue
            job_a = Job(
                job_id=_job_id(seg.doc_id, autre.nom, locators),
                extracteur=autre.nom,
                doc_id=seg.doc_id,
                role=seg.role,
                locators=list(locators),
                priorite=ORDRE_FAMILLE.get(famille(seg.role), 9),
                raison=seg.description,
                cout_estime=autre.cout,
            )
            par_cle[cle_a] = job_a
            plan.jobs.append(job_a)

    if not any(famille(j.role).value == "regles" for j in plan.jobs):
        plan.avertissements.append(
            "Aucune source de RÈGLES identifiée : le dossier ne produira pas de "
            "calendrier. Vérifier le triage ou demander le plan de gestion au BE."
        )

    return plan


def _union(a: list[str], b: list[str], doc) -> list[str]:
    ordre = {bl.locator: i for i, bl in enumerate(doc.blocs)}
    return sorted(set(a) | set(b), key=lambda l: ordre.get(l, 10**6))


def _job_id(doc_id: str, extracteur: str, locators: list[str]) -> str:
    empreinte = hashlib.sha1(
        f"{doc_id}|{extracteur}|{','.join(sorted(locators))}".encode()
    ).hexdigest()[:8]
    return f"{extracteur}-{doc_id}-{empreinte}"


def resume(plan: PlanExtraction) -> str:
    lignes = [f"{len(plan.jobs)} job(s) planifié(s)"]
    for job in plan.par_priorite():
        lignes.append(
            f"  [{job.priorite}] {job.extracteur:<28} {job.doc_id:<28} "
            f"{len(job.locators):>4} bloc(s) · {job.role.value}"
        )
    for a in plan.avertissements:
        lignes.append(f"  ⚠ {a}")
    for n in plan.non_couvert:
        lignes.append(f"  ✗ non couvert : {n}")
    return "\n".join(lignes)