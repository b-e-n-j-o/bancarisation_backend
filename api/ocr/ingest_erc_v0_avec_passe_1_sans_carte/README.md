# ingest_erc — passe 1 (inventaire → référentiel à valider)

À lancer depuis ce dossier (`backend/api/ocr/ingest_erc/`). Le `.env` du backend
est chargé automatiquement (`MISTRAL_API_KEY_BEN`, à défaut `MISTRAL_API_KEY`).

```
python -m ingest_erc.passe1 --check                         # vérifie clé / OCR / LLM (gratuit)
python -m ingest_erc.passe1 sortie.json dossier/*           # étapes 0 → 2 (OCR 4 + GLM 5.2)
python -m ingest_erc.passe1 --model glm --effort high sortie.json dossier/*
python -m ingest_erc.audit_ocr                           # dump cache OCR + dernière passe 1
python -m ingest_erc.audit_ocr --dossier ingest_erc/dossier --sortie sortie.json
python -m ingest_erc.rendu_html sortie.json validation.html # écran BE (maquette)
```

Dépendances : pydantic 2, openpyxl, pyshp, shapely, pyproj, python-dotenv,
mistralai, poppler-utils (pdftotext, utile si `--ocr-backend pdftotext|auto`).

| Variable | Défaut | Rôle |
|---|---|---|
| `MISTRAL_API_KEY_BEN` (à défaut `MISTRAL_API_KEY`) | — | OCR 4 + LLM |
| `OCR_BACKEND` | `mistral` si clé, sinon `pdftotext` | `mistral` / `pdftotext` / `auto` |
| `MISTRAL_OCR_MODEL` | `mistral-ocr-latest` (OCR 4) | épingler avec `mistral-ocr-4-0` |
| `MISTRAL_MODEL` | `glm` → `zai-glm-5-2` | alias `small` / `medium` / `large` / `glm` ou id API |
| `MISTRAL_REASONING_EFFORT` | `high` | GLM : `none`…`xhigh` ; Mistral : `none`/`low`/`medium`/`high` |
| `INGEST_CACHE` | `/tmp/ingest_cache` | cache OCR par sha256 |

| Module | Étape | Nature |
|---|---|---|
| `inventaire.py` | 0 — hash, rôle, pages scannées, annexes embarquées, doublons | déterministe |
| `ocr.py` | texte des PDF (pdftotext / Mistral OCR 4), cache par sha256 | adaptateur |
| `sig_profil.py` | 1a — noms de fichiers réparés, CRS → 2154, profil des colonnes, zones candidates | déterministe |
| `excel_profil.py` | 1b — grille (fusions dépliées, couleurs, formules), blocs temporels, synthèses, légendes, coefficient des totaux | déterministe |
| `pdf_sections.py` | 1b — plan du PDF, familles de codes apprises, score, sélection sous budget | déterministe (+ routeur LLM si ambigu) |
| `extract_referentiel.py` | 1b — faits sourcés (tableur, plan, arrêté) | déterministe (+ LLM si clé) |
| `llm.py` | appels Mistral : routeur de sections, extraction du référentiel | LLM (GLM 5.2 + reasoning) |
| `reconcile_ref.py` | 2 — tables projet / UG / actions, confiance, questions, contrôles | déterministe |
| `rendu_html.py` | 2.5 — écran de validation BE (maquette) | — |

Règle de confiance : haute = 2 sources indépendantes concordent ; moyenne = 1 source ou
inférence cohérente ; basse = conflit / cellule fusionnée / coquille. Toute inférence ou
conflit produit une question. Les réponses repartent dans le pipeline en `origine='user'`.

Limites v0 connues :
- sans LLM, le tableau de synthèse du PDF (Tabl. 5) n'est pas lu (mise en page cassée par
  pdftotext) : les UG de SE/MG ne viennent que du tableur → question. Avec Mistral OCR 4
  (tableaux markdown) + llm.extraire_referentiel, SE2 = UG6, UG7 devient un conflit détecté.
- les annexes scannées (arrêté défrichement) nécessitent Mistral OCR 4.
- « champs GéoMCE socle » = liste indicative, à aligner sur la notice gabarit v2.2.
