r"""  # noqa
extract_budget.py — Extraction LLM des lignes budgétaires depuis un classeur
d'estimation sérialisé en markdown (sortie de xlsx_to_markdown.py).

Architecture deux passes, pensée pour généraliser à des Excel d'autres BE
dont la structure diffère, tout en sortant TOUJOURS le même contrat JSON :

  Passe 1 — CARTOGRAPHIE : le modèle identifie les tables logiques du
  classeur (feuille, plage de lignes, rôle sémantique, granularité
  temporelle, présence de sous-totaux). Sortie JSON légère, validée.
  → C'est elle qui absorbe la variabilité entre BE.

  Passe 2 — EXTRACTION : guidée par la cartographie, le modèle extrait
  chaque ligne budgétaire selon un schéma Pydantic strict (contrat
  ligne_budget), avec association optionnelle à un référentiel d'actions
  déjà extraites du plan de gestion (codes mesure), pattern
  champs_a_confirmer, et traçabilité feuille/ligne.

  Post-traitement déterministe : validation Pydantic (1 retry avec
  feedback des erreurs), puis contrôle croisé somme des lignes ≈ totaux
  déclarés par le BE.

Usage :
    export MISTRAL_API_KEY=...
    python3 extract_budget.py estimation.md \
        --actions actions_plan_gestion.json \   # optionnel : référentiel pour le matching
        --out lignes_budget.json
"""  # noqa

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Literal, Optional

try:  # SDK mistralai >= 2.x
    from mistralai.client import Mistral
except ImportError:  # SDK mistralai 1.x
    from mistralai import Mistral
from pydantic import BaseModel, Field, ValidationError

MODEL = os.environ.get("MISTRAL_MODEL", "mistral-medium-latest")
TOLERANCE_TOTAUX = 0.01  # 1 % d'écart toléré sur le contrôle croisé


# ---------------------------------------------------------------------------
# Contrats Pydantic
# ---------------------------------------------------------------------------

class TableCartographiee(BaseModel):
    table_id: str = Field(description="Identifiant court et unique, ex. 'T1', 'T2'")
    feuille: str
    lignes: str = Field(description="Plage de lignes Excel, ex. '3-11'")
    role: Literal[
        "detail_couts",        # coûts unitaires par prestation
        "planning_occurrences",  # matrice actions × périodes (X, quantités)
        "planning_montants",   # matrice actions × périodes en €
        "recapitulatif",       # totaux, ventilation prestataires, acomptes
        "facturation_reelle",  # décomptes, acomptes facturés, avoirs
        "statut_realisation",  # réalisé / projeté / supprimé
        "hypotheses",          # paramètres, notes
        "autre",
    ]
    description: str
    granularite_temporelle: Literal["aucune", "annee", "periode", "campagne", "mixte"]
    contient_sous_totaux: bool
    colonnes_montants: list[str] = Field(
        default_factory=list, description="Lettres des colonnes portant des montants"
    )


class Cartographie(BaseModel):
    tables: list[TableCartographiee]
    devise: str = "EUR"
    commentaires: Optional[str] = None


class SourceLigne(BaseModel):
    feuille: str
    lignes: list[int] = Field(description="Numéros de ligne Excel d'origine")


class LigneBudget(BaseModel):
    table_id: str = Field(description="table_id de la cartographie dont provient cette ligne")
    libelle_prestation: str = Field(description="Libellé de la prestation, tel quel")
    libelle_action: Optional[str] = Field(
        None, description="Famille/action parente si le tableau en a une"
    )
    code_mesure: Optional[str] = Field(
        None, description="Code mesure si présent (ex. 'SE 1', 'TU 3'). Null sinon, ne jamais inventer."
    )
    prestataire: Optional[str] = None
    montant_ht: Optional[float] = None
    montant_ttc: Optional[float] = None
    taux_tva: Optional[float] = Field(None, description="Fraction, ex. 0.2 pour 20 %")
    unite: Optional[Literal[
        "forfait", "campagne", "annee", "jour", "demi_journee",
        "ha", "ml", "unite", "passage", "releve", "autre"
    ]] = None
    quantite: Optional[float] = None
    nb_campagnes: Optional[int] = Field(
        None, description="Nombre de campagnes/récurrences si le document multiplie un coût unitaire"
    )
    montant_partage_avec: list[int] = Field(
        default_factory=list,
        description=(
            "Si le montant provient d'une cellule fusionnée couvrant plusieurs lignes "
            "(annotation ⟨montants communs aux lignes X-Y⟩), lister ici les autres numéros "
            "de ligne Excel du groupe. Le montant vaut pour le groupe entier, pas par ligne."
        ),
    )
    annees: dict[str, float] = Field(
        default_factory=dict,
        description="Déroulé année → montant HT si le document le fournit (ex. {'2023': 2425})",
    )
    ug_mentionnees: list[str] = Field(default_factory=list)
    est_total: bool = Field(
        False, description="True pour toute ligne d'agrégation (sous-total, total, récap)"
    )
    statut_reel: Optional[Literal["realise", "projete", "non_realise", "supprime"]] = None
    action_associee: Optional[str] = Field(
        None,
        description="Code du référentiel d'actions du plan de gestion si association fiable, sinon null",
    )
    confiance: float = Field(ge=0, le=1)
    champs_a_confirmer: list[str] = Field(
        default_factory=list,
        description="Noms des champs incertains à faire valider par l'utilisateur",
    )
    source: SourceLigne


class TotalDeclare(BaseModel):
    libelle: str
    montant_ht: float
    perimetre: str = Field(description="Ce que ce total est censé couvrir")
    source: SourceLigne


class ExtractionBudget(BaseModel):
    lignes: list[LigneBudget]
    totaux_declares: list[TotalDeclare]
    avertissements: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PROMPT_CARTOGRAPHIE = """Tu analyses un classeur Excel d'estimation budgétaire fourni par un bureau \
d'études pour le suivi de mesures compensatoires environnementales. Le classeur est sérialisé \
en markdown ci-dessous : une section par feuille, chaque table conserve les numéros de ligne \
Excel (colonne "ligne") et les lettres de colonnes. Les lignes marquées [TOTAL?] contiennent \
une somme verticale (indice de ligne d'agrégation, à confirmer par le contexte).

Ta tâche : CARTOGRAPHIER le classeur. Une même feuille peut contenir PLUSIEURS tables logiques \
empilées (blocs d'en-têtes répétés) : découpe-les. Pour chaque table, donne la feuille, la plage \
de lignes, son rôle sémantique, sa granularité temporelle, si elle contient des sous-totaux, et \
les colonnes portant des montants.

Réponds UNIQUEMENT avec un objet JSON valide conforme à ce schéma, sans texte autour :
{schema}

CLASSEUR :
{document}"""  # noqa

PROMPT_EXTRACTION = """Tu extrais les lignes budgétaires d'un classeur Excel d'estimation fourni par un \
bureau d'études pour des mesures compensatoires environnementales. Le classeur (markdown ci-dessous) \
a déjà été cartographié — utilise cette cartographie pour localiser les tables :

CARTOGRAPHIE :
{cartographie}

RÈGLES D'EXTRACTION :
1. Traite les tables UNE PAR UNE, dans l'ordre de la cartographie. Extrais une ligne par prestation \
budgétée, avec son table_id. Reprends les libellés tels quels, sans reformuler.
2. NE RÉCONCILIE JAMAIS LES TABLES ENTRE ELLES. Si la même prestation apparaît dans deux tables \
(coût unitaire ici, déroulé annuel là), extrais-la DEUX FOIS, avec deux table_id différents. Ne \
cherche pas à savoir s'il s'agit de la même prestation, ne fusionne pas, ne déduplique pas : le \
rapprochement est fait en aval par un autre traitement. Ce n'est pas ton travail.
3. Si deux tables se contredisent (libellés, quantités, montants), n'arbitre pas : extrais les \
deux versions telles quelles et décris la contradiction dans avertissements.
4. CELLULES FUSIONNÉES. Une annotation ⟨montants communs aux lignes X-Y⟩ en tête de ligne signifie \
que les montants de cette ligne proviennent de cellules fusionnées couvrant les lignes X à Y : le \
montant vaut pour le GROUPE de lignes, pas pour chacune. Extrais-le sur la ligne d'ancrage, \
renseigne montant_partage_avec avec les autres lignes du groupe, et laisse montant_ht à null sur \
les lignes annotées ⟨montants portés par la ligne X⟩. Une annotation ⟨col. A-B⟩ signifie que le \
montant couvre la plage de colonnes indiquée, une seule fois.
5. Toute ligne d'agrégation (sous-total, total, récap par prestataire, total annuel) doit être \
extraite avec est_total=true — jamais mélangée aux lignes de détail. Les marqueurs [TOTAL?] sont \
des indices ; certaines lignes d'agrégation n'en ont pas (valeurs saisies en dur) : identifie-les \
au libellé et au contexte.
6. Ne calcule JAMAIS toi-même un montant : ne reporte que des valeurs présentes dans le document. \
Si une valeur est ambiguë ou absente, mets null et ajoute le nom du champ dans champs_a_confirmer.
7. Si une table déroule les montants par année (colonnes d'années), remplis annees pour la ligne \
correspondante.
8. code_mesure : uniquement s'il figure dans le document. Ne jamais l'inventer.
9. Montants négatifs (avoirs, prestations non réalisées) : reporte-les tels quels et renseigne \
statut_reel si le document l'indique (réalisé / projeté / non réalisé / mesure supprimée).
10. Recopie dans totaux_declares les totaux généraux affichés par le BE (avec leur périmètre) : \
ils serviront à un contrôle croisé programmatique.
{regles_matching}
Réponds UNIQUEMENT avec un objet JSON valide conforme à ce schéma, sans texte autour :
{schema}

CLASSEUR :
{document}"""  # noqa

REGLES_MATCHING = """11. RÉFÉRENTIEL D'ACTIONS du plan de gestion déjà extrait (code → libellé) :
{referentiel}
Pour chaque ligne de détail, si elle correspond de façon fiable à une action du référentiel \
(par le code mesure, ou à défaut par similarité de libellé), renseigne action_associee avec le \
code du référentiel. En cas de doute : action_associee=null et ajoute "action_associee" dans \
champs_a_confirmer. Ne force jamais une association.
"""  # noqa


# ---------------------------------------------------------------------------
# Appels LLM avec validation + retry
# ---------------------------------------------------------------------------

def _call_json(client: Mistral, prompt: str, model_cls: type[BaseModel],
               max_retries: int = 1) -> BaseModel:
    """Appelle le modèle en JSON mode, valide avec Pydantic, retry avec feedback."""  # noqa
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(max_retries + 1):
        resp = client.chat.complete(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content
        try:
            return model_cls.model_validate_json(raw)
        except ValidationError as err:
            if attempt == max_retries:
                raise
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content":
                    "Ta réponse ne respecte pas le schéma. Erreurs Pydantic :\n"
                    f"{err}\n\nRenvoie l'objet JSON complet corrigé, rien d'autre."},
            ]
    raise RuntimeError("unreachable")


def cartographier(client: Mistral, document_md: str) -> Cartographie:
    prompt = PROMPT_CARTOGRAPHIE.format(
        schema=json.dumps(Cartographie.model_json_schema(), ensure_ascii=False),
        document=document_md,
    )
    return _call_json(client, prompt, Cartographie)


def extraire(client: Mistral, document_md: str, carto: Cartographie,
             referentiel: dict[str, str] | None) -> ExtractionBudget:
    regles = ""
    if referentiel:
        regles = REGLES_MATCHING.format(
            referentiel=json.dumps(referentiel, ensure_ascii=False, indent=2)
        )
    prompt = PROMPT_EXTRACTION.format(
        cartographie=carto.model_dump_json(indent=2),
        regles_matching=regles,
        schema=json.dumps(ExtractionBudget.model_json_schema(), ensure_ascii=False),
        document=document_md,
    )
    return _call_json(client, prompt, ExtractionBudget)


# ---------------------------------------------------------------------------
# Contrôle croisé déterministe
# ---------------------------------------------------------------------------

def controle_croise(result: ExtractionBudget) -> list[str]:
    """Compare la somme des lignes de détail aux totaux déclarés par le BE.

    Heuristique de périmètre : un total déclaré est comparé à la somme des
    lignes de détail (est_total=False, montant_ht renseigné, hors négatifs
    de facturation réelle) de la ou des feuilles de sa source.
    Tout écart > TOLERANCE_TOTAUX est remonté en avertissement — à afficher
    dans l'UI, pas à corriger silencieusement.
    """  # noqa
    alerts: list[str] = []
    for total in result.totaux_declares:
        feuille = total.source.feuille
        somme = sum(
            l.montant_ht for l in result.lignes
            if not l.est_total
            and l.montant_ht is not None
            and l.source.feuille == feuille
            and l.statut_reel not in ("non_realise", "supprime")
        )
        if total.montant_ht == 0:
            continue
        ecart = abs(somme - total.montant_ht) / abs(total.montant_ht)
        if ecart > TOLERANCE_TOTAUX:
            alerts.append(
                f"Écart {ecart:.1%} sur '{total.libelle}' ({feuille}) : "
                f"somme des lignes = {somme:.2f}, total déclaré = {total.montant_ht:.2f}"
            )
    return alerts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", help="Sortie de xlsx_to_markdown.py")
    parser.add_argument("--actions", help="JSON {code: libellé} des actions du plan de gestion")
    parser.add_argument("--out", default="lignes_budget.json")
    args = parser.parse_args()

    document_md = Path(args.markdown).read_text(encoding="utf-8")
    referentiel = None
    if args.actions:
        referentiel = json.loads(Path(args.actions).read_text(encoding="utf-8"))

    client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])

    print("Passe 1/2 — cartographie…", file=sys.stderr)
    carto = cartographier(client, document_md)
    print(f"  {len(carto.tables)} tables identifiées", file=sys.stderr)

    print("Passe 2/2 — extraction…", file=sys.stderr)
    result = extraire(client, document_md, carto, referentiel)
    print(f"  {len(result.lignes)} lignes extraites "
          f"({sum(1 for l in result.lignes if l.est_total)} agrégations)", file=sys.stderr)

    result.avertissements.extend(controle_croise(result))
    for a in result.avertissements:
        print(f"  ⚠ {a}", file=sys.stderr)

    output = {
        "cartographie": carto.model_dump(),
        "extraction": result.model_dump(),
    }
    Path(args.out).write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Écrit : {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()