#!/usr/bin/env bash
# Batterie complète, sans appel API. Usage (depuis le dossier parent de pdfvisuel/) :
#   ./pdfvisuel/lancer_tests.sh [dossier_des_plans_reels]
# Trois jeux : corpus régénéré (reportlab), corpus figé (autre producteur PDF, mêmes cas : les
# générateurs n'écrivent pas les tableaux de la même façon), plans réels annotés.
set -euo pipefail
REELS="${1:-data}"
export PYTHONPATH="$(pwd)${PYTHONPATH:+:$PYTHONPATH}"
python3 -m pdfvisuel.evaluation.generer_corpus corpus
echo "### Corpus régénéré + plans réels"
python3 -m pdfvisuel.evaluation.evaluer corpus/attendus/*.json pdfvisuel/evaluation/attendus_reels/*.json \
        --pdf-dir corpus --pdf-dir "$REELS" -o rapport_evaluation.md | sed -n '/## Global/,$p'
echo "### Corpus figé (autre producteur PDF)"
python3 -m pdfvisuel.evaluation.evaluer pdfvisuel/evaluation/corpus_fige/attendus/*.json \
        --pdf-dir pdfvisuel/evaluation/corpus_fige -o rapport_evaluation_fige.md | sed -n '/## Global/,$p'
for pdf in "$REELS"/*.pdf; do
  python3 -m pdfvisuel.tests.test_m2_simulation "$pdf"
done
python3 -m pdfvisuel.tests.test_m3_insertion
