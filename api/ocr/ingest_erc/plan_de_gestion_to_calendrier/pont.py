"""Pont passe 3 : un PDF de plan de gestion devient des échéances et des occurrences.

Le moteur métier est `pgcal` (sept étapes, du texte aux occurrences datées).
Ce module ne fait que l'appeler, journaliser, puis projeter sa sortie sur les
contrats déjà ingérés en base (`Echeance`, `Occurrence`).

Il n'est utilisé que pour les documents de rôle `plan_gestion`. Un tableur
présent dans le même dossier conserve la priorité sur les codes qu'il date.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from api.ocr.calcul_occurrences import formater_code
from api.ocr.models import Echeance, FenetreIntervention, Occurrence, Recurrence, Source

from .pgcal.schemas import CalendarResult, Rule


def journal(msg: str) -> None:
    print(f"[pgcal] {msg}", flush=True)


def code_canonique(code: str) -> str:
    """« TU 01 » → « TU1 ». Même règle que la passe 3."""
    s = unicodedata.normalize("NFKD", str(code)).encode("ascii", "ignore").decode().strip()
    m = re.fullmatch(r"([A-Za-z]+)[\s\-_.]*0*(\d+)([a-z]?)", s)
    return f"{m.group(1).upper()}{m.group(2)}{m.group(3)}" if m else s.upper().replace(" ", "")


def plages_mois(months: list[int]) -> list[tuple[int | None, int | None, bool]]:
    """Découpe des mois en fenêtres contiguës. Novembre–février traverse l'année."""
    ms = sorted({int(m) for m in months if 1 <= int(m) <= 12})
    if not ms:
        return [(None, None, False)]
    groupes: list[tuple[int, int]] = []
    debut = precedent = ms[0]
    for m in ms[1:]:
        if m == precedent + 1:
            precedent = m
            continue
        groupes.append((debut, precedent))
        debut = precedent = m
    groupes.append((debut, precedent))
    if len(groupes) >= 2 and groupes[0][0] == 1 and groupes[-1][1] == 12:
        return [(groupes[-1][0], groupes[0][1], True), *[(a, b, False) for a, b in groupes[1:-1]]]
    return [(a, b, False) for a, b in groupes]


@dataclass
class Projection:
    document: str
    run_dir: str
    echeances: list[dict] = field(default_factory=list)
    occurrences: list[dict] = field(default_factory=list)
    non_placables: list[dict] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def analyser_plan(pdf: Path, *, annee_n: int | None = None) -> tuple[CalendarResult, Path]:
    """Lance les 7 étapes pgcal sur un PDF. Les artefacts restent à côté du fichier."""
    from .pgcal.config import Settings
    from .pgcal.llm import LLMUnavailable
    from .pgcal.pipeline import run

    from api.ocr.ingest_erc.main_pipeline_a_utiliser import config as ingest_config

    ingest_config.charger_dotenv()
    pdf = Path(pdf)
    if not pdf.is_file():
        raise RuntimeError(f"Plan de gestion introuvable : {pdf}")

    reglages = Settings()
    reglages.api_key = ingest_config.api_key() or reglages.api_key
    if not os.environ.get("PGCAL_OCR_MODEL"):
        reglages.ocr_model = ingest_config.modele_ocr()
    reglages.runs_dir = pdf.resolve().parent / "_pgcal"
    if annee_n:
        reglages.n_year_override = int(annee_n)

    journal(f"document : {pdf.name}")
    journal(
        f"modèles  llm={reglages.llm_model}  ocr={reglages.ocr_model}  "
        f"échantillons={reglages.samples_k}  "
        f"vision grilles={'oui' if reglages.grid_vision_crosscheck else 'non'}"
    )
    if annee_n:
        journal(f"année N retenue (référentiel verrouillé) : {annee_n}")
    if not reglages.api_key:
        raise RuntimeError(
            "Analyse du plan de gestion impossible : clé Mistral absente (MISTRAL_API_KEY_BEN)."
        )

    try:
        resultat, run_dir = run(pdf, reglages, log=journal)
    except LLMUnavailable as err:
        raise RuntimeError(f"Analyse du plan de gestion impossible : {err}") from err
    except SystemExit as err:
        raise RuntimeError(f"Analyse du plan de gestion impossible : {err.code or err}") from err
    journal(f"terminé — {run_dir}")
    return resultat, run_dir


def _confiance(valeur: float) -> float:
    return round(min(1.0, max(0.0, float(valeur))), 2)


def _statut(decision: str, annee: int, optionnel: bool, hypothese: str) -> str:
    if decision != "integre" or optionnel or hypothese:
        return "a_confirmer"
    if annee < date.today().year:
        return "a_confirmer"
    return "planifie"


def _fenetre(debut: int | None, fin: int | None, traverse: bool, texte: str) -> FenetreIntervention:
    return FenetreIntervention(
        debut=f"{debut:02d}-01" if debut else None,
        fin=f"{fin:02d}-28" if fin else None,
        traverse_nouvel_an=traverse,
        texte_source=texte or None,
    )


def _recurrence(annees: list[int], regle: Rule) -> Recurrence:
    if annees:
        return Recurrence(
            type="explicite",
            annees=sorted(set(annees)),
            ancrage_annee=min(annees),
            regle_source=(regle.frequency_text or regle.window_text or None),
        )
    return Recurrence(
        type="dependant_evenement",
        regle_source=regle.trigger or regle.frequency_text or "sans date exploitable",
    )


def _identifiant(*morceaux: object) -> str:
    brut = "|".join(str(m) for m in morceaux)
    return hashlib.sha1(brut.encode()).hexdigest()[:10]


def projeter(
    resultat: CalendarResult,
    *,
    run_dir: Path | None = None,
    titres: dict[str, str] | None = None,
) -> Projection:
    """Une règle pgcal → une échéance par fenêtre de mois. Une occurrence datée → une ligne de calendrier."""
    cadre = resultat.frame
    titres_doc = {code_canonique(a.code): a.title for a in cadre.actions if a.title}
    categories = {code_canonique(a.code): a.category for a in cadre.actions if a.category}
    for code, titre in (titres or {}).items():
        if titre:
            titres_doc[code_canonique(code)] = str(titre)

    regles = {r.rule_id: r for r in resultat.rules}
    doc = Path(resultat.document).name
    out = Projection(document=doc, run_dir=str(run_dir or ""))
    for msg in list(cadre.issues) + list(resultat.issues):
        out.avertissements.append(f"{doc} : {msg}")

    # (règle, passage, fenêtre) → occurrences datées, puis doublons de la même année écartés sur une autre échéance
    groupes: dict[tuple, list] = {}
    for occ in resultat.occurrences:
        if occ.decision in ("declencheur", "a_preciser") or occ.year is None:
            _non_placable(out, occ, regles.get(occ.rule_id), titres_doc, categories, cadre.duration_years, doc)
            continue
        annees = _annees_occ(occ)
        for annee in annees:
            for plage in plages_mois(occ.months):
                cle = (occ.rule_id, occ.pass_label, plage, occ.optional)
                groupes.setdefault(cle, []).append((annee, occ))

    for cle, entrees in groupes.items():
        rule_id, passage, plage, optionnel = cle
        regle = regles.get(rule_id)
        if regle is None:
            out.avertissements.append(f"{doc} : occurrence sans règle {rule_id}")
            continue
        par_annee: dict[int, list] = {}
        for annee, occ in entrees:
            par_annee.setdefault(annee, []).append(occ)
        largeur = max(len(v) for v in par_annee.values())
        for i in range(largeur):
            tranche = [(an, v[i]) for an, v in sorted(par_annee.items()) if len(v) > i]
            _echeance_et_occurrences(
                out, regle, passage, plage, optionnel, tranche, titres_doc, categories,
                cadre.duration_years, doc, i,
            )

    decisions: dict[str, int] = {}
    for occ in resultat.occurrences:
        decisions[occ.decision] = decisions.get(occ.decision, 0) + 1
    out.stats = {
        "document": doc,
        "run_dir": out.run_dir,
        "regles": len(resultat.rules),
        "echeances": len(out.echeances),
        "occurrences": len(out.occurrences),
        "non_placables": len(out.non_placables),
        "decisions": decisions,
    }
    return out


def _annees_occ(occ) -> list[int]:
    if occ.year is None:
        return []
    if occ.span_end_rel is None or occ.rel_year is None:
        return [int(occ.year)]
    fin = int(occ.year) + int(occ.span_end_rel) - int(occ.rel_year)
    if fin < occ.year:
        return [int(occ.year)]
    return list(range(int(occ.year), fin + 1))


def _non_placable(out: Projection, occ, regle: Rule | None, titres, categories, duree, doc: str) -> None:
    code = code_canonique(occ.action_code or (regle.action_code if regle else ""))
    if not code:
        out.avertissements.append(f"{doc} : règle sans code, ignorée ({occ.occ_id})")
        return
    regle = regle or _regle_minimale(occ)
    titre = titres.get(code) or code
    libelle = _libelle(titre, occ.sub_action, occ.pass_label)
    page, extrait = _source(regle)
    ech = Echeance(
        id=f"{code}-{_identifiant(doc, occ.rule_id, occ.occ_id, 'sans-date')}",
        code_operation=code,
        type_operation=categories.get(code) or titre,
        libelle=libelle,
        ug_ids=list(occ.ug_codes or []),
        recurrence=_recurrence([], regle),
        fenetre_intervention=FenetreIntervention(texte_source=occ.hypothesis or None),
        duree_gestion_ans=duree,
        source=Source(page=page, extrait=extrait, doc=doc, loc=f"p.{page}" if page else None),
        confiance=_confiance(occ.confidence),
        champs_a_confirmer=["recurrence"],
        avertissements=[occ.hypothesis or occ.decision],
    )
    out.non_placables.append(ech.model_dump(mode="json"))


def _regle_minimale(occ) -> Rule:
    return Rule(action_code=occ.action_code or "?", sub_action=occ.sub_action or "", kind="non_precisee",
                frequency_text=occ.hypothesis or "")


def _libelle(titre: str, sous: str, passage: str) -> str:
    morceaux = [titre]
    if sous and sous.strip() and sous.strip() != titre:
        morceaux.append(sous.strip())
    if passage and passage.strip():
        morceaux.append(passage.strip())
    return " — ".join(morceaux)


def _source(regle: Rule) -> tuple[int | None, str | None]:
    if not regle.citations:
        return None, None
    c = regle.citations[0]
    return c.page, (c.quote or "")[:500] or None


def _echeance_et_occurrences(
    out, regle, passage, plage, optionnel, tranche, titres, categories, duree, doc, indice,
) -> None:
    code = code_canonique(regle.action_code)
    if not code:
        return
    debut, fin, traverse = plage
    annees = [an for an, _ in tranche]
    titre = titres.get(code) or code
    libelle = _libelle(titre, regle.sub_action, passage)
    page, extrait = _source(regle)
    suffixe = "" if indice == 0 else f"-{indice + 1}"
    ech_id = f"{code}-{_identifiant(doc, regle.rule_id, passage, plage, optionnel)}{suffixe}"
    premier = tranche[0][1]
    avert = [w for w in (premier.hypothesis, *regle.issues) if w]
    champs = []
    if premier.decision != "integre":
        champs.append(premier.decision)
    if optionnel:
        champs.append("passage_optionnel")
    if premier.hypothesis:
        champs.append("hypothese")
    ech = Echeance(
        id=ech_id,
        code_operation=code,
        type_operation=categories.get(code) or titre,
        libelle=libelle,
        ug_ids=list(regle.ug_codes or premier.ug_codes or []),
        recurrence=_recurrence(annees, regle),
        fenetre_intervention=_fenetre(debut, fin, traverse, regle.window_text),
        duree_gestion_ans=duree,
        source=Source(page=page, extrait=extrait, doc=doc, loc=f"p.{page}" if page else None),
        confiance=_confiance(premier.confidence),
        champs_a_confirmer=champs,
        avertissements=avert[:8],
    )
    out.echeances.append(ech.model_dump(mode="json"))
    for annee, occ in tranche:
        out.occurrences.append(Occurrence(
            echeance_cle=ech_id,
            annee=annee,
            code=formater_code(code),
            titre=libelle,
            categorie=ech.type_operation,
            statut=_statut(occ.decision, annee, bool(occ.optional), occ.hypothesis),
            ug_ids=list(occ.ug_codes or ech.ug_ids),
            mois_debut=debut,
            mois_fin=fin,
            traverse_nouvel_an=traverse,
            origine="ia",
            confiance=_confiance(occ.confidence),
            champs_a_confirmer=champs,
            avertissements=avert[:8],
        ).model_dump(mode="json"))


_PLACEHOLDER = "aucune date trouvée dans les documents"


def fusionner(
    echeances: list[dict],
    occurrences: list[dict],
    non_placables: list[dict],
    avertissements: list[str],
    projections: list[Projection],
    codes_tableur: set[str],
) -> tuple[list[dict], list[dict], list[dict], list[str], list[str]]:
    """Ajoute le calendrier pgcal. Un code déjà daté par le tableur n'est pas repris."""
    notes: list[str] = []
    codes_ajoutes: set[str] = set()
    for proj in projections:
        gardes_e, gardes_o, gardes_n = [], [], []
        laisses = set()
        for e in proj.echeances:
            code = code_canonique(str(e.get("code_operation") or ""))
            if code in codes_tableur:
                laisses.add(code)
                continue
            gardes_e.append(e)
            codes_ajoutes.add(code)
        ids = {e["id"] for e in gardes_e}
        gardes_o = [o for o in proj.occurrences if o.get("echeance_cle") in ids]
        for e in proj.non_placables:
            code = code_canonique(str(e.get("code_operation") or ""))
            if code in codes_tableur:
                laisses.add(code)
                continue
            gardes_n.append(e)
        echeances.extend(gardes_e)
        occurrences.extend(gardes_o)
        non_placables.extend(gardes_n)
        avertissements.extend(proj.avertissements)
        notes.append(
            f"[pgcal] {proj.document} : {len(gardes_o)} occurrence(s), "
            f"{len(gardes_e)} échéance(s), {len(gardes_n)} sans date"
            + (f" — artefacts {proj.run_dir}" if proj.run_dir else "")
        )
        if laisses:
            notes.append(
                f"[pgcal] {proj.document} : codes laissés au tableur ({', '.join(sorted(laisses))})"
            )

    if codes_ajoutes:
        ids_retires = set()
        conserves = []
        for e in echeances:
            code = code_canonique(str(e.get("code_operation") or ""))
            warns = " ".join(str(w) for w in (e.get("avertissements") or []))
            if code in codes_ajoutes and _PLACEHOLDER in warns:
                ids_retires.add(e.get("id"))
                continue
            conserves.append(e)
        if ids_retires:
            echeances = conserves
            non_placables = [e for e in non_placables if e.get("id") not in ids_retires]
            occurrences = [o for o in occurrences if o.get("echeance_cle") not in ids_retires]
            notes.append(
                f"[pgcal] {len(ids_retires)} échéance(s) sans date du référentiel "
                f"remplacée(s) par le calendrier du plan"
            )
    return echeances, occurrences, non_placables, avertissements, notes
