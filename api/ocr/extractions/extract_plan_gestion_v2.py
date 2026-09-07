"""
extract_plan_gestion_v2.py — Distributeur (lourd) + slice Python + scribes (légers).

Flux :
  1. LLM medium/high → JSON fiches {bornes + échéances} (pas de contenu intégral)
  2. Python coupe le markdown (bornes, sinon titre au code TU2 / SE1…)
  3. LLM small/none × N → contenu_propre si un chunk OCR a été trouvé
  4. Les échéances (calendrier) sont TOUJOURS émises, même sans texte de fiche
  5. Retourne (actions: list[ActionFiche], echeances: list[Echeance])
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from ..mistral_client import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    Compteur,
    extraire_structure,
)
from ..models import (
    ActionFiche,
    DistributeurResult,
    Echeance,
    FicheBorne,
    ScribeActionResult,
)
from .prompts.prompt_distributeur_plan_gestion import SYSTEM_PROMPT as PROMPT_DISTRIB
from .prompts.prompt_scribe_action import SYSTEM_PROMPT as PROMPT_SCRIBE

log = logging.getLogger("pipeline.plan_gestion_v2")

_OCR_DIR = Path(__file__).resolve().parent.parent
DEBUG_DISTRIB = _OCR_DIR / "debug" / "distributeur"
DEBUG_SCRIBE = _OCR_DIR / "debug" / "scribe"

SCRIBE_MODEL = "mistral-small-2603"
SCRIBE_EFFORT = "none"
SCRIBE_MAX_TOKENS = 8000
# effort=high consomme beaucoup en réflexion : viser large pour le JSON fiches+échéances
DISTRIB_MAX_TOKENS = 100000

_RE_PAGE_MD = re.compile(
    r"<!--\s*=====\s*PAGE\s+(\d+)\s*=====\s*-->|"
    r"<<<PAGE>>>|"
    r"⟦p(\d+)⟧",
    re.IGNORECASE,
)
_RE_HASHES = re.compile(r"^#{1,6}\s*")
_RE_TITRE_FICHE = re.compile(
    r"^#{1,6}\s*([A-Z]{2,3})\s*(\d{1,2})\b",
    re.MULTILINE,
)
_APOS = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "´": "'"})


def _normaliser_citation(s: str) -> str:
    t = (s or "").translate(_APOS)
    t = _RE_HASHES.sub("", t.strip())
    return re.sub(r"\s+", " ", t)


# --- Bornes OCR -------------------------------------------------------------

def trouver_ancre(haystack: str, needle: str, *, start: int = 0) -> Optional[int]:
    """Localise une citation LLM dans l'OCR (exact, hashes markdown, préfixe)."""
    if not needle or not needle.strip():
        return None

    zone = haystack[start:]
    idx = zone.find(needle)
    if idx >= 0:
        return start + idx

    nu = _normaliser_citation(needle)
    if nu and nu != needle:
        idx = zone.find(nu)
        if idx >= 0:
            return start + idx

    for n in (120, 80, 60, 40):
        for cand in (needle, nu):
            if not cand or len(cand) < n:
                continue
            idx = zone.find(cand[:n])
            if idx >= 0:
                return start + idx

    compact = nu or re.sub(r"\s+", " ", needle.strip())
    if len(compact) < 12:
        return None
    pat = re.escape(compact).replace(r"\ ", r"\s+")
    m = re.search(pat, zone, flags=re.MULTILINE)
    if m:
        return start + m.start()
    return None


def couper_par_bornes(
    markdown: str,
    debut: str,
    fin_exclusive: Optional[str],
) -> str:
    """Retourne le slice OCR [debut, fin_exclusive[ ; lève ValueError si ancre absente."""
    i = trouver_ancre(markdown, debut)
    if i is None:
        raise ValueError(f"Ancre début introuvable : {debut[:100]!r}")

    if fin_exclusive and fin_exclusive.strip():
        j = trouver_ancre(markdown, fin_exclusive, start=i + max(1, len(debut) // 4))
        if j is None:
            raise ValueError(f"Ancre fin introuvable : {fin_exclusive[:100]!r}")
        if j <= i:
            raise ValueError(
                f"Ancre fin avant début (i={i}, j={j}) : {fin_exclusive[:80]!r}"
            )
    else:
        j = len(markdown)

    chunk = markdown[i:j].strip()
    if len(chunk) < 20:
        raise ValueError(f"Chunk trop court ({len(chunk)} car.) pour début={debut[:60]!r}")
    return chunk


def indices_titres_fiches(markdown: str) -> list[tuple[int, str]]:
    """Offsets des titres de fiche-action (code normalisé TU1, SE1…)."""
    out: list[tuple[int, str]] = []
    vus: set[str] = set()
    for m in _RE_TITRE_FICHE.finditer(markdown):
        code = f"{m.group(1)}{m.group(2)}".upper()
        if code in vus:
            continue
        vus.add(code)
        out.append((m.start(), code))
    return out


def couper_par_code(markdown: str, code: str) -> Optional[str]:
    """Découpe [titre de `code`, titre de la fiche suivante[ — indépendant des citations LLM."""
    cible = (code or "").replace(" ", "").strip().upper()
    if not cible:
        return None
    titres = indices_titres_fiches(markdown)
    for i, (off, c) in enumerate(titres):
        if c != cible:
            continue
        fin = titres[i + 1][0] if i + 1 < len(titres) else len(markdown)
        chunk = markdown[off:fin].strip()
        if len(chunk) >= 20:
            return chunk
    return None


def couper_fiche(markdown: str, fiche: FicheBorne) -> tuple[str, str]:
    """Essaie les bornes LLM, puis le titre au code. Retourne (chunk, methode)."""
    try:
        return couper_par_bornes(markdown, fiche.debut, fiche.fin_exclusive), "bornes"
    except ValueError:
        pass
    par_code = couper_par_code(markdown, fiche.code or fiche.id)
    if par_code:
        return par_code, "code"
    return "", "vide"


def pages_du_chunk(chunk: str) -> list[int]:
    pages: list[int] = []
    for m in _RE_PAGE_MD.finditer(chunk):
        n = m.group(1) or m.group(2)
        if n:
            pages.append(int(n))
    # dédup en conservant l'ordre
    vus: set[int] = set()
    out: list[int] = []
    for p in pages:
        if p not in vus:
            vus.add(p)
            out.append(p)
    return out


# --- LLM distributeur -------------------------------------------------------

def distribuer(
    markdown: str,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    compteur: Compteur | None = None,
    max_tokens: int = DISTRIB_MAX_TOKENS,
    debug_dir: Path | None = None,
    entete_contexte: str = "",
) -> DistributeurResult:
    blocs = []
    if entete_contexte.strip():
        blocs.append(entete_contexte.strip())
    blocs.append(
        "Cartographie le plan de gestion ci-dessous : chaque fiche-action "
        "avec ses bornes OCR exactes (`debut` / `fin_exclusive`) et ses "
        "échéances (récurrence). N'inclus AUCUN contenu intégral.\n"
        "Tague chaque fiche et chaque échéance avec `ug_ids` issus du "
        "référentiel SIG ci-dessus (ou [\"a_definir\"] si doute)."
    )
    blocs.append(f"<plan_de_gestion>\n{markdown}\n</plan_de_gestion>")
    return extraire_structure(
        system_prompt=PROMPT_DISTRIB,
        user_prompt="\n\n".join(blocs),
        result_type=DistributeurResult,
        etiquettes="DISTRIB",
        debug_dir=debug_dir or DEBUG_DISTRIB,
        debug_prefixe="distrib",
        model=model,
        effort=effort,
        max_tokens=max_tokens,
        utiliser_schema=False,
        compteur=compteur,
        schema_name="distributeur_plan_gestion",
    )


# --- LLM scribe -------------------------------------------------------------

def scribe_action(
    chunk: str,
    fiche: FicheBorne,
    *,
    model: str = SCRIBE_MODEL,
    effort: str = SCRIBE_EFFORT,
    compteur: Compteur | None = None,
    max_tokens: int = SCRIBE_MAX_TOKENS,
    debug_dir: Path | None = None,
) -> ScribeActionResult:
    return extraire_structure(
        system_prompt=PROMPT_SCRIBE,
        user_prompt=(
            f"Fiche `{fiche.id}` — {fiche.titre}\n\n"
            "Nettoie le fragment OCR suivant pour en faire le contenu stockable "
            "de l'action :\n\n"
            f"<chunk_ocr>\n{chunk}\n</chunk_ocr>"
        ),
        result_type=ScribeActionResult,
        etiquettes=f"SCRIBE:{fiche.id}",
        debug_dir=debug_dir or DEBUG_SCRIBE,
        debug_prefixe=f"scribe_{fiche.id.lower()}",
        model=model,
        effort=effort,
        max_tokens=max_tokens,
        utiliser_schema=False,
        compteur=compteur,
        schema_name="scribe_action",
    )


def _contenu_fallback(fiche: FicheBorne, chunk: str) -> str:
    if chunk.strip():
        return chunk.strip()
    parts = [f"# {fiche.code} — {fiche.titre}"]
    if fiche.objectif_long_terme:
        parts.append(f"**Objectif long terme** : {fiche.objectif_long_terme}")
    if fiche.objectif_operationnel:
        parts.append(f"**Objectif opérationnel** : {fiche.objectif_operationnel}")
    if fiche.periodicite_texte:
        parts.append(f"**Périodicité** : {fiche.periodicite_texte}")
    parts.append(
        "_Texte OCR non localisé (bornes et titre de fiche introuvables). "
        "Les échéances restent valides pour le calendrier._"
    )
    return "\n\n".join(parts)


def _action_depuis(
    fiche: FicheBorne,
    chunk: str,
    scribe: ScribeActionResult | None,
) -> ActionFiche:
    contenu = (scribe.contenu_propre.strip() if scribe and scribe.contenu_propre else "") or _contenu_fallback(
        fiche, chunk
    )
    pages = pages_du_chunk(chunk) if chunk else []
    return ActionFiche(
        id=fiche.id,
        code=fiche.code,
        categorie=fiche.categorie,
        titre=fiche.titre,
        lib_thema=fiche.lib_thema,
        objectif_long_terme=fiche.objectif_long_terme,
        objectif_operationnel=fiche.objectif_operationnel,
        ug_ids=list(fiche.ug_ids),
        zone_source_proposee=fiche.zone_source_proposee,
        parcelles=list(fiche.parcelles),
        communes=list(fiche.communes),
        cadrage_surfacique=fiche.cadrage_surfacique,
        description=scribe.description if scribe else None,
        engagements=list(scribe.engagements) if scribe else [],
        indicateurs=list(scribe.indicateurs) if scribe else [],
        intervenants=list(scribe.intervenants) if scribe else [],
        periodicite_texte=fiche.periodicite_texte,
        frise_markdown=scribe.frise_markdown if scribe else None,
        contenu_integral=contenu,
        pages=pages,
        confiance=fiche.confiance if chunk else min(fiche.confiance, 0.55),
        champs_a_confirmer=list(fiche.champs_a_confirmer),
        avertissements=list(fiche.avertissements),
    )


def _propager_lib_thema(fiche: FicheBorne, echeances: list[Echeance]) -> list[Echeance]:
    """Propage lib_thema / code / ug_ids de la fiche si l'échéance les a laissés vides."""
    out: list[Echeance] = []
    for e in echeances:
        data = e.model_dump()
        if (not data.get("lib_thema") or data["lib_thema"] == "autre") and fiche.lib_thema:
            data["lib_thema"] = fiche.lib_thema
        if not data.get("code_operation"):
            data["code_operation"] = fiche.code
        if not data.get("type_operation"):
            data["type_operation"] = fiche.categorie
        if not data.get("ug_ids") and fiche.ug_ids:
            data["ug_ids"] = list(fiche.ug_ids)
        if not data.get("zone_source_proposee") and fiche.zone_source_proposee:
            data["zone_source_proposee"] = fiche.zone_source_proposee
        out.append(Echeance.model_validate(data))
    return out


def _appliquer_ug(
    ug_ids: list[str],
    *,
    zone_proposee: str | None,
    connus: set[str],
    alias: dict[str, str],
    champs_a_confirmer: list[str],
) -> tuple[list[str], list[str]]:
    from ..domain.ug_ids import reconcilier_ug_ids

    ugs, extras = reconcilier_ug_ids(
        ug_ids,
        connus=connus or None,
        zone_proposee=zone_proposee,
        alias_vers_ug=alias,
        forcer_indefini_si_vide=True,
    )
    champs = list(champs_a_confirmer)
    for x in extras:
        if x not in champs:
            champs.append(x)
    return ugs, champs


def _reconcilier_fiche_ugs(
    fiche: FicheBorne,
    *,
    connus: set[str],
    alias: dict[str, str],
) -> FicheBorne:
    ugs, champs = _appliquer_ug(
        list(fiche.ug_ids),
        zone_proposee=fiche.zone_source_proposee,
        connus=connus,
        alias=alias,
        champs_a_confirmer=list(fiche.champs_a_confirmer),
    )
    data = fiche.model_dump()
    data["ug_ids"] = ugs
    data["champs_a_confirmer"] = champs
    echeances_ok: list[dict] = []
    for e in data.get("echeances") or []:
        e_ugs, e_champs = _appliquer_ug(
            list(e.get("ug_ids") or []) or ugs,
            zone_proposee=e.get("zone_source_proposee") or fiche.zone_source_proposee,
            connus=connus,
            alias=alias,
            champs_a_confirmer=list(e.get("champs_a_confirmer") or []),
        )
        e["ug_ids"] = e_ugs
        e["champs_a_confirmer"] = e_champs
        echeances_ok.append(e)
    data["echeances"] = echeances_ok
    return FicheBorne.model_validate(data)


# --- Orchestration ----------------------------------------------------------

def extraire(
    markdown: str,
    *,
    model_distrib: str = DEFAULT_MODEL,
    effort_distrib: str = DEFAULT_EFFORT,
    model_scribe: str = SCRIBE_MODEL,
    effort_scribe: str = SCRIBE_EFFORT,
    compteur: Compteur | None = None,
    debug_dir: Path | None = None,
    max_tokens_distrib: int = DISTRIB_MAX_TOKENS,
    entete_contexte: str = "",
    ug_ids_connus: set[str] | None = None,
    alias_ug: dict[str, str] | None = None,
) -> tuple[list[ActionFiche], list[Echeance]]:
    """
    Pipeline complet. Retourne (actions prêtes à stocker, échéances pour le semoir).
    """
    debug_distrib = (debug_dir / "distributeur") if debug_dir else DEBUG_DISTRIB
    debug_scribe = (debug_dir / "scribe") if debug_dir else DEBUG_SCRIBE
    connus = set(ug_ids_connus or ())
    alias = dict(alias_ug or {})

    distrib = distribuer(
        markdown,
        model=model_distrib,
        effort=effort_distrib,
        compteur=compteur,
        max_tokens=max_tokens_distrib,
        debug_dir=debug_distrib,
        entete_contexte=entete_contexte,
    )
    log.info("Distributeur : %d fiche(s)", len(distrib.fiches))
    print(f"   📋 distributeur → {len(distrib.fiches)} fiche(s)", flush=True)
    if connus:
        print(
            f"   🗺️  référentiel UG : {', '.join(sorted(connus))}",
            flush=True,
        )

    actions: list[ActionFiche] = []
    echeances: list[Echeance] = []

    for fiche_brute in distrib.fiches:
        fiche = _reconcilier_fiche_ugs(fiche_brute, connus=connus, alias=alias)
        print(
            f"   ✂ {fiche.id} · bornes… · ug={fiche.ug_ids or ['—']}",
            flush=True,
        )
        chunk, methode = couper_fiche(markdown, fiche)
        if methode != "bornes":
            msg = (
                f"bornes invalides → découpe par code {fiche.code}"
                if methode == "code"
                else "bornes et titre introuvables — action minimale, échéances conservées"
            )
            log.warning("Slice %s : %s", fiche.id, msg)
            print(f"   ⚠️  {fiche.id} : {msg}", flush=True)
            fiche.avertissements = list(fiche.avertissements) + [f"decoupe: {methode}"]

        scribe: ScribeActionResult | None = None
        if len(chunk) >= 80:
            print(
                f"   ✍️  {fiche.id} · scribe ({model_scribe}, effort={effort_scribe}) "
                f"· {len(chunk):,} car. OCR ({methode})",
                flush=True,
            )
            try:
                scribe = scribe_action(
                    chunk,
                    fiche,
                    model=model_scribe,
                    effort=effort_scribe,
                    compteur=compteur,
                    debug_dir=debug_scribe,
                )
            except Exception as err:  # noqa: BLE001
                log.warning("Scribe %s échoué : %s — fallback chunk OCR", fiche.id, err)
                print(f"   ⚠️  scribe {fiche.id} échoué ({err}) — stocke le chunk brut", flush=True)
                fiche.avertissements = list(fiche.avertissements) + [f"scribe: {err}"]
        else:
            print(f"   📎 {fiche.id} · pas de scribe (chunk {len(chunk)} car.)", flush=True)

        actions.append(_action_depuis(fiche, chunk, scribe))
        echeances.extend(_propager_lib_thema(fiche, list(fiche.echeances)))

    codes_actions = {a.code for a in actions} | {a.id for a in actions}
    for e in echeances:
        code = (e.code_operation or "").replace(" ", "").upper()
        if code and code not in codes_actions:
            stub = ActionFiche(
                id=code,
                code=code,
                categorie=e.type_operation,
                titre=e.libelle or code,
                lib_thema=e.lib_thema,
                objectif_long_terme=e.objectif_long_terme,
                objectif_operationnel=e.objectif_operationnel,
                ug_ids=list(e.ug_ids),
                zone_source_proposee=e.zone_source_proposee,
                parcelles=list(e.parcelles),
                communes=list(e.communes),
                contenu_integral=(
                    f"# {code} — {e.libelle or code}\n\n"
                    "_Fiche reconstituée depuis les échéances (absent du catalogue distributeur)._"
                ),
                confiance=min(e.confiance, 0.5),
                avertissements=["stub: créée depuis les échéances pour le calendrier"],
            )
            actions.append(stub)
            codes_actions.add(code)
            print(f"   🧩 action stub {code} (échéances orphelines)", flush=True)

    print(
        f"   ✅ plan_gestion v2 : {len(actions)} action(s), {len(echeances)} échéance(s)",
        flush=True,
    )
    return actions, echeances
