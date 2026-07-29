-- Partage volontaire du budget projet avec le régulateur DREAL.
-- Défaut false : les montants ne sont pas exposés au parc / dossier de contrôle.

ALTER TABLE bancarisation.projets
  ADD COLUMN IF NOT EXISTS partager_budget_dreal boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN bancarisation.projets.partager_budget_dreal IS
  'Si true, le budget du projet est visible en lecture côté suivi DREAL. Défaut false.';

-- CREATE OR REPLACE ne peut pas réordonner / renommer des colonnes :
-- la nouvelle colonne doit être ajoutée EN FIN de liste.
CREATE OR REPLACE VIEW bancarisation.v_parc_projet AS
WITH sig AS (
  SELECT
    projet_id,
    count(*) FILTER (WHERE niveau = 'critique') AS nb_critique,
    count(*) FILTER (WHERE niveau = 'attention') AS nb_attention,
    jsonb_agg(
      jsonb_build_object(
        'code', code, 'niveau', niveau,
        'libelle', libelle, 'valeur', valeur, 'detail', detail
      ) ORDER BY (niveau = 'critique') DESC, code
    ) AS signaux
  FROM bancarisation.v_parc_signal
  GROUP BY projet_id
),
fin AS (
  SELECT
    projet_id,
    sum(initial) AS total_initial,
    sum(prevu)   AS total_prevu,
    sum(engage)  AS total_engage,
    sum(realise) AS total_realise,
    sum(prevu) FILTER (WHERE annee = EXTRACT(YEAR FROM now())::int)   AS prevu_annee,
    sum(realise) FILTER (WHERE annee = EXTRACT(YEAR FROM now())::int) AS realise_annee,
    min(annee) AS premiere_annee,
    max(annee) AS derniere_annee
  FROM bancarisation.v_budget_delta_annuel
  GROUP BY projet_id
),
occ AS (
  SELECT
    projet_id,
    count(*) AS nb_occurrences,
    count(*) FILTER (WHERE statut = 'realise') AS nb_realisees,
    count(*) FILTER (WHERE statut = 'repousse') AS nb_reportees
  FROM bancarisation.occurrence
  WHERE statut <> 'supprime'
  GROUP BY projet_id
),
bil AS (
  SELECT
    projet_id,
    count(*) FILTER (WHERE etat = 'manquant') AS nb_bilans_manquants,
    count(*) FILTER (WHERE etat = 'valide')   AS nb_bilans_valides,
    max(annee) FILTER (WHERE etat = 'valide') AS dernier_bilan_valide
  FROM bancarisation.v_parc_bilan_matrice
  GROUP BY projet_id
)
SELECT
  p.id AS projet_id,
  p.nom,
  p.reference_interne,
  p.organisation_id,
  org.nom AS organisation_nom,
  p.commune,
  p.departement,
  p.type_procedure,
  p.statut,
  p.date_decision,
  p.duree_annees,
  p.date_fin,
  CASE
    WHEN coalesce(sig.nb_critique, 0) > 0 THEN 2
    WHEN coalesce(sig.nb_attention, 0) > 0 THEN 1
    ELSE 0
  END AS gravite,
  coalesce(sig.nb_critique, 0)  AS nb_signaux_critiques,
  coalesce(sig.nb_attention, 0) AS nb_signaux_attention,
  coalesce(sig.signaux, '[]'::jsonb) AS signaux,
  CASE WHEN p.partager_budget_dreal THEN coalesce(fin.total_initial, 0) ELSE 0 END AS total_initial,
  CASE WHEN p.partager_budget_dreal THEN coalesce(fin.total_prevu, 0)   ELSE 0 END AS total_prevu,
  CASE WHEN p.partager_budget_dreal THEN coalesce(fin.total_engage, 0)  ELSE 0 END AS total_engage,
  CASE WHEN p.partager_budget_dreal THEN coalesce(fin.total_realise, 0) ELSE 0 END AS total_realise,
  CASE
    WHEN p.partager_budget_dreal
    THEN coalesce(fin.total_prevu, 0) - coalesce(fin.total_initial, 0)
    ELSE 0
  END AS delta_total,
  CASE WHEN p.partager_budget_dreal THEN coalesce(fin.prevu_annee, 0)   ELSE 0 END AS prevu_annee_courante,
  CASE WHEN p.partager_budget_dreal THEN coalesce(fin.realise_annee, 0) ELSE 0 END AS realise_annee_courante,
  CASE WHEN p.partager_budget_dreal THEN fin.premiere_annee ELSE NULL END AS premiere_annee,
  CASE WHEN p.partager_budget_dreal THEN fin.derniere_annee ELSE NULL END AS derniere_annee,
  coalesce(occ.nb_occurrences, 0) AS nb_occurrences,
  coalesce(occ.nb_realisees, 0)   AS nb_occurrences_realisees,
  coalesce(occ.nb_reportees, 0)   AS nb_occurrences_reportees,
  coalesce(bil.nb_bilans_valides, 0)   AS nb_bilans_valides,
  coalesce(bil.nb_bilans_manquants, 0) AS nb_bilans_manquants,
  bil.dernier_bilan_valide,
  p.partager_budget_dreal
FROM bancarisation.projets p
JOIN bancarisation.organisations org ON org.id = p.organisation_id
LEFT JOIN sig ON sig.projet_id = p.id
LEFT JOIN fin ON fin.projet_id = p.id
LEFT JOIN occ ON occ.projet_id = p.id
LEFT JOIN bil ON bil.projet_id = p.id;
