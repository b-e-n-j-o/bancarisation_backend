"""
plan_gestion.py — Extracteur unifié : distributeur + scribes.

Un seul job pour le rôle plan_gestion / fiches_actions :
  1. LLM lourd → bornes OCR + échéances (récurrence)
  2. Python coupe chaque fiche dans le markdown
  3. LLM léger (small, effort none) × N → contenu_propre stockable
  4. Claims Kind.action + Kind.echeance_regle

Les anciens extracteurs séparés (actions_plan_gestion / echeances_plan_gestion)
sont remplacés par `plan_gestion` pour éviter un double appel lourd.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..utils.claims import Claim, Kind, enregistrer_payload
from ..utils.familles import RoleDoc
from ..utils.registre import extracteur

if TYPE_CHECKING:
    from ..utils.plan import Contexte, Job

VERSION = "0.3.0"
RE_CODE = re.compile(r"\b([A-Z]{2,3})\s?(\d{1,2})\b")


def _enregistrer_payloads() -> None:
    from ...models import ActionFiche, Echeance

    enregistrer_payload(Kind.echeance_regle, Echeance)
    enregistrer_payload(Kind.action, ActionFiche)


@extracteur(
    nom="plan_gestion",
    version=VERSION,
    roles=(RoleDoc.plan_gestion, RoleDoc.fiches_actions),
    formats=("pdf", "docx", "md"),
    produit=(Kind.action, Kind.echeance_regle),
    description="Distributeur (bornes + échéances) puis scribes légers pour le "
                "contenu propre de chaque fiche-action.",
    cout="lourd",
)
def plan_gestion(job: "Job", ctx: "Contexte") -> list[Claim]:
    from ...extractions.extract_plan_gestion_v2 import extraire

    _enregistrer_payloads()
    markdown = ctx.texte(job)
    debug = ctx.debug_dir
    actions, echeances = extraire(
        markdown,
        compteur=ctx.compteur,
        debug_dir=debug,
        entete_contexte=ctx.entete_contexte(),
        ug_ids_connus=ctx.ug_ids_connus(),
        alias_ug=ctx.alias_ug(),
    )

    ancres_job = [ctx.document.ancre(l) for l in job.locators]
    claims: list[Claim] = []

    for a in actions:
        claims.append(
            Claim.depuis(
                a,
                kind=Kind.action,
                doc_id=ctx.document.doc_id,
                extracteur="plan_gestion",
                version=VERSION,
                ancres=_ancres_pages(a, ctx) or ancres_job,
                confiance=getattr(a, "confiance", 1.0),
                champs_a_confirmer=list(getattr(a, "champs_a_confirmer", []) or []),
                avertissements=list(getattr(a, "avertissements", []) or []),
                cle_locale=getattr(a, "id", None),
            )
        )

    for e in echeances:
        claims.append(
            Claim.depuis(
                e,
                kind=Kind.echeance_regle,
                doc_id=ctx.document.doc_id,
                extracteur="plan_gestion",
                version=VERSION,
                ancres=_ancres_de(e, ctx, ancres_job),
                confiance=getattr(e, "confiance", 1.0),
                champs_a_confirmer=list(getattr(e, "champs_a_confirmer", []) or []),
                avertissements=list(getattr(e, "avertissements", []) or []),
                cle_locale=getattr(e, "code_operation", None) or getattr(e, "id", None),
            )
        )

    return claims


# --- ancrage ---------------------------------------------------------------


def _ancres_de(objet, ctx, defaut: list[str]) -> list[str]:
    explicites = getattr(objet, "ancres", None)
    if explicites:
        return list(explicites)
    return _ancres_pages(objet, ctx) or defaut


def _ancres_pages(objet, ctx) -> list[str]:
    """Convertit un champ `pages` ("12-14" ou [12, 13]) en ancres."""
    pages = getattr(objet, "pages", None)
    if not pages:
        source = getattr(objet, "source", None)
        page = getattr(source, "page", None) if source is not None else None
        if page is None and isinstance(source, dict):
            page = source.get("page")
        if page is not None:
            pages = [page]
        else:
            return []
    numeros: list[int] = []
    if isinstance(pages, str):
        for morceau in re.split(r"[;,]", pages):
            bornes = [int(n) for n in re.findall(r"\d+", morceau)]
            if len(bornes) == 2:
                numeros.extend(range(bornes[0], bornes[1] + 1))
            elif bornes:
                numeros.append(bornes[0])
    else:
        numeros = [int(p) for p in pages if str(p).isdigit() or isinstance(p, int)]
    return [
        ctx.document.ancre(f"p{n}")
        for n in numeros
        if ctx.document.bloc(f"p{n}") is not None
    ]


# --- utilitaire conservé (tests / debug découpe) ---------------------------


def segmenter_fiches(ctx: "Contexte", job: "Job") -> list[list[str]]:
    """Regroupe les blocs de page en fiches-actions via titres à code mesure.

    Conservé pour debug / fallback éventuel — le flux prod utilise
    `extract_plan_gestion_v2.couper_fiche` (bornes puis code).
    """
    segments: list[list[str]] = []
    courant: list[str] = []
    for loc in job.locators:
        bloc = ctx.document.bloc(loc)
        if bloc is None:
            continue
        titres = bloc.meta.get("titres") or ([bloc.titre] if bloc.titre else [])
        debut_fiche = any(RE_CODE.search(t or "") for t in titres)
        if debut_fiche and courant:
            segments.append(courant)
            courant = []
        courant.append(loc)
    if courant:
        segments.append(courant)
    return segments if len(segments) > 1 else []
