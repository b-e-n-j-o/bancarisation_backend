"""
sig_ug.py — Couche SIG → claims `unite_gestion`.

LLM (passe courte) : rôle de la couche + colonne d'identifiant UG.
CODE : appliquer le mappage, une claim par ENTITÉ (pas de unary_union).

Aucune écriture en base ici — la persistance reste dans projets/geometries
après validation utilisateur.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, Field

from ..utils.claims import Claim, Kind, enregistrer_payload
from ..utils.familles import RoleDoc
from ..utils.registre import extracteur

if TYPE_CHECKING:
    from ..utils.plan import Contexte, Job

VERSION = "0.1.0"


class MappageCouche(BaseModel):
    role_couche: Literal[
        "unites_de_gestion", "emprise_projet", "parcellaire",
        "habitats", "stations_especes", "autre",
    ]
    colonne_ug_id: Optional[str] = Field(
        None, description="Colonne portant l'identifiant d'UG. Null si aucune ne convient."
    )
    colonne_libelle: Optional[str] = None
    colonne_surface: Optional[str] = None
    colonne_habitat: Optional[str] = None
    identifiants_reconnus: list[str] = Field(default_factory=list)
    confiance: float = Field(ge=0, le=1)
    justification: str
    avertissements: list[str] = Field(default_factory=list)


class UniteGestionGeo(BaseModel):
    ug_id: str
    ug_id_source: str = Field(description="Valeur brute avant normalisation")
    libelle: Optional[str] = None
    couche: str
    type_geometrie: str
    epsg_source: Optional[int] = None
    surface_ha: Optional[float] = None
    surface_declaree: Optional[float] = None
    attributs: dict = Field(default_factory=dict)
    index_feature: int


enregistrer_payload(Kind.unite_gestion, UniteGestionGeo)


SYSTEM_PROMPT = """Tu analyses la FICHE D'UNE COUCHE SIG issue d'un dossier de compensation \
écologique français. Tu vois le nom de la couche, son CRS, son type de géométrie, et le profil \
de sa table attributaire (colonnes, nombre de valeurs distinctes, échantillon).

Tu ne vois AUCUNE géométrie et tu n'en as pas besoin. Ta seule tâche : dire comment lire cette \
couche.

1. RÔLE. Une couche d'unités de gestion a typiquement autant d'entités que d'UG et une colonne \
d'identifiants courts et distincts. Une emprise de projet a une ou très peu d'entités et pas \
d'identifiant d'UG. Un parcellaire cadastral se reconnaît à ses colonnes (section, numéro, \
commune, insee). Ne force pas : "autre" est une réponse acceptable.

2. COLONNE D'IDENTIFIANT. Choisis la colonne dont les valeurs ressemblent à des identifiants d'UG \
(courts, quasi tous distincts, souvent de la forme UG1, UG2a, A1, Z3…). Si un référentiel d'UG \
issu des autres documents t'est fourni, la bonne colonne est celle dont les valeurs le recoupent : \
c'est le critère le plus fiable, utilise-le en priorité et reporte les correspondances dans \
identifiants_reconnus.
3. Une colonne quasi entièrement distincte mais de type identifiant technique (FID, OBJECTID, id \
auto-incrémenté, UUID) n'est PAS un identifiant d'UG.
4. Si aucune colonne ne convient, colonne_ug_id=null et explique pourquoi.
5. La confiance est une vraie estimation. Sous 0.6, un humain confirmera avant écriture en base."""


USER_PROMPT = """{contexte}

{fiche}"""


@extracteur(
    nom="ug_couche_sig",
    version=VERSION,
    roles=(RoleDoc.carto_ug,),
    formats=("sig",),
    produit=(Kind.unite_gestion,),
    description="Identifie le rôle d'une couche SIG et la colonne portant les "
                "identifiants d'UG, puis émet une claim par entité.",
    cout="leger",
    fan_out=True,
)
def ug_couche_sig(job: "Job", ctx: "Contexte") -> list[Claim]:
    from ...domain.ug_ids import normalize_ug_id
    from ...mistral_client import DEFAULT_MODEL, extraire_structure

    claims: list[Claim] = []

    for locator in job.locators:
        bloc = ctx.document.bloc(locator)
        if bloc is None:
            continue

        mappage: MappageCouche = extraire_structure(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT.format(
                contexte=ctx.entete_contexte(), fiche=bloc.texte
            ),
            result_type=MappageCouche,
            etiquettes=f"SIG/{locator}",
            debug_dir=ctx.debug_dir,
            debug_prefixe="sig",
            model=DEFAULT_MODEL,
            effort="low",
            max_tokens=4000,
            utiliser_schema=False,
            compteur=ctx.compteur,
            schema_name="mappage_couche_sig",
        )

        if mappage.role_couche != "unites_de_gestion" or not mappage.colonne_ug_id:
            continue

        claims.extend(_claims_entites(bloc, mappage, ctx, normalize_ug_id, locator))

    return claims


def _claims_entites(bloc, mappage: MappageCouche, ctx, normalize_ug_id, locator) -> list[Claim]:
    from ..utils.sig import lire_couche

    gdf = lire_couche(bloc)
    epsg = bloc.meta.get("epsg")
    claims: list[Claim] = []

    try:
        surfaces = gdf.to_crs(epsg=2154).geometry.area / 10_000
    except Exception:  # noqa: BLE001
        surfaces = None

    for i, (_, entite) in enumerate(gdf.iterrows()):
        brut = entite.get(mappage.colonne_ug_id)
        if brut is None or str(brut).strip() == "":
            continue
        normalise = normalize_ug_id(str(brut))
        a_confirmer: list[str] = [] if normalise else ["ug_id"]

        attributs = {
            str(k): (v.item() if hasattr(v, "item") else v)
            for k, v in entite.drop(labels="geometry", errors="ignore").items()
            if v is not None and v == v
        }

        payload = UniteGestionGeo(
            ug_id=normalise or str(brut).strip(),
            ug_id_source=str(brut),
            libelle=_valeur(entite, mappage.colonne_libelle),
            couche=bloc.meta.get("nom_source") or bloc.meta["couche"],
            type_geometrie=str(entite.geometry.geom_type) if entite.geometry is not None else "vide",
            epsg_source=epsg,
            surface_ha=float(surfaces.iloc[i]) if surfaces is not None else None,
            surface_declaree=_nombre(entite, mappage.colonne_surface),
            attributs=attributs,
            index_feature=i,
        )

        avertissements = list(mappage.avertissements)
        if epsg is None:
            avertissements.append(
                "CRS sans code EPSG : reprojection impossible, .prj à demander au BE"
            )

        claims.append(
            Claim.depuis(
                payload,
                kind=Kind.unite_gestion,
                doc_id=ctx.document.doc_id,
                extracteur="ug_couche_sig",
                version=VERSION,
                ancres=[f"{ctx.document.ancre(locator)}:f{i}"],
                confiance=mappage.confiance,
                champs_a_confirmer=a_confirmer,
                avertissements=avertissements,
                cle_locale=payload.ug_id,
            )
        )

    return claims


def _valeur(entite, colonne: str | None):
    if not colonne or colonne not in entite:
        return None
    v = entite[colonne]
    return None if v is None or v != v else str(v)


def _nombre(entite, colonne: str | None):
    if not colonne or colonne not in entite:
        return None
    try:
        return float(entite[colonne])
    except (TypeError, ValueError):
        return None
