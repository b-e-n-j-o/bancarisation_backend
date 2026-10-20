-- Sous-type métier d'un document (ex. arrêté initial, modificatif).
ALTER TABLE bancarisation.documents
  ADD COLUMN IF NOT EXISTS sous_type text;
