-- 016_prestataires_projet.sql
-- SIRET normalisé (dédup) + vue projet↔prestataires enrichie (contact + stats budget).

-- ---------------------------------------------------------------
-- 1. siret_norm : digits only, généré depuis siret affiché
-- ---------------------------------------------------------------

ALTER TABLE bancarisation.prestataires
  ADD COLUMN IF NOT EXISTS siret_norm text
  GENERATED ALWAYS AS (
    NULLIF(regexp_replace(coalesce(siret, ''), '[^0-9]', '', 'g'), '')
  ) STORED;

-- Unicité sur SIRET/SIREN normalisé (9 ou 14 chiffres typiques).
CREATE UNIQUE INDEX IF NOT EXISTS prestataires_siret_norm_uidx
  ON bancarisation.prestataires (siret_norm)
  WHERE siret_norm IS NOT NULL AND length(siret_norm) >= 9;

CREATE INDEX IF NOT EXISTS prestataires_siret_norm_idx
  ON bancarisation.prestataires (siret_norm)
  WHERE siret_norm IS NOT NULL;

-- ---------------------------------------------------------------
-- 2. Vue projet ↔ prestataires (occurrences ∪ liens explicites)
-- ---------------------------------------------------------------

DROP VIEW IF EXISTS bancarisation.v_projet_prestataires;

CREATE VIEW bancarisation.v_projet_prestataires AS
WITH depuis_occurrences AS (
  SELECT
    o.projet_id,
    o.prestataire_id,
    count(*) FILTER (WHERE o.statut <> 'supprime') AS nb_occurrences,
    count(*) FILTER (WHERE o.statut = 'realise') AS nb_realisees,
    coalesce(sum(o.montant_ht) FILTER (WHERE o.statut <> 'supprime'), 0) AS total_prevu_ht,
    coalesce(sum(o.montant_engage) FILTER (WHERE o.statut <> 'supprime'), 0) AS total_engage_ht,
    coalesce(sum(o.montant_realise) FILTER (WHERE o.statut <> 'supprime'), 0) AS total_realise_ht,
    min(o.annee) FILTER (WHERE o.statut <> 'supprime') AS annee_min,
    max(o.annee) FILTER (WHERE o.statut <> 'supprime') AS annee_max
  FROM bancarisation.occurrence o
  WHERE o.prestataire_id IS NOT NULL
  GROUP BY o.projet_id, o.prestataire_id
),
depuis_projet AS (
  SELECT
    pp.projet_id,
    pp.prestataire_id,
    pp.role,
    pp.created_at AS rattache_le
  FROM bancarisation.projet_prestataire pp
)
SELECT
  coalesce(occ.projet_id, prj.projet_id) AS projet_id,
  coalesce(occ.prestataire_id, prj.prestataire_id) AS prestataire_id,
  p.nom AS prestataire_nom,
  p.siret,
  p.siret_norm,
  p.forme_juridique,
  p.adresse,
  p.code_postal,
  p.commune,
  p.departement,
  p.email,
  p.telephone,
  p.interlocuteur,
  p.specialites,
  p.categories_mesure,
  p.actif,
  prj.role,
  prj.rattache_le,
  CASE
    WHEN occ.prestataire_id IS NOT NULL AND prj.prestataire_id IS NOT NULL THEN 'les_deux'
    WHEN occ.prestataire_id IS NOT NULL THEN 'occurrence'
    ELSE 'projet'
  END AS source,
  coalesce(occ.nb_occurrences, 0) AS nb_occurrences,
  coalesce(occ.nb_realisees, 0) AS nb_realisees,
  coalesce(occ.total_prevu_ht, 0) AS total_prevu_ht,
  coalesce(occ.total_engage_ht, 0) AS total_engage_ht,
  coalesce(occ.total_realise_ht, 0) AS total_realise_ht,
  occ.annee_min,
  occ.annee_max
FROM depuis_occurrences occ
FULL JOIN depuis_projet prj
  ON prj.projet_id = occ.projet_id
 AND prj.prestataire_id = occ.prestataire_id
JOIN bancarisation.prestataires p
  ON p.id = coalesce(occ.prestataire_id, prj.prestataire_id);
