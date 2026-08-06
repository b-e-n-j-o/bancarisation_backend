"""
registre.py — Catalogue déclaratif des extracteurs.

Un extracteur déclare : les rôles documentaires qu'il sait traiter, les formats
qu'il accepte, les kinds de claims qu'il produit, et son coût. Le planificateur
ne fait qu'apparier (rôle, format) → extracteur.

Décision d'architecture : LE LLM NE CHOISIT PAS L'EXTRACTEUR PAR SON NOM.
Il cartographie (quel rôle, quelle plage), le planificateur déterministe fait
l'appariement contre ce registre. Laisser un modèle inventer un nom
d'extracteur n'a que des inconvénients : hallucination possible, plan non
rejouable, aucun message d'erreur exploitable. La partie "agentique" est en
amont (comprendre le dossier), pas dans le routage.

Ajouter la prise en charge d'un nouveau BE = enregistrer un extracteur
supplémentaire, jamais toucher à l'orchestrateur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional, Protocol

from .claims import Kind
from .familles import Famille, RoleDoc, famille


class FonctionExtracteur(Protocol):
    def __call__(self, job, ctx) -> list:  # -> list[Claim]
        ...


@dataclass(frozen=True)
class Extracteur:
    nom: str
    version: str
    roles: tuple[RoleDoc, ...]
    formats: tuple[str, ...]
    produit: tuple[Kind, ...]
    description: str
    fn: FonctionExtracteur
    cout: Literal["leger", "moyen", "lourd"] = "moyen"
    fan_out: bool = field(
        default=False,
        metadata={"aide": "True si l'extracteur traite les blocs un par un"},
    )

    @property
    def familles(self) -> set[Famille]:
        return {famille(r) for r in self.roles}

    @property
    def specificite(self) -> int:
        """Moins il couvre de rôles, plus il est spécialisé — donc prioritaire."""
        return len(self.roles)


REGISTRE: dict[str, Extracteur] = {}


def extracteur(
    *,
    nom: str,
    version: str,
    roles: tuple[RoleDoc, ...],
    formats: tuple[str, ...],
    produit: tuple[Kind, ...],
    description: str,
    cout: Literal["leger", "moyen", "lourd"] = "moyen",
    fan_out: bool = False,
) -> Callable:
    def decorateur(fn: FonctionExtracteur) -> FonctionExtracteur:
        if nom in REGISTRE:
            raise ValueError(f"Extracteur déjà enregistré : {nom}")
        REGISTRE[nom] = Extracteur(
            nom=nom, version=version, roles=roles, formats=formats,
            produit=produit, description=description, fn=fn,
            cout=cout, fan_out=fan_out,
        )
        return fn

    return decorateur


def candidats(role: RoleDoc, format_doc: str) -> list[Extracteur]:
    """Extracteurs capables de traiter ce (rôle, format), du plus spécialisé au
    plus générique."""
    trouves = [
        e for e in REGISTRE.values()
        if role in e.roles and format_doc in e.formats
    ]
    return sorted(trouves, key=lambda e: (e.specificite, e.nom))


def get(nom: str) -> Optional[Extracteur]:
    return REGISTRE.get(nom)


def catalogue() -> str:
    lignes = ["| extracteur | version | rôles | formats | produit |",
              "| --- | --- | --- | --- | --- |"]
    for e in sorted(REGISTRE.values(), key=lambda x: x.nom):
        lignes.append(
            f"| {e.nom} | {e.version} | {', '.join(r.value for r in e.roles)} | "
            f"{', '.join(e.formats)} | {', '.join(k.value for k in e.produit)} |"
        )
    return "\n".join(lignes)