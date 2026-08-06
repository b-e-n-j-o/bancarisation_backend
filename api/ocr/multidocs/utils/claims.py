"""
claims.py — Le contrat de sortie commun à TOUS les extracteurs.

Règle d'or, généralisée depuis ton prompt budget ("NE RÉCONCILIE JAMAIS LES
TABLES ENTRE ELLES") : un extracteur AFFIRME, il ne réconcilie pas.

Si la même action apparaît dans le plan de gestion et dans le planning
financier, ça fait DEUX claims, pas une fusion. Le rapprochement est un étage
séparé (résolution d'entités), avec sa hiérarchie d'autorité et sa remontée de
conflits. C'est exactement ce qui a fait exploser ton premier test budget :
on demandait au modèle d'arbitrer des contradictions au moment où il extrayait.

Un claim porte toujours quatre choses :
  · d'où il vient   (doc_id + ancres)  → vérifiable par le chargé du BE
  · qui l'a produit (extracteur@version) → rejouable, mesurable en éval
  · à quel point    (confiance, champs_a_confirmer) → priorisation de la revue
  · quoi           (kind + donnees)   → payload validé par un modèle Pydantic
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Kind(str, Enum):
    """Ce qu'un claim peut affirmer. Le mapping vers les tables Supabase se fait
    à l'étage de réconciliation, pas ici."""

    parametre_dossier = "parametre_dossier"  # T0, durée, périmètre, devise
    unite_gestion = "unite_gestion"
    action = "action"                        # fiche-action (le "quoi")
    echeance_regle = "echeance_regle"        # règle datée/récurrente (le "quand")
    occurrence_datee = "occurrence_datee"    # date ferme isolée (jalon, butoir)
    ligne_budget = "ligne_budget"
    prescription = "prescription"            # obligation issue d'un arrêté
    fait_realise = "fait_realise"            # statut / montant constaté
    reference_croisee = "reference_croisee"  # "le tableau p.62 récapitule les actions"


class Claim(BaseModel):
    claim_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: Kind
    donnees: dict

    doc_id: str
    ancres: list[str] = Field(
        default_factory=list,
        description="Ancres complètes 'doc_id#locator' justifiant l'affirmation",
    )

    extracteur: str
    version: str
    confiance: float = Field(default=1.0, ge=0, le=1)
    champs_a_confirmer: list[str] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)

    cle_locale: Optional[str] = Field(
        None,
        description="Identifiant donné par l'extracteur dans son propre référentiel "
                    "(ex. 'SE 1'), utilisé comme clé de blocking à la réconciliation",
    )

    @classmethod
    def depuis(
        cls,
        payload: BaseModel,
        *,
        kind: Kind,
        doc_id: str,
        extracteur: str,
        version: str,
        ancres: Optional[list[str]] = None,
        **kw,
    ) -> "Claim":
        return cls(
            kind=kind,
            donnees=payload.model_dump(mode="json"),
            doc_id=doc_id,
            ancres=ancres or [],
            extracteur=extracteur,
            version=version,
            **kw,
        )

    def valider(self) -> BaseModel:
        """Revalide le payload contre le modèle enregistré pour ce kind.
        Sert de garde-fou quand on relit des claims depuis le disque."""
        modele = PAYLOADS.get(self.kind)
        if modele is None:
            raise KeyError(f"Aucun modèle de payload enregistré pour {self.kind}")
        return modele.model_validate(self.donnees)


# --- registre des payloads -------------------------------------------------
# Rempli par les extracteurs à l'import (evite que ce module connaisse models.py).

PAYLOADS: dict[Kind, type[BaseModel]] = {}


def enregistrer_payload(kind: Kind, modele: type[BaseModel]) -> None:
    existant = PAYLOADS.get(kind)
    if existant is not None and existant is not modele:
        raise ValueError(
            f"Deux payloads concurrents pour {kind} : {existant.__name__} / {modele.__name__}. "
            "Un kind = un contrat. Crée un nouveau kind si le contrat diffère."
        )
    PAYLOADS[kind] = modele


# --- paramètres du dossier -------------------------------------------------


class ParametreDossier(BaseModel):
    """T0 et cadre temporel. Le paramètre le plus critique de tout le système :
    beaucoup de plans expriment du relatif ("N+2", "pendant 30 ans à compter de
    la signature"). On stocke l'offset ET la référence, on résout à la
    génération — si le T0 bouge, tout le calendrier se recale sans réextraction."""

    cle: str = Field(description="annee_t0, duree_ans, origine_t0, perimetre, devise…")
    valeur: str
    justification: Optional[str] = None


enregistrer_payload(Kind.parametre_dossier, ParametreDossier)


class LotClaims(BaseModel):
    """Sortie d'un job. Sérialisé en JSONL pour l'audit et les évals."""

    job_id: str
    extracteur: str
    version: str
    claims: list[Claim] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)
    cout_usd: Optional[float] = None
    duree_s: Optional[float] = None