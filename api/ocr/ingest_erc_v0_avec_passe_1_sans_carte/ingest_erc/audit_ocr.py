"""Audit OCR Mistral (cache) + faits de la passe 1.

Sans nouvel appel API : lit `{sha}.mistral.{modele}.json` dans INGEST_CACHE
et la dernière `sortie.json` de job.

```
cd backend/api/ocr/ingest_erc
python -m ingest_erc.audit_ocr
python -m ingest_erc.audit_ocr --dossier ingest_erc/dossier --sortie sortie.json
```

Produit `audit_ocr/` :
  INDEX.md                         — mode d'emploi + stats pages cibles
  plan/  arrete/                   — full.md, pages.json, page_XX.md (cibles)
  passe1/sortie.json + faits.md    — extraits de la passe 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

from . import config

WORK_ROOT = Path(__file__).resolve().parents[2] / "work" / "ingest_erc"


def est_bruit_macos(nom: str) -> bool:
    n = Path(nom).name
    return n.startswith(".") or n.startswith("._") or n in {"Thumbs.db", "desktop.ini"}

# Pages 1-indexées demandées pour caler regex / sections.
CIBLES = {
    "plan": [
        ("p01-05_titre_tdm", range(1, 6)),
        ("p37-39_familles_tabl5", range(37, 40)),
        ("p40-41_fiche_TU1", range(40, 42)),
        ("p63_fiche_TE1", [63]),
        ("p80_etat_zero", [80]),
        ("p82-89_annexe_defrichement", range(82, 90)),
        ("p100-101_annee_N", range(100, 102)),
    ],
    "arrete": [
        ("p01_libelle", [1]),
        ("p03_beneficiaire", [3]),
        ("p13-14_obligations_MC", range(13, 15)),
        ("p16_a_compter_2022", [16]),
    ],
}

REPERES = [
    "plan de gestion", "unité de gestion", "tableau 5", "tabl. 5",
    "travaux uniques", "(TU)", "(TE)", "TU1", "TE1", "SE1", "SE2", "MG1",
    "état zéro", "annee n", "année n", "à compter de 2022",
    "défrichement", "bénéficiaire", "MC1", "MC3",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_ocr(sha: str) -> Path | None:
    d = config.cache_dir()
    slug = config.modele_ocr().replace("/", "_")
    p = d / f"{sha}.mistral.{slug}.json"
    return p if p.exists() else None


def pages_par_num(brut: list[dict]) -> dict[int, dict]:
    return {int(p["num"]): p for p in brut}


def ecrire_pages(dest: Path, nom: str, brut: list[dict], cibles: list) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    par = pages_par_num(brut)
    (dest / "pages.json").write_text(json.dumps(brut, ensure_ascii=False, indent=1), encoding="utf-8")
    full = []
    for p in sorted(par):
        md = par[p].get("texte") or ""
        full.append(f"\n\n<!-- ===== PAGE {p} ===== -->\n\n{md}")
    (dest / "full.md").write_text("".join(full), encoding="utf-8")

    lignes = [f"## {nom}", "", f"{len(par)} page(s) OCR · cache `{nom}`", ""]
    for label, nums in cibles:
        sous = dest / label
        sous.mkdir(exist_ok=True)
        lignes.append(f"### {label}")
        for n in nums:
            p = par.get(n)
            if not p:
                lignes.append(f"- p.{n} **absente** du cache")
                continue
            md = p.get("texte") or ""
            (sous / f"page_{n:02d}.md").write_text(md, encoding="utf-8")
            apercu = " ".join(md.split())[:220]
            lignes.append(
                f"- p.{n} · {p.get('nb_mots', len(md.split()))} mots"
                + (f" · `{apercu}…`" if apercu else " · *(vide)*")
            )
        lignes.append("")
    return lignes


def classer_pdf(nom: str, nb: int) -> str:
    n = nom.lower()
    if "plan" in n and "gestion" in n:
        return "plan"
    if nb and nb > 40:
        return "plan"
    return "arrete"


def dernier_job() -> Path | None:
    if not WORK_ROOT.exists():
        return None
    jobs = [p for p in WORK_ROOT.iterdir() if (p / "sortie.json").exists()]
    if not jobs:
        return None
    return max(jobs, key=lambda p: (p / "sortie.json").stat().st_mtime)


def faits_md(faits: list[dict]) -> str:
    par_type: dict[str, list] = defaultdict(list)
    for f in faits:
        par_type[f.get("type", "?")].append(f)
    out = ["# Faits extraits (passe 1)", ""]
    out.append(f"{len(faits)} fait(s) · {sum(1 for f in faits if f.get('methode')=='llm')} LLM")
    out.append("")
    for typ in sorted(par_type):
        items = par_type[typ]
        out.append(f"## {typ} ({len(items)})")
        for f in items[:40]:
            src = f.get("source") or {}
            val = f.get("valeur")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)[:180]
            else:
                val = str(val)[:180]
            out.append(
                f"- `{f.get('cle')}` · {src.get('doc','')} {src.get('loc','')} "
                f"· {f.get('methode')} · {val}"
            )
        if len(items) > 40:
            out.append(f"- … {len(items) - 40} de plus")
        out.append("")
    return "\n".join(out)


def reperes_md(docs: dict[str, list[dict]]) -> str:
    lignes = ["# Repères dans l'OCR (pages cibles)", ""]
    for role, brut in docs.items():
        par = pages_par_num(brut)
        nums = []
        for _, r in CIBLES.get(role, []):
            nums.extend(list(r))
        lignes.append(f"## {role}")
        for motif in REPERES:
            hits = []
            for n in nums:
                txt = (par.get(n) or {}).get("texte") or ""
                if motif.lower() in txt.lower():
                    hits.append(str(n))
            marque = ", ".join(hits) if hits else "—"
            lignes.append(f"- `{motif}` → p.{marque}")
        lignes.append("")
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Dump audit OCR Mistral (cache) + faits passe 1.")
    p.add_argument("--dossier", type=Path, default=None,
                   help="Dossier de PDF (sinon dernier job ingest_erc).")
    p.add_argument("--sortie", type=Path, default=None,
                   help="sortie.json de passe 1 (sinon celle du dernier job).")
    p.add_argument("--out", type=Path, default=None, help="Répertoire d'audit.")
    args = p.parse_args(argv)

    config.charger_dotenv()
    job = dernier_job()
    dossier = args.dossier
    if dossier is None and job:
        dossier = job / "dossier"
    if dossier is None or not dossier.is_dir():
        raise SystemExit("Aucun dossier PDF. Passe --dossier ou lance d'abord une passe 1.")

    out = args.out or Path(__file__).resolve().parents[1] / "audit_ocr"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    index = ["# Audit OCR Mistral (Le Barp)", "",
             "Sources : cache `/tmp/ingest_cache` (aucun nouvel appel API).", ""]
    dumps: dict[str, list[dict]] = {}

    for pdf in sorted(dossier.iterdir()):
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf" or est_bruit_macos(pdf.name):
            continue
        sha = sha256(pdf)
        cache = cache_ocr(sha)
        if not cache:
            index.append(f"- **{pdf.name}** : pas de cache OCR (`{sha[:12]}…`). Relancer une passe 1.")
            continue
        brut = json.loads(cache.read_text(encoding="utf-8"))
        role = classer_pdf(pdf.name, len(brut))
        dumps[role] = brut
        dest = out / role
        dest.mkdir(exist_ok=True)
        (dest / "source.txt").write_text(f"{pdf.name}\n{sha}\n{cache}\n", encoding="utf-8")
        shutil.copy(cache, dest / cache.name)
        index.extend(ecrire_pages(dest, pdf.name, brut, CIBLES.get(role, [])))
        index.append(f"Cache : `{cache.name}`")
        index.append("")

    (out / "reperes.md").write_text(reperes_md(dumps), encoding="utf-8")

    sortie = args.sortie
    if sortie is None and job:
        sortie = job / "sortie.json"
    if sortie and sortie.exists():
        dest = out / "passe1"
        dest.mkdir(exist_ok=True)
        shutil.copy(sortie, dest / "sortie.json")
        data = json.loads(sortie.read_text(encoding="utf-8"))
        (dest / "faits.md").write_text(faits_md(data.get("faits") or []), encoding="utf-8")
        (dest / "documents.json").write_text(
            json.dumps(data.get("documents"), ensure_ascii=False, indent=1), encoding="utf-8")
        (dest / "stats.json").write_text(
            json.dumps({"config": data.get("config"), "durees_s": data.get("durees_s"),
                        "stats": (data.get("referentiel") or {}).get("stats"),
                        "nb_faits": len(data.get("faits") or [])},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        index.append("## Passe 1")
        index.append(f"- copiée depuis `{sortie}`")
        index.append(f"- {len(data.get('faits') or [])} faits → `passe1/faits.md`")
        index.append("")

    (out / "INDEX.md").write_text("\n".join(index), encoding="utf-8")
    print(f"✅ Audit écrit dans {out}")
    print(f"   → ouvre {out / 'INDEX.md'}")


if __name__ == "__main__":
    main()
