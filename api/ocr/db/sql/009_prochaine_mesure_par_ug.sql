-- 009_prochaine_mesure_par_ug.sql
-- Prochaine occurrence à échéance, ventilée par UG (ug_ids[]).
-- Aligné sur findNextMesure (frontend) et v_projet_liste_resume.

CREATE OR REPLACE VIEW bancarisation.v_prochaine_mesure_par_ug AS
WITH occ_expanded AS (
  SELECT
    o.projet_id,
    o.id AS occurrence_id,
    o.code,
    o.titre,
    o.statut,
    o.categorie,
    unnest(o.ug_ids) AS ug_id,
    COALESCE(o.mois_fin, o.mois_debut, 12)::int AS mois_fin_eff,
    CASE
      WHEN o.mois_debut IS NOT NULL
        AND o.mois_fin IS NOT NULL
        AND o.mois_debut > o.mois_fin
      THEN o.annee + 1
      ELSE o.annee
    END AS annee_fin,
    (
      make_date(
        CASE
          WHEN o.mois_debut IS NOT NULL
            AND o.mois_fin IS NOT NULL
            AND o.mois_debut > o.mois_fin
          THEN o.annee + 1
          ELSE o.annee
        END,
        COALESCE(o.mois_fin, o.mois_debut, 12)::int,
        1
      ) + interval '1 month' - interval '1 day'
    )::date AS deadline
  FROM bancarisation.occurrence o
  WHERE o.statut NOT IN ('realise', 'supprime')
    AND o.ug_ids IS NOT NULL
    AND cardinality(o.ug_ids) > 0
),
ranked AS (
  SELECT
    projet_id,
    ug_id,
    occurrence_id,
    code,
    titre,
    categorie,
    deadline,
    mois_fin_eff,
    annee_fin,
    (deadline - CURRENT_DATE)::int AS jours_restants,
    ROW_NUMBER() OVER (
      PARTITION BY projet_id, ug_id
      ORDER BY deadline ASC, code ASC
    ) AS rn
  FROM occ_expanded
  WHERE deadline >= CURRENT_DATE
    AND ug_id IS NOT NULL
    AND ug_id <> ''
)
SELECT
  projet_id,
  ug_id,
  occurrence_id,
  code AS prochaine_code,
  titre AS prochaine_titre,
  categorie AS prochaine_categorie,
  deadline AS prochaine_deadline,
  mois_fin_eff AS prochaine_mois_fin,
  annee_fin AS prochaine_annee_fin,
  jours_restants,
  CASE
    WHEN jours_restants <= 14 THEN 'urgent'
    WHEN jours_restants <= 45 THEN 'soon'
    ELSE 'ok'
  END AS urgence
FROM ranked
WHERE rn = 1;

COMMENT ON VIEW bancarisation.v_prochaine_mesure_par_ug IS
  'Prochaine mesure à échéance par UG (deadline = fin de mois de fenêtre).';

CREATE INDEX IF NOT EXISTS occurrence_ug_ids_gin_idx
  ON bancarisation.occurrence USING GIN (ug_ids);
