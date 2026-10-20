"""Mesure la cartographie des pages : un appel après l'autre, ou les quatre ensemble.

Le pipeline n'est pas modifié. À lancer quand aucune autre extraction Mistral
ne tourne : ces appels partagent le même quota.

    python bench_roles_parallele.py plan.pdf --mode parallele --ecart 3
    python bench_roles_parallele.py plan.pdf --mode serie
    python bench_roles_parallele.py plan.pdf --mode les-deux

`--ecart` espace le *départ* des appels parallèles, en secondes. Une fois partis,
ils restent en vol en même temps : la limite de débit Mistral (requêtes et tokens)
s'applique à tous. Un 429 est affiché tel quel, sans nouvel essai.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[4]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.ocr.ingest_erc.main_pipeline_a_utiliser import config as ingest_config
from api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pgcal import prompts
from api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pgcal.config import Settings
from api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pgcal.llm import MistralClient
from api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pgcal.s1_ingest import ingest
from api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pgcal.s2_locate import _compact, _heuristics
from api.ocr.ingest_erc.plan_de_gestion_to_calendrier.pgcal.schemas import PageRolesLLM


def _lots(doc, infos, taille: int) -> list[tuple[str, str]]:
    lots = []
    for start in range(0, doc.n_pages, taille):
        chunk = doc.pages[start:start + taille]
        pages = "\n\n".join(f"[PAGE {p.number}]\n" + _compact(p.text)[:1400] for p in chunk)
        hints = "\n".join(
            f"p{i.number}: codes={i.codes[:8]} grille={'oui' if i.grid_hint else 'non'} fiche_regex={i.fiche_code}"
            for i in infos[start:start + taille]
        )
        lots.append((f"roles_{start + 1}", prompts.PAGE_ROLES.format(hints=hints, pages=pages)))
    return lots


def _appel(client, tag: str, user: str) -> dict:
    """Un seul essai. Le cache du pipeline n'est pas utilisé."""
    print(f"  → {tag}  envoi Mistral (mistral-large-4-0)", flush=True)
    t0 = time.time()
    try:
        resp = client.chat.complete(
            model="mistral-large-4-0",
            messages=[
                {"role": "system", "content": prompts.SYSTEM_BASE},
                {"role": "user", "content": user},
            ],
            temperature=0,
            top_p=1,
            max_tokens=16_000,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "PageRolesLLM",
                    "schema": PageRolesLLM.model_json_schema(),
                    "strict": False,
                },
            },
        )
        usage = getattr(resp, "usage", None)
        tokens = usage.model_dump() if usage is not None and hasattr(usage, "model_dump") else {}
        duree = round(time.time() - t0, 1)
        print(f"  ← {tag}  ok  {duree}s  tokens={tokens}", flush=True)
        return {"tag": tag, "ok": True, "s": duree, "erreur": "", "tokens": tokens}
    except Exception as err:  # noqa: BLE001
        msg = str(err).replace("\n", " ")
        duree = round(time.time() - t0, 1)
        print(f"  ← {tag}  ERREUR  {duree}s  {msg[:300]}", flush=True)
        return {"tag": tag, "ok": False, "s": duree, "erreur": msg[:300], "tokens": {}}


def _serie(client, lots: list[tuple[str, str]]) -> tuple[float, list[dict]]:
    t0 = time.time()
    lignes = [_appel(client, tag, user) for tag, user in lots]
    return round(time.time() - t0, 1), lignes


def _parallele(client, lots: list[tuple[str, str]], ecart: float) -> tuple[float, list[dict]]:
    t0 = time.time()

    def un(i: int, tag: str, user: str) -> dict:
        if ecart:
            time.sleep(i * ecart)
        return _appel(client, tag, user)

    lignes = []
    with ThreadPoolExecutor(max_workers=len(lots)) as pool:
        futurs = [pool.submit(un, i, tag, user) for i, (tag, user) in enumerate(lots)]
        for fut in as_completed(futurs):
            lignes.append(fut.result())
    lignes.sort(key=lambda r: r["tag"])
    return round(time.time() - t0, 1), lignes


def _afficher(mode: str, mur: float, lignes: list[dict]) -> None:
    print(f"\n== {mode} — mur {mur}s ==")
    for r in lignes:
        etat = "ok" if r["ok"] else "ERREUR"
        extra = "" if r["ok"] else f"  {r['erreur']}"
        print(f"  {r['tag']:12} {etat:6} {r['s']:7}s{extra}")
    taux = [r for r in lignes if "429" in r["erreur"] or "rate" in r["erreur"].lower()]
    if taux:
        print(f"  → {len(taux)} réponse(s) de limite de débit")


def main() -> None:
    p = argparse.ArgumentParser(description="Cartographie des pages : série vs parallèle espacé.")
    p.add_argument("pdf", type=Path)
    p.add_argument("--mode", choices=("parallele", "serie", "les-deux"), default="parallele")
    p.add_argument("--ecart", type=float, default=3.0, help="secondes entre le départ de chaque appel parallèle")
    p.add_argument("--lot", type=int, default=15)
    p.add_argument("--page-debut", type=int, default=161)
    p.add_argument("--page-fin", type=int, default=209)
    args = p.parse_args()

    ingest_config.charger_dotenv()
    cle = ingest_config.api_key()
    if not cle:
        sys.exit("Clé Mistral absente (MISTRAL_API_KEY_BEN).")

    reglages = Settings()
    reglages.api_key = cle
    reglages.offline = True
    tmp = Path("/tmp/bench_roles_pgcal")
    tmp.mkdir(parents=True, exist_ok=True)
    llm = MistralClient(reglages, tmp)
    doc, _ = ingest(args.pdf, reglages, llm)
    doc.pages = [page for page in doc.pages if args.page_debut <= page.number <= args.page_fin]
    doc.n_pages = len(doc.pages)
    if not doc.pages:
        sys.exit(f"Aucune page entre {args.page_debut} et {args.page_fin}.")
    infos, *_ = _heuristics(doc)
    lots = _lots(doc, infos, args.lot)
    print(
        f"pages {doc.pages[0].number}–{doc.pages[-1].number} "
        f"({doc.n_pages}) → {len(lots)} appels de {args.lot} pages"
    )
    print("Même quota que l'extraction en cours : ne pas lancer en même temps qu'un run pgcal.")

    try:
        from mistralai.client import Mistral
    except ImportError:
        from mistralai import Mistral
    client = Mistral(api_key=cle)

    if args.mode in ("serie", "les-deux"):
        print(f"série : {len(lots)} appel(s), un après l'autre", flush=True)
        mur, lignes = _serie(client, lots)
        _afficher("série", mur, lignes)
    if args.mode in ("parallele", "les-deux"):
        print(
            f"parallèle : {len(lots)} appel(s), départs espacés de {args.ecart}s",
            flush=True,
        )
        mur, lignes = _parallele(client, lots, args.ecart)
        _afficher(f"parallèle, départ espacé de {args.ecart}s", mur, lignes)


if __name__ == "__main__":
    main()
