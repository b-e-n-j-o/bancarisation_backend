-- 020_projet_liste_resume.sql
-- Résumé liste projets : compteurs + prochaine mesure à échéance.
-- Évite le N×GET /occurrences côté frontend (latence visible dès ~10 projets).
--
-- Logique alignée sur findNextMesure (frontend) :
--   deadline = dernier jour du mois de fin de fenêtre (mois_fin / mois_debut),
--   année + 1 si la fenêtre traverse le nouvel an (mois_debut > mois_fin).

CREATE OR REPLACE VIEW bancarisation.v_projet_liste_resume AS
WITH occ_deadline AS (
  SELECT
    o.projet_id,
    o.id,
    o.code,
    o.titre,
    o.statut,
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
),
next_occ AS (
  SELECT DISTINCT ON (projet_id)
    projet_id,
    code,
    titre,
    deadline,
    mois_fin_eff,
    annee_fin
  FROM occ_deadline
  WHERE deadline >= CURRENT_DATE
  ORDER BY projet_id, deadline ASC, code ASC
),
counts AS (
  SELECT
    projet_id,
    count(*) FILTER (WHERE statut <> 'supprime') AS nb_occurrences,
    count(*) FILTER (WHERE statut = 'realise') AS nb_realisees
  FROM bancarisation.occurrence
  GROUP BY projet_id
)
SELECT
  p.id,
  p.organisation_id,
  p.nom,
  p.reference_interne,
  p.commune,
  p.departement,
  p.description,
  p.type_procedure,
  p.date_decision,
  p.duree_annees,
  p.date_fin,
  p.statut,
  p.created_at,
  p.updated_at,
  coalesce(c.nb_occurrences, 0)::int AS nb_occurrences,
  coalesce(c.nb_realisees, 0)::int AS nb_realisees,
  0::numeric AS dette_m2,
  n.code AS prochaine_code,
  n.titre AS prochaine_titre,
  n.deadline AS prochaine_deadline,
  CASE
    WHEN n.deadline IS NULL THEN NULL
    ELSE (n.deadline - CURRENT_DATE)::int
  END AS prochaine_jours_restants,
  n.mois_fin_eff AS prochaine_mois_fin,
  n.annee_fin AS prochaine_annee_fin
FROM bancarisation.projets p
LEFT JOIN counts c ON c.projet_id = p.id
LEFT JOIN next_occ n ON n.projet_id = p.id;

COMMENT ON VIEW bancarisation.v_projet_liste_resume IS
  'Liste projets enrichie : réalisées / total + prochaine occurrence (J-x).';
