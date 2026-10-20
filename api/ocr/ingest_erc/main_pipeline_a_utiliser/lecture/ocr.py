"""Adaptateur texte des PDF.

- backend "pdftotext" : couche texte native pdfplumber (rapide, pas d'images)
- backend "mistral" / "hybride" : markdown Mistral OCR 4 pour la structure
                        + couche native pdfplumber pour scorer les chiffres
- backend "auto"      : texte natif d'abord, hybride si pages scannées
                        ou si le document ressemble à un plan / arrêté

Le markdown brut est mis en cache (clé = sha256 + modèle). La fusion hybride
est recalculée à la lecture, pour que mdnorm.py puisse évoluer sans re-OCR.
"""
from __future__ import annotations

import base64
import json
import re
import time
import unicodedata
from pathlib import Path

from .. import config
from .mdnorm import fiabilite_nombres, markdown_vers_texte
from ..modeles import PageTexte

SEUIL_PAGE_IMAGE = 40  # mots : en dessous, on considère la page comme scannée


def pdftotext_pages(pdf: str) -> list[PageTexte]:
    """Couche texte native via pdfplumber (MIT). Le nom historique est conservé."""
    import pdfplumber
    pages = []
    with pdfplumber.open(pdf) as doc:
        for i, page in enumerate(doc.pages, start=1):
            txt = page.extract_text(layout=True) or ""
            pages.append(PageTexte(num=i, texte=txt, nb_mots=len(txt.split())))
    return pages


def _nom_upload(filename: str) -> str:
    """Nom ASCII / minuscules pour l'API Files Mistral (évite 404 sur accents)."""
    stem = Path(filename).stem
    ascii_stem = (
        unicodedata.normalize("NFKD", stem)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", ascii_stem).strip("._-").casefold()
    return f"{ascii_stem or 'document'}.pdf"


def _pages_depuis_ocr(resp) -> list[PageTexte]:
    out = []
    for i, p in enumerate(resp.pages):
        md = p.markdown or ""
        num = getattr(p, "index", None)
        out.append(PageTexte(
            num=(num + 1 if num is not None else i + 1),
            texte=md, markdown=md, nb_mots=len(md.split()),
        ))
    return out


def _ocr_via_upload(client, pdf_bytes: bytes, upload_name: str, model: str):
    uploaded = client.files.upload(
        file={"file_name": upload_name, "content": pdf_bytes},
        purpose="ocr",
    )
    try:
        time.sleep(0.4)
        signed = client.files.get_signed_url(file_id=uploaded.id)
        return client.ocr.process(
            model=model,
            document={"type": "document_url", "document_url": signed.url},
            include_image_base64=False,
        )
    finally:
        try:
            client.files.delete(file_id=uploaded.id)
        except Exception:
            pass


def _ocr_via_base64(client, pdf_bytes: bytes, model: str):
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return client.ocr.process(
        model=model,
        document={"type": "document_url",
                  "document_url": f"data:application/pdf;base64,{b64}"},
        include_image_base64=False,
    )


def _limite_debit(msg: str) -> bool:
    t = msg.lower()
    return "429" in t or "rate limit" in t or "rate_limited" in t


def mistral_pages(pdf: str) -> list[PageTexte]:
    """Mistral OCR 4. Upload + URL signée (chemin qui marche dans ocr_mistral.py)."""
    try:
        from mistralai.client import Mistral  # SDK 2 / 3
    except ImportError:
        from mistralai import Mistral  # SDK 1

    cle = config.api_key()
    if not cle:
        raise RuntimeError(
            "OCR Mistral demandé mais aucune clé "
            "(MISTRAL_API_KEY ou MISTRAL_API_KEY_BEN)."
        )
    model = config.modele_ocr()
    pdf_bytes = Path(pdf).read_bytes()
    if not pdf_bytes:
        raise RuntimeError(f"PDF vide : {pdf}")

    client = Mistral(api_key=cle)
    upload_name = _nom_upload(Path(pdf).name)
    derniere: Exception | None = None

    for tentative in range(1, 5):
        try:
            print(f"📄 [OCR] {Path(pdf).name} → {model} (tentative {tentative}/4) …",
                  flush=True)
            resp = _ocr_via_upload(client, pdf_bytes, upload_name, model)
            print(f"   ✅ {len(resp.pages)} page(s)", flush=True)
            return _pages_depuis_ocr(resp)
        except Exception as err:  # noqa: BLE001
            derniere = err
            msg = str(err)
            print(f"   ⚠️  upload échoué : {msg[:220]}", flush=True)
            if tentative < 4 and ("404" in msg or "No file matches" in msg):
                time.sleep(1.0)
                continue
            if tentative < 4 and _limite_debit(msg):
                attente = 20 * tentative
                print(f"   ⏳ limite de débit, nouvel essai dans {attente}s", flush=True)
                time.sleep(attente)
                continue
            break

    if derniere and _limite_debit(str(derniere)):
        raise RuntimeError(
            f"OCR Mistral limité (429) après plusieurs attentes : {derniere}"
        ) from derniere

    print("   ↪️  repli OCR en base64…", flush=True)
    try:
        resp = _ocr_via_base64(client, pdf_bytes, model)
        print(f"   ✅ {len(resp.pages)} page(s) (base64)", flush=True)
        return _pages_depuis_ocr(resp)
    except Exception as err:  # noqa: BLE001
        raise RuntimeError(
            f"OCR Mistral échoué (upload puis base64) : {err}"
        ) from (derniere or err)


def pages_depuis_markdown(mds: list[str], natifs: list[PageTexte] | None = None) -> list[PageTexte]:
    """Fusionne la sortie Mistral (markdown) et la couche texte native page à page."""
    out = []
    for i, md in enumerate(mds, 1):
        nat = natifs[i - 1] if natifs and i <= len(natifs) else None
        out.append(PageTexte(
            num=i, texte=markdown_vers_texte(md or ""), markdown=md or None,
            nb_mots=nat.nb_mots if nat else len((md or "").split()),
            texte_natif=nat.texte if nat else None,
            fiabilite_ocr=fiabilite_nombres(md or "", nat.texte) if nat else None,
        ))
    return out


def pages_texte_brut(pages: list[PageTexte]) -> list[PageTexte]:
    """Normalise des pages déjà chargées (cache markdown brut)."""
    return pages_depuis_markdown([p.markdown or p.texte for p in pages], None)


def _besoin_ocr_mistral(pages: list[PageTexte]) -> bool:
    if not pages:
        return False
    scannees = sum(1 for p in pages if p.nb_mots < SEUIL_PAGE_IMAGE)
    if scannees >= max(1, 0.3 * len(pages)):
        return True
    tete = " ".join(p.texte for p in pages[:3]).lower()
    tete = unicodedata.normalize("NFKD", tete).encode("ascii", "ignore").decode()
    return any(m in tete for m in ("plan de gestion", "arrete", "derogation"))


def _lire_ou_ecrire(cache: Path, produire) -> list[PageTexte]:
    if cache.exists():
        return [PageTexte(**p) for p in json.loads(cache.read_text())]
    pages = produire()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([p.model_dump() for p in pages], ensure_ascii=False))
    return pages


def _mds_mistral(pdf: str, sha: str, cache_dir: Path) -> list[str]:
    slug = config.modele_ocr().replace("/", "_")
    pages = _lire_ou_ecrire(
        cache_dir / f"{sha}.mistral.{slug}.json",
        lambda: mistral_pages(pdf),
    )
    return [p.markdown or p.texte for p in pages]


def texte_pdf(pdf: str, sha: str, backend: str | None = None) -> list[PageTexte]:
    """backend : pdftotext | mistral | hybride (défaut si clé) | auto.

    hybride = markdown Mistral pour la structure + couche native pour
    vérifier les chiffres (les tableaux denses sont souvent mal relus par l'OCR).
    """
    config.charger_dotenv()
    backend = backend or config.ocr_backend()
    cache_dir = config.cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    if backend == "pdftotext":
        return _lire_ou_ecrire(cache_dir / f"{sha}.pdftotext.json",
                               lambda: pdftotext_pages(pdf))

    natifs = _lire_ou_ecrire(cache_dir / f"{sha}.pdftotext.json",
                             lambda: pdftotext_pages(pdf))

    if backend == "auto" and not (config.actif() and _besoin_ocr_mistral(natifs)):
        return natifs

    try:
        mds = _mds_mistral(pdf, sha, cache_dir)
    except RuntimeError as err:
        print(f"   ⚠️  OCR ignoré ({Path(pdf).name}) : {err}", flush=True)
        print("   → suite avec le texte natif du PDF, le dépôt continue", flush=True)
        return natifs
    return pages_depuis_markdown(mds, natifs)
