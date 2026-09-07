"""
arrete.py — Extracteur des obligations d'un arrêté / autorisation environnementale.

Famille `obligations` : claims Kind.prescription uniquement.
L'arrêté n'écrit JAMAIS le calendrier (pas d'échéance, pas d'occurrence).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..utils.claims import Claim, Kind, enregistrer_payload
from ..utils.familles import RoleDoc
from ..utils.registre import extracteur

if TYPE_CHECKING:
    from ..utils.plan import Contexte, Job

VERSION = "0.1.0"


def _enregistrer_payloads() -> None:
    from ...extractions.extract_arrete import Prescription

    enregistrer_payload(Kind.prescription, Prescription)


@extracteur(
    nom="prescriptions_arrete",
    version=VERSION,
    roles=(RoleDoc.arrete, RoleDoc.autorisation_environnementale),
    formats=("pdf", "docx", "md"),
    produit=(Kind.prescription,),
    description="Checklist des obligations opposables (arrêté / AE). "
                "Jamais d'occurrence calendaire.",
    cout="lourd",
)
def prescriptions_arrete(job: "Job", ctx: "Contexte") -> list[Claim]:
    from ...extractions.extract_arrete import extraire_depuis_markdown

    _enregistrer_payloads()
    markdown = ctx.texte(job)
    debug = ctx.debug_dir
    arrete = extraire_depuis_markdown(
        markdown,
        compteur=ctx.compteur,
        debug_dir=debug,
    )

    meta = arrete.model_dump(mode="json")
    meta.pop("prescriptions", None)

    ancres_job = [ctx.document.ancre(l) for l in job.locators]
    claims: list[Claim] = []

    if not arrete.prescriptions:
        claims.append(
            Claim(
                kind=Kind.prescription,
                donnees={"_meta_only": True, "arrete_meta": meta},
                doc_id=ctx.document.doc_id,
                extracteur="prescriptions_arrete",
                version=VERSION,
                ancres=ancres_job,
                confiance=0.4,
                avertissements=list(arrete.avertissements),
                cle_locale="ARRETE-META",
            )
        )
        return claims

    for p in arrete.prescriptions:
        claim = Claim.depuis(
            p,
            kind=Kind.prescription,
            doc_id=ctx.document.doc_id,
            extracteur="prescriptions_arrete",
            version=VERSION,
            ancres=_ancres_page(p.page, ctx) or ancres_job,
            confiance=p.confiance,
            champs_a_confirmer=[] if p.confiance >= 0.6 else ["interpretation"],
            avertissements=[p.remarque] if p.remarque else [],
            cle_locale=p.code,
        )
        claim.donnees["arrete_meta"] = meta
        claims.append(claim)

    return claims


def _ancres_page(page: int | None, ctx) -> list[str]:
    if page is None:
        return []
    for bloc in ctx.document.blocs:
        loc = (bloc.locator or "").lower()
        if loc in {f"p{page}", f"page{page}", f"p{page:02d}"} or loc.endswith(
            f"-p{page}"
        ):
            return [ctx.document.ancre(bloc.locator)]
    return [ctx.document.ancre(f"p{page}")]
