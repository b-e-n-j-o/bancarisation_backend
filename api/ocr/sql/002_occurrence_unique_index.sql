-- Index requis par ingestion_base_de_donnees (ON CONFLICT occurrences IA)
-- À exécuter dans le SQL Editor Supabase si l'ingestion échoue avec :
-- "there is no unique or exclusion constraint matching the ON CONFLICT specification"

CREATE UNIQUE INDEX IF NOT EXISTS occurrence_ia_echeance_annee_idx
    ON bancarisation.occurrence (echeance_id, annee)
    WHERE origine = 'ia' AND echeance_id IS NOT NULL;
