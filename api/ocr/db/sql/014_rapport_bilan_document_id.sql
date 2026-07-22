-- 014_rapport_bilan_document_id.sql
-- Garantit la colonne document_id (rattachement PDF bucket) si absente
-- d'une installation antérieure de 013.

ALTER TABLE bancarisation.rapport_bilan
  ADD COLUMN IF NOT EXISTS document_id uuid NULL;
