"""Recette bout en bout, sans changer le pipeline.

    python recette.py <dossier> <sortie/>

Enchaîne passe 1, acceptation automatique des propositions, verrouillage
(force=True) et passe 3. Écrit rapport.md. Les appels LLM et l'OCR passent
par le cache ingest_erc : un second lancement ne rappelle pas l'API.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from api.ocr.ingest_erc.main_pipeline_a_utiliser import config
from api.ocr.ingest_erc.main_pipeline_a_utiliser.carte_llm.carte import Regles, charger_ou_cartographier
from api.ocr.ingest_erc.main_pipeline_a_utiliser.carte_llm.carte_classeur import charger_carte_cache
from api.ocr.ingest_erc.main_pipeline_a_utiliser.lecture.inventaire import aplatir, inventorier
from api.ocr.ingest_erc.main_pipeline_a_utiliser.passes.passe1 import executer
from api.ocr.ingest_erc.main_pipeline_a_utiliser.passes.passe3 import executer_passe3
from api.ocr.ingest_erc.jobs import _resume_recurrence, construire_verrouille


def lister(dossier: Path) -> list[str]:
    if dossier.is_file():
        return [str(dossier)]
    return [str(p) for p, _ in aplatir([str(dossier)])]


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _valeur(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        return _j(v)
    return str(v).replace("|", "/")


def _sources(cellule: dict) -> str:
    morceaux = []
    for s in cellule.get("sources") or []:
        doc = s.get("doc") or ""
        loc = s.get("loc") or ""
        morceaux.append(f"{doc} {loc}".strip())
    conf = cellule.get("confiance") or ""
    src = ", ".join(morceaux) if morceaux else "sans source"
    return f"{conf} — {src}" if conf else src


def _cellule(cellule) -> str:
    if not isinstance(cellule, dict) or "valeur" not in cellule:
        return _valeur(cellule)
    return f"{_valeur(cellule.get('valeur'))} ({_sources(cellule)})"


def _tokens(bloc: dict | None) -> dict:
    b = bloc or {}
    return {
        "n": int(b.get("n") or 0),
        "prompt": int(b.get("prompt") or 0),
        "completion": int(b.get("completion") or 0),
        "total": int(b.get("total") or 0),
    }


def rapport(sortie: dict, questions: list, verrouille, res, dures: dict) -> str:
    ref = sortie.get("referentiel") or {}
    lignes = [
        "# Recette ingest_erc",
        "",
        "Les propositions ont été acceptées telles quelles, puis le référentiel a été "
        "verrouillé avec `force=True`. Rien n'est écrit en base.",
        "",
        "## Référentiel",
        "",
        "### Projet",
        "",
        "| Champ | Valeur | Confiance et source |",
        "|---|---|---|",
    ]
    for cle, cellule in (ref.get("projet") or {}).items():
        if isinstance(cellule, dict) and "valeur" in cellule:
            lignes.append(f"| {cle} | {_valeur(cellule.get('valeur'))} | {_sources(cellule)} |")
        else:
            lignes.append(f"| {cle} | {_valeur(cellule)} | |")

    lignes += ["", "### Unités de gestion", "",
               "| Code | Libellé | Type ERC | Zone SIG | Surface ha |",
               "|---|---|---|---|---|"]
    for u in ref.get("ugs") or []:
        lignes.append(
            f"| {u.get('ug_code')} | {_cellule(u.get('libelle'))} | {_cellule(u.get('type_erc'))} "
            f"| {_cellule(u.get('zone_sig'))} | {_cellule(u.get('surface_ha'))} |"
        )
    if not ref.get("ugs"):
        lignes.append("| — | | | | |")

    lignes += ["", "### Actions", "",
               "| Code | Intitulé | Nature | UG | Cible |",
               "|---|---|---|---|---|"]
    for a in ref.get("actions") or []:
        lignes.append(
            f"| {a.get('code')} | {_cellule(a.get('intitule'))} | {_cellule(a.get('nature'))} "
            f"| {_cellule(a.get('ugs'))} | {_cellule(a.get('cible'))} |"
        )
    if not ref.get("actions"):
        lignes.append("| — | | | | |")

    lignes += ["", "## Questions qui auraient été posées au BE", ""]
    if not questions:
        lignes.append("Aucune.")
    for q in questions:
        acceptee = q.get("proposition")
        statut = f"acceptée automatiquement : « {acceptee} »" if acceptee else \
            "sans proposition — laissée ouverte, verrouillage forcé"
        lignes += [f"### {q.get('id')}", "", q.get("texte") or "", "",
                   f"Options : {', '.join(q.get('options') or []) or '—'}.", "",
                   statut, ""]
        for s in q.get("sources") or []:
            lignes.append(f"- {s.get('doc', '')} {s.get('loc', '')} — {(s.get('extrait') or '')[:160]}")
        lignes.append("")

    lignes += ["", "## Échéances", "",
               "| Code | Libellé | Récurrence | Fenêtre | Source | Confiance |",
               "|---|---|---|---|---|---|"]
    echs = [e if isinstance(e, dict) else e.model_dump(mode="json") for e in (res.echeances or [])]
    for e in echs:
        fen = e.get("fenetre_intervention") or {}
        fen_txt = "—"
        if fen.get("debut") or fen.get("fin"):
            fen_txt = f"{fen.get('debut') or '—'} → {fen.get('fin') or '—'}"
        src = e.get("source") or {}
        src_txt = f"{src.get('doc') or ''} {src.get('loc') or ''}".strip() or "—"
        lignes.append(
            f"| {e.get('code_operation')} | {_valeur(e.get('libelle'))[:80]} "
            f"| {_resume_recurrence(e.get('recurrence'))} | {fen_txt} | {src_txt} "
            f"| {e.get('confiance')} |"
        )
    if not echs:
        lignes.append("| — | | | | | |")

    occs = [o if isinstance(o, dict) else o.model_dump(mode="json") for o in (res.occurrences or [])]
    annees = sorted({int(o["annee"]) for o in occs if o.get("annee") is not None})
    lignes += ["", "## Occurrences — actions × années", ""]
    if not annees:
        lignes.append("Aucune occurrence placée.")
    else:
        entete = "| Action | " + " | ".join(str(a) for a in annees) + " |"
        sep = "|---|" + "---|" * len(annees)
        lignes += [entete, sep]
        par_code: dict[str, dict[int, list[str]]] = {}
        ordre = []
        for o in occs:
            code = str(o.get("code") or "")
            if code not in par_code:
                par_code[code] = {}
                ordre.append(code)
            an = int(o["annee"])
            md, mf = o.get("mois_debut"), o.get("mois_fin")
            marque = f"{md:02d}–{mf:02d}" if md and mf else "●"
            par_code[code].setdefault(an, [])
            if marque not in par_code[code][an]:
                par_code[code][an].append(marque)
        for code in ordre:
            cellules = [" ".join(par_code[code].get(a, [])) or "·" for a in annees]
            lignes.append(f"| {code} | " + " | ".join(cellules) + " |")

    non_pl = [e if isinstance(e, dict) else e.model_dump(mode="json") for e in (res.non_placables or [])]
    if non_pl:
        lignes += ["", "Non placées :"]
        for e in non_pl:
            lignes.append(f"- {e.get('code_operation')} — {e.get('libelle')}")

    lignes += ["", "## Rejets", ""]
    rejets = [r if isinstance(r, dict) else r.model_dump(mode="json") for r in (res.rejets or [])]
    if not rejets:
        lignes.append("Aucun.")
    for r in rejets:
        src = r.get("source") or {}
        lignes.append(
            f"- **{r.get('motif')}** — {r.get('detail')} "
            f"({src.get('doc', '')} {src.get('loc', '')})".rstrip()
        )

    lignes += ["", "## Avertissements", ""]
    avert = list(res.avertissements or [])
    lignes.append("\n".join(f"- {a}" for a in avert) if avert else "Aucun.")

    t1 = _tokens(sortie.get("tokens_llm"))
    t3 = _tokens((res.stats or {}).get("tokens_llm"))
    lignes += [
        "",
        "## Coût",
        "",
        "| | Appels LLM | Tokens entrée | Tokens sortie | Durée |",
        "|---|---:|---:|---:|---:|",
        f"| Passe 1 | {t1['n']} | {t1['prompt']} | {t1['completion']} | {dures.get('passe1', '—')} s |",
        f"| Passe 3 | {t3['n']} | {t3['prompt']} | {t3['completion']} | {dures.get('passe3', '—')} s |",
        f"| Total | {t1['n'] + t3['n']} | {t1['prompt'] + t3['prompt']} | "
        f"{t1['completion'] + t3['completion']} | {dures.get('total', '—')} s |",
        "",
        "Un appel servi par le cache n'est pas recompté : au second lancement ces compteurs "
        f"retombent à zéro. Cache : `{config.cache_dir()}`.",
        "",
        f"Questions encore ouvertes au verrouillage : {', '.join(verrouille.questions_forcees) or 'aucune'}.",
        "",
    ]
    return "\n".join(lignes)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: python recette.py <dossier> <sortie/>")
    dossier, dest = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    chemins = lister(dossier)
    if not chemins:
        sys.exit(f"Aucun PDF, tableur ou SIG dans {dossier}")
    dest.mkdir(parents=True, exist_ok=True)
    config.charger_dotenv()
    print(config.banniere(), flush=True)
    print(f"{len(chemins)} fichier(s)", flush=True)

    t0 = time.time()
    t1 = time.time()
    sortie = executer(chemins)
    d_passe1 = round(time.time() - t1, 1)
    (dest / "passe1.json").write_text(
        json.dumps(sortie, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    questions = list((sortie.get("referentiel") or {}).get("questions") or [])
    reponses = {q["id"]: q["proposition"] for q in questions if q.get("proposition")}
    print(f"Acceptation de {len(reponses)} proposition(s) sur {len(questions)} question(s).", flush=True)
    _, verrouille, _ = construire_verrouille(sortie, {"reponses": reponses})
    (dest / "verrouille.json").write_text(verrouille.model_dump_json(indent=1), encoding="utf-8")

    cache = config.cache_dir()
    docs = inventorier(chemins)
    cartes = {}
    for d in docs:
        if d.role != "plan_gestion":
            continue
        try:
            c = charger_ou_cartographier(d, cache)
        except Exception as err:  # noqa: BLE001
            print(f"   ⚠️  carte ignorée ({d.nom}) : {err}", flush=True)
            c = None
        if c:
            cartes[d.nom] = c
    cartes_xl = {}
    for d in docs:
        if d.role == "tableur":
            c = charger_carte_cache(d, cache)
            if c:
                cartes_xl[d.nom] = c
    plan = next(iter(cartes.values()), None)
    regles = Regles.depuis(plan) if plan else Regles.defaut()

    t3 = time.time()
    res = executer_passe3(verrouille, docs, cartes, regles, cache, cartes_classeur=cartes_xl)
    d_passe3 = round(time.time() - t3, 1)
    (dest / "passe3.json").write_text(res.model_dump_json(indent=1), encoding="utf-8")

    dures = {"passe1": d_passe1, "passe3": d_passe3, "total": round(time.time() - t0, 1)}
    texte = rapport(sortie, questions, verrouille, res, dures)
    (dest / "rapport.md").write_text(texte, encoding="utf-8")
    print(f"Rapport : {dest / 'rapport.md'}", flush=True)


if __name__ == "__main__":
    main()
