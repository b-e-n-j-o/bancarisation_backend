# pdfvisuel

Lit dans un plan de gestion PDF l'information que l'OCR perd : mois surlignés, croix et bandes
positionnées sous des colonnes d'années, tableaux collés en image, pages scannées.

- **Module 1** (`m1_detection`, sans API, pdfplumber + pypdfium2) : lecture géométrique de la couche vectorielle du PDF,
  reconstruction des grilles temporelles cellule par cellule, classification des images, routage.
- **Module 2** (`m2_vision`, API Mistral) : OCR/vision uniquement sur les zones routées.
- **Module 3** (`m3_assemblage`, sans API) : texte enrichi + contrôles de cohérence.

```bash
pip install -r pdfvisuel/requirements.txt
python -m pdfvisuel.pipeline plan.pdf -o sortie/          # module 2 en dry-run par défaut
./pdfvisuel/lancer_tests.sh dossier_des_plans/            # évaluation complète, sans API
```

Lire ensuite `sortie/controles.md`, `sortie/grilles.md` et `sortie/overlays/`.

Documentation détaillée : [DOC_PIPELINE.md](DOC_PIPELINE.md).

Dépendances sous licences permissives uniquement (MIT, Apache/BSD) : voir la section 8 de la doc.
