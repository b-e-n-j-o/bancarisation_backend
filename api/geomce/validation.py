"""Validation pré-vol GéoMCE — idempotent, sans effet de bord."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from api.geomce.constantes import (
    CIBLE_SEP,
    CIBLES_FERMEES,
    DBF_WIDTHS,
    CHAMP_VIDE,
    SRID_LABELS,
    SRID_METROPOLE,
    SRID_PAR_DEPT,
)

Niveau = Literal["bloquant", "avertissement", "ok"]


@dataclass
class ControleItem:
    code: str
    niveau: Niveau
    message: str
    action: str | None = None
    champ: str | None = None


@dataclass
class RapportControle:
    bloquants: list[ControleItem] = field(default_factory=list)
    avertissements: list[ControleItem] = field(default_factory=list)
    ok_count: int = 0
    srid: int | None = None
    srid_label: str | None = None
    nb_polygones: int = 0
    surface_ha: float | None = None
    attributs_apercu: dict[str, str] = field(default_factory=dict)
    peut_exporter: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "bloquants": [asdict(x) for x in self.bloquants],
            "avertissements": [asdict(x) for x in self.avertissements],
            "ok_count": self.ok_count,
            "srid": self.srid,
            "srid_label": self.srid_label,
            "nb_polygones": self.nb_polygones,
            "surface_ha": self.surface_ha,
            "attributs_apercu": self.attributs_apercu,
            "peut_exporter": self.peut_exporter,
        }


def resolve_srid(departement: str | None) -> tuple[int | None, str | None]:
    if not departement or not str(departement).strip():
        return None, None
    dep = str(departement).strip().upper()
    # 2A / 2B → métropole
    if dep in ("2A", "2B") or (dep.isdigit() and len(dep) == 2):
        return SRID_METROPOLE, SRID_LABELS[SRID_METROPOLE]
    if dep in SRID_PAR_DEPT:
        srid = SRID_PAR_DEPT[dep]
        return srid, SRID_LABELS.get(srid, f"EPSG:{srid}")
    if dep.isdigit() and len(dep) == 3:
        # DOM non listé
        return None, None
    # Départements métropolitains à 2 chiffres ou corse déjà traités
    if re.fullmatch(r"0?\d{1,2}", dep):
        return SRID_METROPOLE, SRID_LABELS[SRID_METROPOLE]
    return None, None


def tronquer(valeur: str | None, width: int) -> tuple[str, bool]:
    raw = (valeur or "").strip()
    if not raw:
        return CHAMP_VIDE, False
    if len(raw) <= width:
        return raw, False
    return raw[:width], True


def joindre_cibles(cibles: list[str] | None) -> str:
    if not cibles:
        return CHAMP_VIDE
    clean = [c.strip() for c in cibles if c and c.strip()]
    if not clean:
        return CHAMP_VIDE
    return CIBLE_SEP.join(clean)


def build_attributs(
    *,
    nom: str | None,
    cibles: list[str] | None,
    description: str | None,
    decision: str | None,
    refei: str | None,
    categorie: str | None,
    mode: str = "complet",
) -> tuple[dict[str, str], list[ControleItem]]:
    """Construit les 7 champs DBF + avertissements de troncature / tirets."""
    warns: list[ControleItem] = []
    if mode == "geometrie_seule":
        nom_out, _ = tronquer(nom, DBF_WIDTHS["NOM"])
        return {
            "ID": "1",
            "NOM": nom_out if nom_out != CHAMP_VIDE else CHAMP_VIDE,
            "CIBLE": CHAMP_VIDE,
            "DESCRIPTIO": CHAMP_VIDE,
            "DECISION": CHAMP_VIDE,
            "REFEI": CHAMP_VIDE,
            "CATEGORIE": CHAMP_VIDE,
        }, warns

    nom_out, trunc_nom = tronquer(nom, DBF_WIDTHS["NOM"])
    if trunc_nom:
        warns.append(
            ControleItem(
                code="W01",
                niveau="avertissement",
                message=f"Le nom sera tronqué à 50 caractères : « {nom_out} ». Vous pouvez saisir une version courte.",
                champ="geomce_nom",
            )
        )

    desc_out, trunc_desc = tronquer(description, DBF_WIDTHS["DESCRIPTIO"])
    if trunc_desc:
        warns.append(
            ControleItem(
                code="W02",
                niveau="avertissement",
                message="La description sera tronquée. Vous pourrez la compléter dans GéoMCE après l'import.",
                champ="geomce_description",
            )
        )

    decision_out, _ = tronquer(decision, DBF_WIDTHS["DECISION"])
    refei_out, _ = tronquer(refei, DBF_WIDTHS["REFEI"])
    if decision_out == CHAMP_VIDE or refei_out == CHAMP_VIDE:
        warns.append(
            ControleItem(
                code="W03",
                niveau="avertissement",
                message="Champ rempli par un tiret faute d'information. Renseignez la référence de l'arrêté pour un dossier complet.",
                champ="reference_decision" if decision_out == CHAMP_VIDE else "reference_ei",
            )
        )

    cat_out, _ = tronquer(categorie, DBF_WIDTHS["CATEGORIE"])
    if cat_out != CHAMP_VIDE and len(cat_out) < 6:
        warns.append(
            ControleItem(
                code="W06",
                niveau="avertissement",
                message="Classification incomplète : GéoMCE vous demandera de la compléter à la première modification.",
                champ="geomce_categorie",
            )
        )

    cible_out = joindre_cibles(cibles)

    return {
        "ID": "1",
        "NOM": nom_out,
        "CIBLE": cible_out,
        "DESCRIPTIO": desc_out,
        "DECISION": decision_out,
        "REFEI": refei_out,
        "CATEGORIE": cat_out,
    }, warns


def controler(
    *,
    projet: dict[str, Any],
    geoms: list[dict[str, Any]],
    categories_ok: set[str],
    last_verse: dict[str, Any] | None = None,
    current_geom_hash: str | None = None,
    mode: str = "complet",
    strategie_geom: str = "eclate",
    nb_parties: int = 0,
) -> RapportControle:
    """Produit le rapport de pré-vol."""
    rapport = RapportControle()
    ok = 0

    # Géométries
    poly = [g for g in geoms if (g.get("type_geom") or "").lower() in ("polygon", "multipolygon")]
    non_poly = [g for g in geoms if (g.get("type_geom") or "").lower() not in ("polygon", "multipolygon", "")]

    if not poly and not geoms:
        rapport.bloquants.append(
            ControleItem(
                code="E01",
                niveau="bloquant",
                message="Cette mesure n'a aucune unité de gestion avec une géométrie.",
                action="Ouvrir l'onglet Carto pour importer des polygones.",
            )
        )
    elif not poly:
        rapport.bloquants.append(
            ControleItem(
                code="E01",
                niveau="bloquant",
                message="Aucune géométrie polygonale. GéoMCE n'accepte que des polygones.",
                action="Ouvrir l'onglet Carto.",
            )
        )
    else:
        ok += 1

    for g in non_poly:
        lib = g.get("libelle") or g.get("ug_id") or "UG"
        rapport.bloquants.append(
            ControleItem(
                code="E02",
                niveau="bloquant",
                message=f"L'unité de gestion « {lib} » est un point ou une ligne. GéoMCE n'accepte que des polygones.",
                action="Corriger ou exclure cette UG.",
            )
        )

    for g in geoms:
        if g.get("invalide"):
            lib = g.get("libelle") or g.get("ug_id") or "UG"
            rapport.bloquants.append(
                ControleItem(
                    code="E03",
                    niveau="bloquant",
                    message=f"La géométrie de « {lib} » est invalide et n'a pas pu être corrigée automatiquement.",
                    action="Éditer l'UG dans Carto.",
                )
            )
        if g.get("reparee"):
            rapport.avertissements.append(
                ControleItem(
                    code="W04",
                    niveau="avertissement",
                    message="La géométrie a été corrigée automatiquement (auto-intersection). Vérifiez l'aperçu.",
                )
            )

    srid, srid_label = resolve_srid(projet.get("departement"))
    if srid is None:
        rapport.bloquants.append(
            ControleItem(
                code="E04",
                niveau="bloquant",
                message="Le territoire du projet n'est pas déterminé, impossible de choisir le système de coordonnées.",
                action="Renseigner le département sur la fiche projet.",
                champ="departement",
            )
        )
    else:
        ok += 1
        rapport.srid = srid
        rapport.srid_label = srid_label

    nom = projet.get("geomce_nom") or ""
    if mode != "geometrie_seule" and not str(nom).strip():
        rapport.bloquants.append(
            ControleItem(
                code="E05",
                niveau="bloquant",
                message="Le nom GéoMCE est obligatoire.",
                action="Saisir le nom tel qu'il apparaîtra dans GéoMCE.",
                champ="geomce_nom",
            )
        )
    else:
        ok += 1

    cat = (projet.get("geomce_categorie") or "").strip()
    if mode != "geometrie_seule":
        if not cat:
            rapport.bloquants.append(
                ControleItem(
                    code="E06",
                    niveau="bloquant",
                    message="Sélectionnez la catégorie ERC de la mesure.",
                    champ="geomce_categorie",
                )
            )
        elif cat not in categories_ok:
            rapport.bloquants.append(
                ControleItem(
                    code="E07",
                    niveau="bloquant",
                    message="Ce code n'existe pas dans le référentiel GéoMCE.",
                    champ="geomce_categorie",
                )
            )
        else:
            ok += 1

    cibles = projet.get("geomce_cible") or []
    if isinstance(cibles, str):
        cibles = [cibles]
    if mode != "geometrie_seule":
        if not cibles:
            rapport.bloquants.append(
                ControleItem(
                    code="E08",
                    niveau="bloquant",
                    message="Indiquez au moins une cible.",
                    champ="geomce_cible",
                )
            )
        else:
            bad = [c for c in cibles if c not in CIBLES_FERMEES]
            if bad:
                rapport.bloquants.append(
                    ControleItem(
                        code="E09",
                        niveau="bloquant",
                        message=f"Valeur de cible non reconnue par GéoMCE : {', '.join(bad)}.",
                        champ="geomce_cible",
                    )
                )
            else:
                ok += 1

    attrs, trunc_warns = build_attributs(
        nom=projet.get("geomce_nom"),
        cibles=list(cibles) if cibles else None,
        description=projet.get("geomce_description") or projet.get("description"),
        decision=projet.get("reference_decision"),
        refei=projet.get("reference_ei"),
        categorie=projet.get("geomce_categorie"),
        mode=mode,
    )
    rapport.avertissements.extend(trunc_warns)
    rapport.attributs_apercu = attrs

    # Hash / historique
    if last_verse and current_geom_hash:
        if last_verse.get("geom_hash") == current_geom_hash:
            rapport.avertissements.append(
                ControleItem(
                    code="W07",
                    niveau="avertissement",
                    message=(
                        f"Cette géométrie a déjà été versée le "
                        f"{last_verse.get('verse_le') or last_verse.get('cree_le')}. "
                        "Un nouvel envoi créera une emprise en double dans GéoMCE."
                    ),
                )
            )
        else:
            rapport.avertissements.append(
                ControleItem(
                    code="W08",
                    niveau="avertissement",
                    message=(
                        f"La géométrie a changé depuis le versement du "
                        f"{last_verse.get('verse_le') or last_verse.get('cree_le')}. "
                        "L'ancienne emprise devra être supprimée manuellement dans GéoMCE."
                    ),
                )
            )

    if strategie_geom == "eclate" and nb_parties > 1:
        rapport.avertissements.append(
            ControleItem(
                code="W09",
                niveau="avertissement",
                message=(
                    f"GéoMCE affichera un message d'erreur lors de l'import. "
                    f"C'est normal : la mesure et ses {nb_parties} emprises seront bien créées."
                ),
            )
        )

    rapport.nb_polygones = nb_parties or len(poly)
    rapport.ok_count = ok
    rapport.peut_exporter = len(rapport.bloquants) == 0
    return rapport
