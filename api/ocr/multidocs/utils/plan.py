"""
plan.py — Objets du plan d'extraction.

Un JOB = (extracteur, document, plage de blocs). C'est l'unité rejouable,
cachable et mesurable du système : on peut relancer un job seul, comparer deux
versions d'un extracteur sur le même job, chiffrer son coût.

Le CONTEXTE est ce qu'on injecte à chaque extracteur en plus de son texte :
T0, unités de gestion connues, référentiel de codes mesure déjà repérés,
claims déjà produits par les jobs de priorité inférieure. C'est ce qui permet
au budget de se raccrocher aux actions sans jamais réconcilier lui-même.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field

from .claims import Claim
from .corpus import Corpus, DocumentNormalise
from .familles import RoleDoc


class Job(BaseModel):
    job_id: str
    extracteur: str
    doc_id: str
    role: RoleDoc
    locators: list[str] = Field(default_factory=list)
    priorite: int = 5
    raison: str = ""
    cout_estime: str = "moyen"

    @property
    def cle_cache(self) -> str:
        return f"{self.extracteur}:{self.doc_id}:{','.join(self.locators)}"


class PlanExtraction(BaseModel):
    jobs: list[Job] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)
    non_couvert: list[str] = Field(
        default_factory=list,
        description="Segments cartographiés sans extracteur capable de les traiter "
                    "— trous de couverture connus, à afficher, jamais à masquer",
    )

    def par_priorite(self) -> list[Job]:
        return sorted(self.jobs, key=lambda j: (j.priorite, j.doc_id))


@dataclass
class ZoneSigContexte:
    """Zone confirmée issue du dépôt SIG — injectée dans les prompts règles.

    ``ug_id`` = code canonique en base (ug1, ug2…).
    ``libelle`` / ``source_fichier`` / ``nom_source`` = libellés humains pour
    matcher le texte du plan de gestion.
    """

    ug_id: str
    nom_source: str
    categorie: str = "autre"  # compensation|evitement|reduction|accompagnement|autre
    surface_ha: Optional[float] = None
    cible: Optional[str] = None
    libelle: str = ""
    source_fichier: Optional[str] = None


@dataclass
class Contexte:
    """Passé à chaque extracteur. Non sérialisable (porte le compteur de coût)."""

    corpus: Corpus
    document: DocumentNormalise
    parametres: dict = field(default_factory=dict)      # annee_t0, duree_ans…
    referentiel_actions: dict = field(default_factory=dict)  # code → libellé
    unites_gestion: list[str] = field(default_factory=list)
    zones_sig: list[ZoneSigContexte] = field(default_factory=list)
    claims_amont: list[Claim] = field(default_factory=list)
    compteur: Optional[object] = None                   # api.ocr.mistral_client.Compteur
    debug_dir: Optional[object] = None

    def texte(self, job: Job) -> str:
        """Le markdown ancré que voit l'extracteur."""
        return self.document.rendu(job.locators or None)

    def entete_contexte(self) -> str:
        """Bloc de contexte injecté en tête de prompt. Court : c'est du budget
        de tokens payé sur CHAQUE appel du fan-out."""
        morceaux = []
        if self.parametres:
            morceaux.append("Paramètres du dossier : " + ", ".join(
                f"{k}={v}" for k, v in self.parametres.items()))
        if self.zones_sig:
            morceaux.append(self._bloc_zones_sig())
        elif self.unites_gestion:
            morceaux.append(
                "Unités de gestion connues (codes) : " + ", ".join(self.unites_gestion)
                + ' — utilise uniquement ces codes, sinon ["a_definir"].'
            )
        if self.referentiel_actions:
            morceaux.append("Codes mesure déjà repérés : " + ", ".join(
                f"{c} = {l}" for c, l in list(self.referentiel_actions.items())[:60]))
        return "\n".join(morceaux)

    def _bloc_zones_sig(self) -> str:
        from api.ocr.domain.ug_ids import formater_referentiel_ug_pour_prompt

        return formater_referentiel_ug_pour_prompt(self.zones_sig)

    def alias_ug(self) -> dict[str, str]:
        """libellé / nom fichier / nom_source (minuscule) → ug_id."""
        alias: dict[str, str] = {}
        for z in self.zones_sig:
            if not z.ug_id:
                continue
            for cle in (z.ug_id, z.libelle, z.nom_source, z.source_fichier or ""):
                c = (cle or "").strip().lower()
                if c and c not in alias:
                    alias[c] = z.ug_id
            # stem fichier sans extension
            src = (z.source_fichier or "").strip()
            if src and "." in src:
                stem = src.rsplit(".", 1)[0].strip().lower()
                if stem and stem not in alias:
                    alias[stem] = z.ug_id
        return alias

    def ug_ids_connus(self) -> set[str]:
        return {z.ug_id for z in self.zones_sig if z.ug_id} | set(self.unites_gestion)