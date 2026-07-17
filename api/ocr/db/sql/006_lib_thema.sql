-- Classification Théma (lib_thema) sur fiches-actions, échéances et occurrences.
-- Source de vérité : action_fiche (classée à l'extraction LLM), puis propagée.

ALTER TABLE bancarisation.action_fiche
    ADD COLUMN IF NOT EXISTS lib_thema text NOT NULL DEFAULT 'autre';

ALTER TABLE bancarisation.echeance
    ADD COLUMN IF NOT EXISTS lib_thema text NOT NULL DEFAULT 'autre';

ALTER TABLE bancarisation.occurrence
    ADD COLUMN IF NOT EXISTS lib_thema text NOT NULL DEFAULT 'autre';

-- Backfill depuis fiche_json si déjà présent (ré-import partiel)
UPDATE bancarisation.action_fiche
SET lib_thema = COALESCE(NULLIF(trim(fiche_json->>'lib_thema'), ''), 'autre')
WHERE lib_thema = 'autre'
  AND fiche_json ? 'lib_thema'
  AND NULLIF(trim(fiche_json->>'lib_thema'), '') IS NOT NULL;

-- Propager action → échéance (via action_cle)
UPDATE bancarisation.echeance e
SET lib_thema = a.lib_thema
FROM bancarisation.action_fiche a
WHERE a.projet_id = e.projet_id
  AND a.cle = e.action_cle
  AND a.lib_thema IS NOT NULL
  AND a.lib_thema <> 'autre'
  AND (e.lib_thema IS NULL OR e.lib_thema = 'autre');

-- Propager échéance → occurrence
UPDATE bancarisation.occurrence o
SET lib_thema = e.lib_thema
FROM bancarisation.echeance e
WHERE e.id = o.echeance_id
  AND e.lib_thema IS NOT NULL
  AND e.lib_thema <> 'autre'
  AND (o.lib_thema IS NULL OR o.lib_thema = 'autre');

-- CREATE OR REPLACE ne peut pas réordonner / renommer des colonnes existantes :
-- on conserve l'ordre d'origine et on ajoute lib_thema en fin de liste.
CREATE OR REPLACE VIEW bancarisation.v_occurrence_calendrier AS
SELECT
    o.id,
    o.projet_id,
    o.echeance_id,
    o.annee,
    o.code,
    o.titre,
    o.categorie,
    o.statut,
    o.ug_ids,
    o.mois_debut,
    o.mois_fin,
    o.traverse_nouvel_an,
    o.origine,
    o.confiance,
    o.champs_a_confirmer,
    o.avertissements,
    o.modifie_le,
    o.date_realisation,
    o.commentaire,
    e.cle AS echeance_cle,
    e.code_operation,
    e.libelle AS echeance_libelle,
    e.action_cle,
    COALESCE(
        NULLIF(NULLIF(o.lib_thema, ''), 'autre'),
        NULLIF(NULLIF(e.lib_thema, ''), 'autre'),
        NULLIF(af.lib_thema, ''),
        'autre'
    ) AS lib_thema
FROM bancarisation.occurrence o
LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id
LEFT JOIN bancarisation.action_fiche af
    ON af.projet_id = o.projet_id
   AND af.cle = e.action_cle;
