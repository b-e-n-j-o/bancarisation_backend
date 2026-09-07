"""
Extraction structurée d'un arrêté préfectoral (prescriptions spécifiques loi sur l'eau,
dérogation espèces protégées, autorisation environnementale).

Sortie = la couche "obligation réglementaire" du modèle 3 couches :
    arrete (métadonnées) + arrete_prescription (obligations opposables)

Ce n'est PAS un générateur de calendrier BE. Certaines prescriptions portent
toutefois une temporalité réglementaire propre (délais de notification, fréquences
de suivi imposées) : elles sont extraites dans `temporalite` et donneront des
jalons réglementaires, distincts des occurrences du plan de gestion.

Usage :
    python3 -m api.ocr.extract_arrete arrete.pdf --out arrete.json
    python3 -m api.ocr.extract_arrete arrete.md --skip-ocr --out arrete.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal

from mistralai import Mistral
from pydantic import BaseModel, Field, ValidationError

from .prompts.prompt_anlayse_arrete import SYSTEM_PROMPT

MODEL_OCR = "mistral-ocr-latest"
MODEL_LLM = "mistral-medium-3-5"


# --------------------------------------------------------------------------
# 1. Contrats
# --------------------------------------------------------------------------

Categorie = Literal[
    "evitement",
    "reduction",
    "compensation",
    "accompagnement",
    "suivi",
    "gouvernance",       # expert écologue, coordination, formation des entreprises
    "information",       # transmission DDTM/OFB, notification, affichage
    "donnees",           # versement GéoMCE, SIG
    "administratif",     # recours, modification des prescriptions, droits des tiers
]

Phase = Literal[
    "avant_travaux",
    "chantier",
    "exploitation",
    "gestion_compensation",
    "toute_duree",
    "non_precise",
]

TypeTemporalite = Literal[
    "delai_relatif",     # "3 mois à compter de la notification", "15 jours avant"
    "recurrent",         # "tous les ans au mois d'octobre"
    "paliers",           # "tous les ans les 5 premières années puis tous les 5 ans"
    "duree",             # "pendant 30 ans à compter de l'achèvement des travaux"
    "condition",         # déclenché par un événement non daté
    "aucune",
]


class Temporalite(BaseModel):
    type: TypeTemporalite = "aucune"
    # delai_relatif
    delai_valeur: int | None = None
    delai_unite: Literal["jour", "mois", "annee"] | None = None
    sens: Literal["avant", "apres"] | None = None
    point_depart: str | None = Field(
        None,
        description="Événement de référence textuel : 'notification de l'arrêté', "
                    "'démarrage des travaux', 'achèvement des travaux'.",
    )
    # recurrent / paliers
    frequence_annees: float | None = None
    fenetre_mois: list[int] | None = Field(
        None, description="Mois autorisés (1-12), ex. [10] pour 'au mois d'octobre'."
    )
    paliers: list[str] | None = Field(
        None, description="Ex. ['annuel pendant 5 ans', 'quinquennal ensuite'].",
    )
    # duree
    duree_annees: int | None = None
    duree_ouverte: bool = Field(
        False,
        description="True si l'arrêté prolonge au-delà du terme chiffré "
                    "('même au-delà des 30 années de gestion').",
    )


class Prescription(BaseModel):
    code: str = Field(description="Identifiant court stable, ex. 'ART3-P2'.")
    article: str = Field(description="Article source, ex. 'ARTICLE 3'.")
    page: int | None = Field(None, description="Page du PDF (1-indexé).")
    intitule: str = Field(description="Formulation courte et actionnable, < 120 car.")
    texte_source: str = Field(
        description="Citation littérale et intégrale de la phrase prescriptive. "
                    "Ne jamais reformuler ici."
    )
    categorie: Categorie
    phase: Phase
    destinataire: Literal["beneficiaire", "expert_ecologue", "entreprises", "autre"] = "beneficiaire"
    autorite_destinataire: list[str] = Field(
        default_factory=list,
        description="Qui reçoit le livrable, ex. ['DDTM 33', 'OFB 33'].",
    )
    temporalite: Temporalite = Field(default_factory=Temporalite)
    livrable: str | None = Field(
        None, description="Pièce attendue : 'plan de circulation', 'rapport de synthèse'..."
    )
    indicateur: str | None = Field(
        None, description="Critère vérifiable chiffré, ex. 'surface ≥ 4718 m²'."
    )
    obligation_de_resultat: bool = False
    opposable: bool = Field(
        True,
        description="False pour les articles purement procéduraux (recours, "
                    "droits des tiers, exécution) qui ne créent pas d'obligation à suivre.",
    )
    confiance: float = Field(ge=0, le=1)
    remarque: str | None = None


class SiteCompensation(BaseModel):
    commune: str | None = None
    parcelles: list[str] = Field(default_factory=list)
    surface_a_atteindre_m2: float | None = None
    surface_totale_m2: float | None = None
    distance_au_projet: str | None = None
    milieu: str | None = None


class RubriqueNomenclature(BaseModel):
    rubrique: str
    intitule_court: str
    volume: str | None = None
    regime: Literal["Declaration", "Autorisation", "Enregistrement", "Inconnu"] = "Inconnu"


class Arrete(BaseModel):
    numero: str | None = None
    date_signature: date | None = None
    autorite: str | None = Field(None, description="Ex. 'Préfète de la Gironde'.")
    service_instructeur: str | None = Field(None, description="Ex. 'DDTM 33 - service eau et nature'.")
    fondement: list[str] = Field(default_factory=list, description="Ex. ['L.214-3', 'L.163-1'].")
    regime: Literal["Declaration", "Autorisation", "Derogation", "Inconnu"] = "Inconnu"
    numero_dossier: str | None = None
    beneficiaire: str | None = None
    siret: str | None = None
    projet_nom: str | None = None
    projet_commune: str | None = None
    projet_parcelles: list[str] = Field(default_factory=list)
    emprise_ha: float | None = None
    surface_impactee_m2: float | None = None
    rubriques: list[RubriqueNomenclature] = Field(default_factory=list)
    site_compensation: SiteCompensation | None = None
    duree_suivi_annees: int | None = None
    prescriptions: list[Prescription]
    avertissements: list[str] = Field(
        default_factory=list,
        description="Ce que le modèle n'a pas su trancher : contradictions, "
                    "renvois à des annexes absentes, chiffres illisibles.",
    )


# --------------------------------------------------------------------------
# 2. Pipeline
# --------------------------------------------------------------------------

def ocr_pdf(client: Mistral, pdf_path: Path) -> str:
    """PDF -> markdown, avec marqueurs de page pour tracer la source."""
    uploaded = client.files.upload(
        file={"file_name": pdf_path.name, "content": pdf_path.read_bytes()},
        purpose="ocr",
    )
    signed = client.files.get_signed_url(file_id=uploaded.id)
    resp = client.ocr.process(
        model=MODEL_OCR,
        document={"type": "document_url", "document_url": signed.url},
        include_image_base64=False,
    )
    return "\n\n".join(
        f"<!-- page {p.index + 1} -->\n{p.markdown}" for p in resp.pages
    )


def extraire_depuis_markdown(
    markdown: str,
    *,
    compteur=None,
    debug_dir: Path | None = None,
) -> Arrete:
    """Extraction pipeline (même contrat que plan_gestion) — 0 occurrence calendaire."""
    from ..mistral_client import DEFAULT_MODEL, extraire_structure

    schema = Arrete.model_json_schema()
    user_msg = (
        "Schéma JSON attendu :\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "--- ARRÊTÉ ---\n"
        f"{markdown}\n"
        "--- FIN ---\n\n"
        "Produis le JSON."
    )
    debug = debug_dir if isinstance(debug_dir, Path) else Path("debug") / "arrete"
    arrete: Arrete = extraire_structure(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_msg,
        result_type=Arrete,
        etiquettes="ARRETE",
        debug_dir=debug,
        debug_prefixe="arrete",
        model=DEFAULT_MODEL,
        effort="medium",
        max_tokens=16000,
        utiliser_schema=False,
        compteur=compteur,
        schema_name="arrete_prefectoral",
    )
    alertes = controler(arrete, markdown)
    if alertes:
        arrete.avertissements = list(arrete.avertissements) + alertes
        print(f"   ⚠ arrêté : {len(alertes)} contrôle(s) déterministe(s)", flush=True)
    print(
        f"   ✅ arrêté {arrete.numero or '—'} : "
        f"{len(arrete.prescriptions)} prescription(s) "
        f"({sum(1 for p in arrete.prescriptions if p.opposable)} opposables)",
        flush=True,
    )
    return arrete


def extraire(client: Mistral, markdown: str) -> tuple[Arrete, dict]:
    schema = Arrete.model_json_schema()
    user_msg = (
        "Schéma JSON attendu :\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "--- ARRÊTÉ ---\n"
        f"{markdown}\n"
        "--- FIN ---\n\n"
        "Produis le JSON."
    )

    resp = client.chat.complete(
        model=MODEL_LLM,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=16000,
        # la réflexion aide beaucoup sur le découpage article -> prescriptions
        # (retirer si le quota de sortie explose, cf. incident extraction budget)
        reasoning_effort="medium",
    )

    usage = {
        "input": resp.usage.prompt_tokens,
        "output": resp.usage.completion_tokens,
        "total": resp.usage.total_tokens,
    }

    raw = resp.choices[0].message.content
    if isinstance(raw, list):  # blocs de contenu selon version SDK
        raw = "".join(getattr(b, "text", "") or "" for b in raw)
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")

    try:
        arrete = Arrete.model_validate_json(raw)
    except ValidationError as e:
        Path("_arrete_raw_invalide.json").write_text(raw, encoding="utf-8")
        raise SystemExit(
            f"JSON invalide (dump dans _arrete_raw_invalide.json)\n{e}"
        ) from e

    return arrete, usage


# --------------------------------------------------------------------------
# 3. Contrôles déterministes post-LLM
# --------------------------------------------------------------------------

def controler(arrete: Arrete, markdown: str) -> list[str]:
    """Garde-fous : ce qui ne dépend pas du LLM ne doit pas lui être confié."""
    alertes: list[str] = []

    # a) chaque texte_source doit exister dans le markdown -> anti-hallucination
    normalise = " ".join(markdown.split()).lower()
    for p in arrete.prescriptions:
        extrait = " ".join(p.texte_source.split()).lower()[:80]
        if extrait and extrait not in normalise:
            alertes.append(
                f"{p.code}: texte_source introuvable dans l'OCR (reformulation ?)"
            )

    # b) codes uniques
    codes = [p.code for p in arrete.prescriptions]
    if len(codes) != len(set(codes)):
        alertes.append("codes de prescription dupliqués")

    # c) cohérence temporelle
    for p in arrete.prescriptions:
        t = p.temporalite
        if t.type == "delai_relatif" and not (t.delai_valeur and t.point_depart):
            alertes.append(f"{p.code}: délai relatif incomplet")
        if t.type in ("recurrent", "paliers") and not (t.frequence_annees or t.paliers):
            alertes.append(f"{p.code}: récurrence sans fréquence")
        if t.fenetre_mois and any(m < 1 or m > 12 for m in t.fenetre_mois):
            alertes.append(f"{p.code}: fenetre_mois hors bornes")

    # d) couverture des articles : un article du PDF sans aucune prescription
    #    est soit purement procédural, soit un oubli -> à vérifier à l'œil
    articles_pdf = set(re.findall(r"ARTICLE\s+\w+", markdown.upper()))
    articles_vus = {p.article.upper() for p in arrete.prescriptions}
    manquants = sorted(a for a in articles_pdf if a not in articles_vus)
    if manquants:
        alertes.append(f"articles sans prescription extraite : {', '.join(manquants)}")

    # e) faible confiance
    faibles = [p.code for p in arrete.prescriptions if p.confiance < 0.6]
    if faibles:
        alertes.append(f"confiance < 0.6 : {', '.join(faibles)}")

    return alertes


# --------------------------------------------------------------------------
# 4. Mapping tables bancarisation.arrete / arrete_prescription
# --------------------------------------------------------------------------

REGIME_VERS_TYPE = {
    "Declaration": "declaration_loi_eau",
    "Autorisation": "autorisation_env",
    "Derogation": "derogation_ep",
    "Inconnu": "autre",
}

NATURE_DE_TEMPO: dict[str, str | None] = {
    "delai_relatif": "ponctuelle",
    "recurrent": "recurrente",
    "paliers": "recurrente",
    "duree": "permanente",
    "condition": "ponctuelle",
    "aucune": None,
}

_RE_CIBLE = re.compile(
    r"(?P<val>\d+(?:[.,]\d+)?)\s*(?P<u>m²|m2|ha|%|an(?:née)?s?)?",
    re.IGNORECASE,
)


def type_arrete_depuis(arrete: Arrete) -> str:
    return REGIME_VERS_TYPE.get(arrete.regime, "autre")


def _cible_depuis(
    indicateur: str | None,
    site: SiteCompensation | None = None,
) -> tuple[float | None, str | None]:
    if indicateur:
        m = _RE_CIBLE.search(indicateur)
        if m:
            val = float(m.group("val").replace(",", "."))
            u = (m.group("u") or "").lower()
            if u in ("m²", "m2"):
                u = "m2"
            elif u.startswith("an"):
                u = "ans"
            return val, u or None
    if site and site.surface_a_atteindre_m2 is not None:
        return float(site.surface_a_atteindre_m2), "m2"
    return None, None


def _as_prescription(payload: Prescription | dict[str, Any]) -> Prescription:
    if isinstance(payload, Prescription):
        return payload
    propre = {
        k: v
        for k, v in payload.items()
        if k not in ("arrete_meta", "_meta_only")
    }
    return Prescription.model_validate(propre)


def prescription_vers_ligne(
    payload: Prescription | dict[str, Any],
    *,
    site: SiteCompensation | None = None,
) -> dict[str, Any]:
    """Claim / modèle LLM → ligne `arrete_prescription`. Jamais de date inventée."""
    p = _as_prescription(payload)
    t = p.temporalite
    cible_v, cible_u = _cible_depuis(p.indicateur, site)
    return {
        "code": p.code,
        "article": p.article,
        "intitule": p.intitule,
        "categorie": p.categorie,
        "nature": NATURE_DE_TEMPO.get(t.type),
        "cible_valeur": cible_v,
        "cible_unite": cible_u,
        "echeance": None,
        "recurrence": None if t.type == "aucune" else t.model_dump(mode="json"),
        "page_source": p.page,
        "texte_source": p.texte_source,
        "confiance": p.confiance,
        "opposable": p.opposable,
        "phase": p.phase,
        "destinataire": p.destinataire,
        "livrable": p.livrable,
        "indicateur": p.indicateur,
        "obligation_de_resultat": p.obligation_de_resultat,
        "temporalite": t.model_dump(mode="json"),
        "autorite_destinataire": p.autorite_destinataire,
        "remarque": p.remarque,
        "origine": "ia",
    }


def vers_lignes_db(
    arrete: Arrete,
    *,
    projet_id: str,
    document_id: str | None = None,
    extraction_modele: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    confiances = [p.confiance for p in arrete.prescriptions] or [0.0]
    arrete_row: dict[str, Any] = {
        "projet_id": projet_id,
        "type": type_arrete_depuis(arrete),
        "reference": arrete.numero,
        "autorite": arrete.autorite or arrete.service_instructeur,
        "beneficiaire": arrete.beneficiaire,
        "numero_dossier": arrete.numero_dossier,
        "date_signature": (
            arrete.date_signature.isoformat() if arrete.date_signature else None
        ),
        "date_notification": None,
        "document_id": document_id,
        "extraction": arrete.model_dump(mode="json"),
        "extraction_modele": extraction_modele or MODEL_LLM,
        "confiance": round(sum(confiances) / len(confiances), 3),
        "origine": "ia",
    }
    presc_rows = [
        prescription_vers_ligne(p, site=arrete.site_compensation)
        for p in arrete.prescriptions
    ]
    return arrete_row, presc_rows


def arrete_depuis_claims(
    meta: dict[str, Any],
    prescriptions: list[dict[str, Any]],
) -> Arrete:
    """Reconstruit un Arrete depuis les claims jsonl (ingestion déterministe)."""
    propre = [
        {k: v for k, v in p.items() if k not in ("arrete_meta", "_meta_only")}
        for p in prescriptions
    ]
    payload = {**meta, "prescriptions": propre}
    return Arrete.model_validate(payload)


def resume(arrete: Arrete, alertes: list[str], usage: dict) -> None:
    print(f"\nArrêté {arrete.numero} — {arrete.beneficiaire} — {arrete.projet_commune}")
    print(f"{len(arrete.prescriptions)} prescriptions "
          f"({sum(1 for p in arrete.prescriptions if p.opposable)} opposables)\n")
    for p in sorted(arrete.prescriptions, key=lambda x: x.article):
        flag = "" if p.opposable else "  [procédural]"
        tempo = p.temporalite.type
        tempo = f"  <{tempo}>" if tempo != "aucune" else ""
        print(f"  {p.code:<10} {p.categorie:<15} {p.intitule[:70]}{tempo}{flag}")
    if arrete.avertissements:
        print("\nAvertissements LLM :")
        for a in arrete.avertissements:
            print(f"  - {a}")
    if alertes:
        print("\nContrôles déterministes :")
        for a in alertes:
            print(f"  ! {a}")
    cout = usage["input"] / 1e6 * 0.4 + usage["output"] / 1e6 * 2.0
    print(f"\ntokens in={usage['input']} out={usage['output']}  (~{cout:.3f} $)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--out", type=Path, default=Path("arrete.json"))
    ap.add_argument("--skip-ocr", action="store_true",
                    help="La source est déjà un markdown OCR.")
    ap.add_argument("--dump-md", type=Path, default=None)
    args = ap.parse_args()

    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        sys.exit("MISTRAL_API_KEY manquant")
    client = Mistral(api_key=key)

    if args.skip_ocr:
        markdown = args.source.read_text(encoding="utf-8")
    else:
        print("OCR…")
        markdown = ocr_pdf(client, args.source)
        if args.dump_md:
            args.dump_md.write_text(markdown, encoding="utf-8")

    print("Extraction…")
    arrete, usage = extraire(client, markdown)
    alertes = controler(arrete, markdown)

    args.out.write_text(
        arrete.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )
    resume(arrete, alertes, usage)
    print(f"\n-> {args.out}")


if __name__ == "__main__":
    main()