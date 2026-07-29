"""
extract_arrete.py — Extraction structurée d'un arrêté préfectoral.

Entrée  : le markdown OCR de l'arrêté (produit par l'étape OCR Mistral,
          comme pour les plans de gestion).
Sortie  : un objet ArreteExtraction (metadonnees + prescriptions) qui mappe
          directement les tables bancarisation.arrete et bancarisation.arrete_prescription.

Un seul appel LLM (doc entier), mistral-medium-3-5 + reasoning_effort.
Aligné sur le style de extract_echeances.py : pydantic, JSON strict, log des tokens.

Usage :
    python3 -m api.arrete.extract_arrete chemin/vers/arrete_ocr.md
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError
from mistralai import Mistral

logger = logging.getLogger("extract_arrete")

MODEL = os.getenv("MISTRAL_MODEL_ARRETE", "mistral-medium-3-5")
REASONING_EFFORT = os.getenv("MISTRAL_REASONING_EFFORT", "high")

# Tarifs indicatifs par million de tokens (à ajuster au tarif réel du contrat).
PRIX_INPUT_PAR_M = float(os.getenv("MISTRAL_PRIX_INPUT_M", "0.40"))
PRIX_OUTPUT_PAR_M = float(os.getenv("MISTRAL_PRIX_OUTPUT_M", "2.00"))

TypeArrete = Literal[
    "declaration_loi_eau", "autorisation_env", "derogation_ep",
    "arrete_modificatif", "autre",
]
Categorie = Literal[
    "compensation", "evitement", "reduction", "accompagnement",
    "suivi", "chantier", "administratif",
]
Nature = Literal["calendaire", "recurrente", "permanente", "ponctuelle", "seuil"]


# ---------------------------------------------------------------------------
# Schéma de sortie
# ---------------------------------------------------------------------------

class Metadonnees(BaseModel):
    type: TypeArrete
    reference: Optional[str] = None          # ex. "2023/01/05-004"
    autorite: Optional[str] = None           # ex. "DDTM 33 - service eau et nature"
    beneficiaire: Optional[str] = None       # ex. "SAS BEOLETTO"
    numero_dossier: Optional[str] = None     # ex. "010007707"
    date_signature: Optional[str] = None     # ISO "YYYY-MM-DD" ou null
    date_notification: Optional[str] = None  # null si absente du texte (résolue à l'ingestion)
    rubriques: list[str] = Field(default_factory=list)  # nomenclature R.214-1, ex. ["3.3.1.0"]
    confiance: float = 0.0


class Prescription(BaseModel):
    article: Optional[str] = None            # ex. "Article 4"
    intitule: str                            # court, ex. "Surface de compensation à atteindre"
    categorie: Optional[Categorie] = None
    nature: Optional[Nature] = None
    cible_valeur: Optional[float] = None      # ex. 4718
    cible_unite: Optional[str] = None         # ex. "m2", "%", "ha"
    echeance: Optional[str] = None            # ISO SEULEMENT si datable en absolu, sinon null
    recurrence: Optional[dict] = None         # règle jsonb (périodique / paliers / relatif)
    page_source: Optional[int] = None
    texte_source: Optional[str] = None        # extrait littéral court (audit)
    confiance: float = 0.0


class ArreteExtraction(BaseModel):
    metadonnees: Metadonnees
    prescriptions: list[Prescription] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Tu es un assistant spécialisé dans l'analyse d'arrêtés préfectoraux français \
encadrant des mesures compensatoires (séquence Éviter-Réduire-Compenser). \
Tu extrais des DONNÉES STRUCTURÉES d'un arrêté fourni en markdown (issu d'un OCR).

Tu renvoies UNIQUEMENT un objet JSON valide, sans texte autour, sans balises \
markdown, conforme au schéma décrit ci-dessous. Tu n'inventes jamais : si une \
information est absente, tu mets null (ou une liste vide). Tu ne traduis pas, \
tu ne reformules pas les valeurs verbatim (références, dates, chiffres).

## Bloc 1 — metadonnees (fiable, extraction verbatim)
- type : un parmi declaration_loi_eau | autorisation_env | derogation_ep | \
arrete_modificatif | autre. Un arrêté « prescriptions à déclaration » au titre \
de L.214-3 (loi sur l'eau) = declaration_loi_eau.
- reference : le numéro de l'arrêté tel qu'écrit.
- autorite : le service instructeur / signataire (ex. « DDTM 33 - service eau \
et nature », « DREAL ... »). NE PAS présumer que c'est la DREAL.
- beneficiaire : le pétitionnaire / maître d'ouvrage.
- numero_dossier : n° de dossier / d'enregistrement s'il existe.
- date_signature : date de signature au format ISO YYYY-MM-DD, sinon null.
- date_notification : SEULEMENT si le texte la donne explicitement ; sinon null \
(elle sera renseignée à l'ingestion). Ne la déduis pas de la signature.
- rubriques : liste des rubriques de la nomenclature R.214-1 citées (ex. "3.3.1.0").
- confiance : 0..1 sur l'ensemble du bloc.

## Bloc 2 — prescriptions (obligations opposables, à valider ensuite par l'instructeur)
Décompose l'arrêté en obligations matérielles, une par ligne. Pour chacune :
- article : l'article source (ex. "Article 4").
- intitule : formulation courte et claire de l'obligation.
- categorie : compensation | evitement | reduction | accompagnement | suivi | \
chantier | administratif.
- nature :
  * seuil       → objectif de résultat chiffré (surface à atteindre, %).
  * recurrente  → action répétée (fauche annuelle, suivi périodique).
  * permanente  → état à maintenir en continu (préservation, mise en défens).
  * ponctuelle  → acte unique (versement GéoMCE, transmission d'un plan, info préalable).
  * calendaire  → jalon daté unique (rare) ; préfère les autres natures.
- cible_valeur + cible_unite : si l'obligation porte un chiffre (ex. 4718 / "m2").
- echeance : date ISO UNIQUEMENT si l'obligation est datable en ABSOLU dans le \
texte. Si l'échéance est RELATIVE à un événement du dossier (notification de \
l'arrêté, achèvement des travaux, démarrage du chantier), NE METS PAS de date : \
mets echeance=null et encode la règle dans recurrence.
- recurrence : objet jsonb selon le cas, sinon null :
  * périodique : {"type":"periodique","cadence_annees":1,"fenetre_mois":10}  (ex. fauche en octobre)
  * paliers    : {"type":"paliers","point_depart":"achevement_travaux",\
"duree_totale_annees":30,"paliers":[{"cadence_annees":1,"duree_annees":5},\
{"cadence_annees":5,"duree_annees":25}]}  (suivi à cadence dégressive)
  * relatif    : {"type":"relatif","base":"date_notification","offset_mois":3}  (ex. versement GéoMCE)
- page_source : numéro de page du markdown si un marqueur de page est présent, sinon null.
- texte_source : un court extrait littéral justifiant la ligne (pour l'audit).
- confiance : 0..1 sur cette prescription.

Inclus les obligations administratives qui portent une échéance ou une \
transmission (versement GéoMCE, transmission des bilans/notes annuelles, \
information préalable au démarrage des travaux). EXCLUS le boilerplate sans \
obligation matérielle (droits des tiers, voies et délais de recours, \
publication/affichage) SAUF s'il porte une échéance opposable.

Réponds avec un unique objet JSON : {"metadonnees": {...}, "prescriptions": [ {...} ]}.
"""


def build_user_prompt(markdown: str) -> str:
    return (
        "Voici le texte OCR de l'arrêté à analyser. Extrais les métadonnées et "
        "les prescriptions selon le schéma.\n\n"
        "----- DÉBUT ARRÊTÉ -----\n"
        f"{markdown}\n"
        "----- FIN ARRÊTÉ -----"
    )


# ---------------------------------------------------------------------------
# Appel LLM
# ---------------------------------------------------------------------------

def _parse_json(raw: str) -> dict:
    """Parse tolérant : retire un éventuel fencing ```json et coupe au 1er objet."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1]
        if txt.lstrip().startswith("json"):
            txt = txt.lstrip()[4:]
    start, end = txt.find("{"), txt.rfind("}")
    if start != -1 and end != -1:
        txt = txt[start : end + 1]
    return json.loads(txt)


def extract_arrete(
    markdown: str,
    client: Optional[Mistral] = None,
    model: str = MODEL,
    max_retries: int = 2,
) -> tuple[ArreteExtraction, dict]:
    """Extrait un ArreteExtraction depuis le markdown OCR. Renvoie (extraction, usage)."""
    client = client or Mistral(api_key=os.environ["MISTRAL_API_KEY"])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(markdown)},
    ]

    last_err: Optional[Exception] = None
    for tentative in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            resp = client.chat.complete(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
                reasoning_effort=REASONING_EFFORT,  # requiert un SDK mistralai 1.x récent
            )
        except TypeError:
            # SDK trop ancien pour reasoning_effort : on retente sans le paramètre.
            resp = client.chat.complete(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )
        dt = time.perf_counter() - t0

        raw = resp.choices[0].message.content
        u = resp.usage
        usage = {
            "modele": model,
            "tentative": tentative,
            "input_tokens": getattr(u, "prompt_tokens", None),
            "output_tokens": getattr(u, "completion_tokens", None),
            "total_tokens": getattr(u, "total_tokens", None),
            # Mistral n'expose pas toujours les tokens de réflexion séparément :
            "reasoning_tokens": _reasoning_tokens(u),
            "duree_s": round(dt, 2),
        }
        usage["cout_estime_usd"] = _cout(usage)
        logger.info("extraction arrêté | %s", usage)

        try:
            data = _parse_json(raw)
            extraction = ArreteExtraction.model_validate(data)
            return extraction, usage
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            logger.warning("parse/validation KO (tentative %d) : %s", tentative, e)
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    "Ta réponse n'était pas un JSON valide conforme au schéma "
                    f"({e}). Renvoie UNIQUEMENT l'objet JSON corrigé."
                ),
            })

    raise RuntimeError(f"Extraction impossible après {max_retries} tentatives : {last_err}")


def _reasoning_tokens(usage) -> Optional[int]:
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        return getattr(details, "reasoning_tokens", None)
    return None


def _cout(usage: dict) -> Optional[float]:
    it, ot = usage.get("input_tokens"), usage.get("output_tokens")
    if it is None or ot is None:
        return None
    c = it / 1_000_000 * PRIX_INPUT_PAR_M + ot / 1_000_000 * PRIX_OUTPUT_PAR_M
    return round(c, 4)


# ---------------------------------------------------------------------------
# Mapping vers les tables (à passer à repository.py)
# ---------------------------------------------------------------------------

def to_db_rows(extraction: ArreteExtraction, projet_id: str, document_id: Optional[str],
               usage: dict) -> tuple[dict, list[dict]]:
    """Transforme l'extraction en (row `arrete`, rows `arrete_prescription`)."""
    m = extraction.metadonnees
    arrete_row = {
        "projet_id": projet_id,
        "type": m.type,
        "reference": m.reference,
        "autorite": m.autorite,
        "beneficiaire": m.beneficiaire,
        "numero_dossier": m.numero_dossier,
        "date_signature": m.date_signature,
        "date_notification": m.date_notification,
        "document_id": document_id,
        "extraction": extraction.model_dump(),   # trace complète (rubriques incluses) en jsonb
        "extraction_modele": usage.get("modele"),
        "confiance": m.confiance,
        "origine": "ia",
    }
    presc_rows = [
        {
            "article": p.article,
            "intitule": p.intitule,
            "categorie": p.categorie,
            "nature": p.nature,
            "cible_valeur": p.cible_valeur,
            "cible_unite": p.cible_unite,
            "echeance": p.echeance,
            "recurrence": p.recurrence,
            "page_source": p.page_source,
            "texte_source": p.texte_source,
            "confiance": p.confiance,
            "origine": "ia",
        }
        for p in extraction.prescriptions
    ]
    return arrete_row, presc_rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(argv) < 2:
        print("usage: python3 -m api.arrete.extract_arrete <arrete_ocr.md>", file=sys.stderr)
        return 2
    markdown = Path(argv[1]).read_text(encoding="utf-8")
    extraction, usage = extract_arrete(markdown)
    print(json.dumps(extraction.model_dump(), ensure_ascii=False, indent=2))
    print(f"\n# usage: {usage}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))