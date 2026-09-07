"""
superviseur.py — Boucle bornée de rattrapage APRÈS l'extraction.

Pas un agent libre : un audit déterministe des trous, puis au plus
MAX_TOURS × MAX_APPELS_LLM appels small ciblés.

Trous traités :
  · fiche_sans_texte   — action sans chunk OCR → recoupe par code + scribe
  · echeance_orpheline — échéance sans action parente → stub
  · ug_indefini        — a_definir alors qu'un référentiel SIG existe
  · t0_manquant        — pas d'année de départ → aperçu des 1ères pages du plan

Les UG inventées hors référentiel sont refusées (reconcilier_ug_ids).
Le calendrier (échéances) n'est jamais réécrit ici : le semoir Python s'en charge.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .etape2_triage import CarteDossier, ParametreDetecte
from .utils.claims import Claim, Kind, ParametreDossier
from .utils.corpus import Corpus
from .utils.familles import FAMILLE_DE_ROLE, Famille
from .utils.plan import ZoneSigContexte

MAX_TOURS = 3
MAX_APPELS_LLM = 5
UG_INDEFINIE = "a_definir"
TYPES_OP = frozenset({"EP", "TU", "TE", "SE", "MG"})
SCRIBE_MODEL = "mistral-small-2603"
_RE_PREFIXE = re.compile(r"^([A-Z]{2,3})")


class Trou(BaseModel):
    type: Literal["fiche_sans_texte", "echeance_orpheline", "ug_indefini", "t0_manquant"]
    cle: str
    detail: str = ""
    doc_id: Optional[str] = None


class PropositionUg(BaseModel):
    ug_ids: list[str] = Field(default_factory=list)
    confiance: float = Field(ge=0, le=1)
    justification: str = ""


class ParamT0(BaseModel):
    annee_t0: Optional[int] = None
    duree_ans: Optional[int] = None
    origine_t0: Optional[str] = None
    confiance: float = Field(default=0.0, ge=0, le=1)


def rattraper(
    claims: list[Claim],
    corpus: Corpus,
    carte: CarteDossier,
    *,
    zones_sig: list[ZoneSigContexte] | None = None,
    compteur=None,
    debug_dir: Path | None = None,
    sortie: Path | None = None,
) -> tuple[list[Claim], dict]:
    """Complète les trous, réécrit claims.jsonl + superviseur.json + dossier.json."""
    from ..domain.ug_ids import formater_referentiel_ug_pour_prompt

    _injecter_params_carte(claims, carte)

    zones = list(zones_sig or [])
    connus = {z.ug_id for z in zones if z.ug_id}
    alias = _alias_zones(zones)
    recap: dict = {
        "tours": 0,
        "appels_llm": 0,
        "trous_initiaux": [],
        "corrections": [],
        "trous_restants": [],
    }

    trous = lister_trous(claims, carte, connus)
    recap["trous_initiaux"] = [t.model_dump() for t in trous]
    if not trous:
        print("   ✓ superviseur : aucun trou", flush=True)
        recap["trous_restants"] = []
        _ecrire(sortie, claims, recap, carte)
        return claims, recap

    print(f"   🔎 superviseur : {len(trous)} trou(s) — rattrapage borné", flush=True)
    referentiel_txt = formater_referentiel_ug_pour_prompt(zones)

    for tour in range(1, MAX_TOURS + 1):
        recap["tours"] = tour
        if not trous:
            break
        faits = 0
        for trou in list(trous):
            llm_ok = recap["appels_llm"] < MAX_APPELS_LLM
            ok, used_llm = _traiter(
                trou, claims, corpus, carte,
                connus=connus, alias=alias,
                compteur=compteur, debug_dir=debug_dir,
                llm_ok=llm_ok,
                referentiel_txt=referentiel_txt,
            )
            if used_llm:
                recap["appels_llm"] += 1
            if ok:
                faits += 1
                recap["corrections"].append(trou.model_dump())
        trous = lister_trous(claims, carte, connus)
        print(
            f"   · tour {tour} : {faits} correction(s), "
            f"{len(trous)} trou(s) restant(s), LLM={recap['appels_llm']}/{MAX_APPELS_LLM}",
            flush=True,
        )
        if faits == 0:
            break

    recap["trous_restants"] = [t.model_dump() for t in trous]
    _ecrire(sortie, claims, recap, carte)
    return claims, recap


def lister_trous(
    claims: list[Claim],
    carte: CarteDossier,
    connus: set[str],
) -> list[Trou]:
    actions = [c for c in claims if c.kind == Kind.action]
    echeances = [c for c in claims if c.kind == Kind.echeance_regle]
    codes = {
        _norm_code(k)
        for a in actions
        for k in (a.cle_locale, a.donnees.get("id"), a.donnees.get("code"))
        if k
    }

    trous: list[Trou] = []
    for a in actions:
        contenu = (a.donnees.get("contenu_integral") or "")
        avert = " ".join(a.donnees.get("avertissements") or a.avertissements or [])
        mince = (
            len(contenu) < 80
            or "non localisé" in contenu
            or "découpe: vide" in avert
            or "decoupe: vide" in avert
        )
        if mince:
            trous.append(Trou(
                type="fiche_sans_texte",
                cle=str(a.cle_locale or a.donnees.get("id") or "?"),
                detail="contenu OCR absent ou trop court",
                doc_id=a.doc_id,
            ))
        if connus and _uniquement_indefini(a.donnees.get("ug_ids") or []):
            trous.append(Trou(
                type="ug_indefini",
                cle=f"action:{a.cle_locale or a.donnees.get('id')}",
                detail="ug_ids = a_definir alors qu'un référentiel SIG existe",
                doc_id=a.doc_id,
            ))

    for e in echeances:
        code = _norm_code(e.donnees.get("code_operation") or e.cle_locale or "")
        if code and code not in codes:
            trous.append(Trou(
                type="echeance_orpheline",
                cle=str(e.donnees.get("id") or e.cle_locale or code),
                detail=f"code {code} sans action parente",
                doc_id=e.doc_id,
            ))
        if connus and _uniquement_indefini(e.donnees.get("ug_ids") or []):
            trous.append(Trou(
                type="ug_indefini",
                cle=f"echeance:{e.donnees.get('id') or e.cle_locale}",
                detail="ug_ids = a_definir alors qu'un référentiel SIG existe",
                doc_id=e.doc_id,
            ))

    if _annee_t0(carte, claims) is None:
        trous.append(Trou(
            type="t0_manquant",
            cle="annee_t0",
            detail="année de départ absente",
        ))

    vus: set[tuple[str, str]] = set()
    uniques: list[Trou] = []
    for t in trous:
        k = (t.type, t.cle)
        if k in vus:
            continue
        vus.add(k)
        uniques.append(t)
    return uniques


# --- outils -----------------------------------------------------------------


def _traiter(
    trou: Trou,
    claims: list[Claim],
    corpus: Corpus,
    carte: CarteDossier,
    *,
    connus: set[str],
    alias: dict[str, str],
    compteur,
    debug_dir: Path | None,
    llm_ok: bool,
    referentiel_txt: str,
) -> tuple[bool, bool]:
    if trou.type == "echeance_orpheline":
        return _stub_action(trou, claims), False
    if trou.type == "fiche_sans_texte":
        return _recouper_fiche(trou, claims, corpus, compteur, debug_dir, llm_ok)
    if trou.type == "ug_indefini":
        return _retaguer_ug(
            trou, claims, connus, alias, referentiel_txt,
            compteur, debug_dir, llm_ok,
        )
    if trou.type == "t0_manquant":
        return _extraire_t0(claims, corpus, carte, compteur, debug_dir, llm_ok)
    return False, False


def _stub_action(trou: Trou, claims: list[Claim]) -> bool:
    from ..models import ActionFiche

    cible = None
    for c in claims:
        if c.kind != Kind.echeance_regle:
            continue
        eid = str(c.donnees.get("id") or c.cle_locale or "")
        if eid == trou.cle or trou.cle in eid:
            cible = c
            break
    if cible is None:
        return False
    code = _norm_code(cible.donnees.get("code_operation") or "")
    if not code:
        return False
    if any(
        c.kind == Kind.action
        and _norm_code(c.donnees.get("id") or c.cle_locale or "") == code
        for c in claims
    ):
        return False
    cat = cible.donnees.get("type_operation") or _categorie_depuis_code(code)
    fiche = ActionFiche(
        id=code,
        code=code,
        categorie=cat,
        titre=cible.donnees.get("libelle") or code,
        lib_thema=cible.donnees.get("lib_thema") or "autre",
        ug_ids=list(cible.donnees.get("ug_ids") or []),
        contenu_integral=f"# {code}\n\n_Fiche reconstituée par le superviseur._",
        confiance=0.45,
        avertissements=["superviseur: stub depuis échéance orpheline"],
    )
    claims.append(
        Claim.depuis(
            fiche,
            kind=Kind.action,
            doc_id=cible.doc_id,
            extracteur="superviseur",
            version="1.0.0",
            ancres=list(cible.ancres),
            confiance=0.45,
            avertissements=list(fiche.avertissements),
            cle_locale=code,
        )
    )
    print(f"      + stub action {code}", flush=True)
    return True


def _recouper_fiche(
    trou: Trou,
    claims: list[Claim],
    corpus: Corpus,
    compteur,
    debug_dir: Path | None,
    llm_ok: bool,
) -> tuple[bool, bool]:
    from ..extractions.extract_plan_gestion_v2 import couper_par_code, scribe_action
    from ..models import ActionFiche, FicheBorne

    action = next(
        (
            c for c in claims
            if c.kind == Kind.action
            and str(c.cle_locale or c.donnees.get("id") or "") == trou.cle
        ),
        None,
    )
    if action is None:
        return False, False
    doc = corpus.doc(action.doc_id)
    if doc is None:
        return False, False
    markdown = doc.rendu()
    chunk = couper_par_code(markdown, trou.cle)
    if not chunk or len(chunk) < 80:
        return False, False

    used_llm = False
    scribe = None
    if llm_ok:
        try:
            fiche = FicheBorne(
                id=str(action.donnees.get("id") or trou.cle),
                code=str(action.donnees.get("code") or trou.cle),
                categorie=action.donnees.get("categorie") or _categorie_depuis_code(trou.cle),
                titre=str(action.donnees.get("titre") or trou.cle),
                debut=chunk[:80],
                fin_exclusive=None,
                lib_thema=action.donnees.get("lib_thema") or "autre",
                ug_ids=list(action.donnees.get("ug_ids") or []),
                confiance=float(action.donnees.get("confiance") or 0.6),
            )
            scribe = scribe_action(
                chunk, fiche,
                compteur=compteur,
                debug_dir=debug_dir,
            )
            used_llm = True
        except Exception as err:  # noqa: BLE001
            print(f"      ⚠️  scribe rattrapage {trou.cle} : {err}", flush=True)

    payload = dict(action.donnees)
    payload["contenu_integral"] = (
        (scribe.contenu_propre.strip() if scribe and scribe.contenu_propre else "")
        or chunk
    )
    if scribe:
        if scribe.description:
            payload["description"] = scribe.description
        if scribe.engagements:
            payload["engagements"] = list(scribe.engagements)
        if scribe.frise_markdown:
            payload["frise_markdown"] = scribe.frise_markdown
    avert = [
        a for a in (payload.get("avertissements") or [])
        if "decoupe: vide" not in a and "découpe: vide" not in a
    ]
    avert.append("superviseur: texte recoupé par code")
    payload["avertissements"] = avert
    try:
        ActionFiche.model_validate(payload)
    except Exception:  # noqa: BLE001
        return False, used_llm
    action.donnees = payload
    action.avertissements = avert
    print(f"      + texte {trou.cle} ({len(payload['contenu_integral']):,} car.)", flush=True)
    return True, used_llm


def _retaguer_ug(
    trou: Trou,
    claims: list[Claim],
    connus: set[str],
    alias: dict[str, str],
    referentiel_txt: str,
    compteur,
    debug_dir: Path | None,
    llm_ok: bool,
) -> tuple[bool, bool]:
    from ..domain.ug_ids import reconcilier_ug_ids

    kind_pref, _, cle = trou.cle.partition(":")
    claim = _trouver_claim(claims, kind_pref, cle)
    if claim is None:
        return False, False

    texte = " ".join(
        str(x) for x in (
            claim.donnees.get("titre"),
            claim.donnees.get("libelle"),
            claim.donnees.get("zone_source_proposee"),
            claim.donnees.get("description"),
            " ".join(claim.donnees.get("communes") or []),
            (claim.donnees.get("contenu_integral") or "")[:800],
        ) if x
    )
    ugs, extras = reconcilier_ug_ids(
        claim.donnees.get("ug_ids"),
        connus=connus,
        zone_proposee=texte,
        alias_vers_ug=alias,
        forcer_indefini_si_vide=True,
    )
    used_llm = False
    if _uniquement_indefini(ugs) and llm_ok and connus:
        prop = _llm_ug(texte[:2500], referentiel_txt, compteur, debug_dir, trou.cle)
        used_llm = prop is not None
        if prop and prop.confiance >= 0.55:
            ugs2, extras2 = reconcilier_ug_ids(
                prop.ug_ids,
                connus=connus,
                alias_vers_ug=alias,
                forcer_indefini_si_vide=True,
            )
            if not _uniquement_indefini(ugs2):
                ugs, extras = ugs2, extras2

    if _uniquement_indefini(ugs):
        return False, used_llm

    _appliquer_ugs(claim, ugs, extras)
    if kind_pref == "action":
        code = _norm_code(claim.donnees.get("code") or claim.cle_locale or "")
        for e in claims:
            if e.kind != Kind.echeance_regle:
                continue
            if _norm_code(e.donnees.get("code_operation") or "") != code:
                continue
            if _uniquement_indefini(e.donnees.get("ug_ids") or []):
                _appliquer_ugs(e, ugs, extras)
    print(f"      + UG {trou.cle} → {ugs}", flush=True)
    return True, used_llm


def _llm_ug(
    texte: str,
    referentiel: str,
    compteur,
    debug_dir: Path | None,
    etiquette: str,
) -> Optional[PropositionUg]:
    from ..mistral_client import extraire_structure

    try:
        return extraire_structure(
            system_prompt=(
                "Tu rattaches une fiche ou une échéance aux UNITÉS DE GESTION "
                "du référentiel SIG. Utilise UNIQUEMENT les codes listés. "
                'Si le rapprochement est douteux → {"ug_ids":["a_definir"],'
                '"confiance":0.2,"justification":"…"}. JSON exclusif.'
            ),
            user_prompt=f"{referentiel}\n\n<extrait>\n{texte}\n</extrait>",
            result_type=PropositionUg,
            etiquettes=f"SUP-UG:{etiquette}",
            debug_dir=debug_dir,
            debug_prefixe=f"sup_ug_{etiquette.replace(':', '_')}",
            model=SCRIBE_MODEL,
            effort="none",
            max_tokens=800,
            utiliser_schema=False,
            compteur=compteur,
            schema_name="proposition_ug",
        )
    except Exception as err:  # noqa: BLE001
        print(f"      ⚠️  LLM UG {etiquette} : {err}", flush=True)
        return None


def _extraire_t0(
    claims: list[Claim],
    corpus: Corpus,
    carte: CarteDossier,
    compteur,
    debug_dir: Path | None,
    llm_ok: bool,
) -> tuple[bool, bool]:
    if not llm_ok:
        return False, False
    from ..mistral_client import extraire_structure

    plans = [
        d for d in corpus.documents
        if any(
            s.doc_id == d.doc_id and FAMILLE_DE_ROLE.get(s.role) == Famille.regles
            for s in carte.segments
        )
    ]
    if not plans:
        plans = [d for d in corpus.documents if d.format == "pdf"] or list(corpus.documents)
    morceaux = []
    for d in plans[:2]:
        txt = "\n\n".join(b.texte for b in d.blocs[:6])
        morceaux.append(f"### {d.nom_fichier}\n{txt[:5000]}")
    apercu = "\n\n".join(morceaux)
    if len(apercu) < 80:
        return False, False
    try:
        param: ParamT0 = extraire_structure(
            system_prompt=(
                "Extrais UNIQUEMENT l'année de départ de la compensation (annee_t0) "
                "et la durée d'engagement en années si elle est écrite. "
                "N'invente rien. JSON "
                '{"annee_t0":2019,"duree_ans":30,"origine_t0":"état zéro","confiance":0.8}'
            ),
            user_prompt=apercu,
            result_type=ParamT0,
            etiquettes="SUP-T0",
            debug_dir=debug_dir,
            debug_prefixe="sup_t0",
            model=SCRIBE_MODEL,
            effort="none",
            max_tokens=600,
            utiliser_schema=False,
            compteur=compteur,
            schema_name="param_t0",
        )
    except Exception as err:  # noqa: BLE001
        print(f"      ⚠️  LLM T0 : {err}", flush=True)
        return False, True

    if not param.annee_t0 or param.confiance < 0.45:
        return False, True

    doc_id = plans[0].doc_id if plans else (corpus.documents[0].doc_id if corpus.documents else "")
    _poser_param(
        claims, carte, doc_id,
        cle="annee_t0",
        valeur=str(param.annee_t0),
        confiance=param.confiance,
        justification=param.origine_t0,
    )
    if param.duree_ans:
        _poser_param(
            claims, carte, doc_id,
            cle="duree_ans",
            valeur=str(param.duree_ans),
            confiance=param.confiance,
        )
    print(
        f"      + T0 {param.annee_t0}"
        + (f" · {param.duree_ans} ans" if param.duree_ans else ""),
        flush=True,
    )
    return True, True


# --- helpers ----------------------------------------------------------------


def _injecter_params_carte(claims: list[Claim], carte: CarteDossier) -> None:
    """Les paramètres du triage deviennent des claims (horizon du semoir)."""
    presents = {
        c.donnees.get("cle")
        for c in claims
        if c.kind == Kind.parametre_dossier
    }
    doc_id = next((s.doc_id for s in carte.segments), "")
    for p in carte.parametres:
        if p.cle in presents:
            continue
        if not (p.valeur or "").strip():
            continue
        claims.append(
            Claim.depuis(
                ParametreDossier(cle=p.cle, valeur=p.valeur),
                kind=Kind.parametre_dossier,
                doc_id=doc_id,
                extracteur="triage",
                version="1.0.0",
                confiance=p.confiance,
                cle_locale=p.cle,
            )
        )


def _poser_param(
    claims: list[Claim],
    carte: CarteDossier,
    doc_id: str,
    *,
    cle: str,
    valeur: str,
    confiance: float,
    justification: str | None = None,
) -> None:
    claims.append(
        Claim.depuis(
            ParametreDossier(cle=cle, valeur=valeur, justification=justification),
            kind=Kind.parametre_dossier,
            doc_id=doc_id,
            extracteur="superviseur",
            version="1.0.0",
            confiance=confiance,
            cle_locale=cle,
        )
    )
    if not any(p.cle == cle for p in carte.parametres):
        carte.parametres.append(ParametreDetecte(
            cle=cle, valeur=valeur, confiance=confiance,
        ))
    else:
        for p in carte.parametres:
            if p.cle == cle and (not p.valeur or p.confiance < confiance):
                p.valeur = valeur
                p.confiance = confiance


def _annee_t0(carte: CarteDossier, claims: list[Claim]) -> Optional[int]:
    candidats: list[str] = []
    for p in carte.parametres:
        if p.cle == "annee_t0":
            candidats.append(p.valeur)
    for c in claims:
        if c.kind == Kind.parametre_dossier and c.donnees.get("cle") == "annee_t0":
            candidats.append(str(c.donnees.get("valeur") or ""))
    for v in candidats:
        try:
            y = int(str(v).strip()[:4])
        except (TypeError, ValueError):
            continue
        if 1990 <= y <= 2100:
            return y
    return None


def _duree_ans(carte: CarteDossier, claims: list[Claim]) -> Optional[int]:
    candidats: list[str] = []
    for p in carte.parametres:
        if p.cle == "duree_ans":
            candidats.append(p.valeur)
    for c in claims:
        if c.kind == Kind.parametre_dossier and c.donnees.get("cle") == "duree_ans":
            candidats.append(str(c.donnees.get("valeur") or ""))
    for v in candidats:
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 99:
            return n
    return None


def _trouver_claim(claims: list[Claim], kind_pref: str, cle: str) -> Optional[Claim]:
    want = Kind.action if kind_pref == "action" else Kind.echeance_regle
    cible = _norm_code(cle) or cle
    for c in claims:
        if c.kind != want:
            continue
        ids = {
            _norm_code(c.cle_locale or "") or "",
            _norm_code(c.donnees.get("id") or "") or "",
            _norm_code(c.donnees.get("code") or "") or "",
            _norm_code(c.donnees.get("code_operation") or "") or "",
            str(c.cle_locale or ""),
            str(c.donnees.get("id") or ""),
        }
        if cle in ids or cible in ids:
            return c
    return None


def _appliquer_ugs(claim: Claim, ugs: list[str], extras: list[str]) -> None:
    claim.donnees["ug_ids"] = ugs
    champs = list(claim.donnees.get("champs_a_confirmer") or claim.champs_a_confirmer or [])
    for x in extras:
        if x not in champs:
            champs.append(x)
    if "ug_ids" in champs and not _uniquement_indefini(ugs):
        champs = [c for c in champs if c != "ug_ids"]
    claim.donnees["champs_a_confirmer"] = champs
    claim.champs_a_confirmer = champs


def _uniquement_indefini(ugs: list) -> bool:
    vals = [str(u) for u in (ugs or []) if u]
    if not vals:
        return True
    return all(v.replace(" ", "").lower() in {UG_INDEFINIE, "adefinir"} for v in vals)


def _norm_code(raw) -> str:
    return str(raw or "").replace(" ", "").strip().upper()


def _categorie_depuis_code(code: str) -> str:
    m = _RE_PREFIXE.match(_norm_code(code))
    if m and m.group(1) in TYPES_OP:
        return m.group(1)
    return "TU"


def _alias_zones(zones: list[ZoneSigContexte]) -> dict[str, str]:
    alias: dict[str, str] = {}
    for z in zones:
        if not z.ug_id:
            continue
        for cle in (z.ug_id, z.libelle, z.nom_source, z.source_fichier or ""):
            c = (cle or "").strip().lower()
            if c and c not in alias:
                alias[c] = z.ug_id
    return alias


def _ecrire(
    sortie: Path | None,
    claims: list[Claim],
    recap: dict,
    carte: CarteDossier,
) -> None:
    if sortie is None:
        return
    sortie.mkdir(parents=True, exist_ok=True)
    with (sortie / "claims.jsonl").open("w", encoding="utf-8") as f:
        for c in claims:
            f.write(c.model_dump_json() + "\n")
    (sortie / "superviseur.json").write_text(
        json.dumps(recap, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (sortie / "carte.json").write_text(
        carte.model_dump_json(indent=2), encoding="utf-8",
    )
    t0 = _annee_t0(carte, claims)
    if not t0:
        return
    from ..models import DossierMetadata, DossierResult, HorizonGestion

    duree_i = _duree_ans(carte, claims)
    fin = (t0 + duree_i) if duree_i else None
    dossier = DossierResult(
        dossier=DossierMetadata(
            horizon=HorizonGestion(
                annee_debut=t0,
                annee_fin=fin,
                duree_ans=duree_i,
            )
        )
    )
    (sortie / "dossier.json").write_text(
        dossier.model_dump_json(indent=2), encoding="utf-8",
    )
