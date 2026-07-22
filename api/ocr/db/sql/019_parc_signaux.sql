-- 019_parc_signaux.sql
-- Moteur de signaux et agrégats du parc (vue de contrôle DREAL).
--
-- Principe : AUCUN score composite opaque. Chaque signal est une règle
-- explicable, produisant une ligne (projet, code, niveau, libelle, detail).
-- Le libellé est rédigé pour pouvoir être repris tel quel dans un courrier.
--
-- Les seuils viennent de bancarisation.parametre_signal (migration 018).

-- ═══════════════════════════════════════════════════════════════
-- 1. Années d'obligation de bilan
-- ═══════════════════════════════════════════════════════════════
-- Un projet doit un bilan pour chaque année civile écoulée entre l'année
-- de sa décision et la fin de ses obligations. L'année en cours n'est
-- jamais due (son bilan ne peut être clos).

CREATE OR REPLACE VIEW bancarisation.v_projet_annee_obligation AS
SELECT
  p.id AS projet_id,
  p.organisation_id,
  gs.annee::int AS annee
FROM bancarisation.projets p
CROSS JOIN LATERAL generate_series(
  EXTRACT(YEAR FROM p.date_decision)::int,
  LEAST(
    EXTRACT(YEAR FROM COALESCE(p.date_fin, now()))::int,
    EXTRACT(YEAR FROM now())::int - 1
  )
) AS gs(annee)
WHERE p.date_decision IS NOT NULL
  AND p.statut <> 'archive';

-- ═══════════════════════════════════════════════════════════════
-- 2. Matrice des bilans : une case par (projet, année) sur toute la
--    fenêtre du projet, avec l'état documentaire.
-- ═══════════════════════════════════════════════════════════════
-- etat : 'valide' | 'brouillon' | 'manquant' | 'a_venir'

CREATE OR REPLACE VIEW bancarisation.v_parc_bilan_matrice AS
WITH fenetre AS (
  SELECT
    p.id AS projet_id,
    p.organisation_id,
    gs.annee::int AS annee,
    EXTRACT(YEAR FROM now())::int AS annee_courante
  FROM bancarisation.projets p
  CROSS JOIN LATERAL generate_series(
    EXTRACT(YEAR FROM p.date_decision)::int,
    EXTRACT(YEAR FROM COALESCE(p.date_fin, now() + interval '1 year'))::int
  ) AS gs(annee)
  WHERE p.date_decision IS NOT NULL
),
meilleur AS (
  SELECT DISTINCT ON (rb.projet_id, rb.annee)
    rb.projet_id, rb.annee, rb.id, rb.statut, rb.version, rb.valide_le
  FROM bancarisation.rapport_bilan rb
  ORDER BY rb.projet_id, rb.annee,
           (rb.statut = 'valide') DESC, rb.version DESC
)
SELECT
  f.projet_id,
  f.organisation_id,
  f.annee,
  COALESCE(
    m.statut,
    CASE WHEN f.annee < f.annee_courante THEN 'manquant' ELSE 'a_venir' END
  ) AS etat,
  m.id AS rapport_id,
  m.version,
  m.valide_le
FROM fenetre f
LEFT JOIN meilleur m ON m.projet_id = f.projet_id AND m.annee = f.annee;

-- ═══════════════════════════════════════════════════════════════
-- 3. Signaux
-- ═══════════════════════════════════════════════════════════════
-- Colonnes communes : projet_id, code, niveau, libelle, valeur, detail
-- niveau ∈ ('critique', 'attention')

CREATE OR REPLACE VIEW bancarisation.v_parc_signal AS

-- 3.1 Bilan annuel non remis --------------------------------------
WITH sig_bilan AS (
  SELECT
    o.projet_id,
    'bilan_manquant' AS code,
    CASE WHEN count(*) >= bancarisation.seuil('bilan_manquant_critique')
         THEN 'critique' ELSE 'attention' END AS niveau,
    format(
      '%s bilan(s) annuel(s) non remis : %s.',
      count(*), string_agg(o.annee::text, ', ' ORDER BY o.annee)
    ) AS libelle,
    count(*)::numeric AS valeur,
    jsonb_build_object('annees', jsonb_agg(o.annee ORDER BY o.annee)) AS detail
  FROM bancarisation.v_projet_annee_obligation o
  WHERE NOT EXISTS (
    SELECT 1 FROM bancarisation.rapport_bilan rb
    WHERE rb.projet_id = o.projet_id
      AND rb.annee = o.annee
      AND rb.statut = 'valide'
  )
  GROUP BY o.projet_id
),

-- 3.2 Retard d'exécution -------------------------------------------
sig_retard AS (
  SELECT
    oc.projet_id,
    'retard_execution' AS code,
    CASE WHEN max(EXTRACT(YEAR FROM now())::int - oc.annee)
              >= bancarisation.seuil('retard_critique_ans')
         THEN 'critique' ELSE 'attention' END AS niveau,
    format(
      '%s action(s) d''exercices échus non soldées, la plus ancienne de %s.',
      count(*), min(oc.annee)
    ) AS libelle,
    count(*)::numeric AS valeur,
    jsonb_build_object(
      'annee_min', min(oc.annee),
      'montant_concerne', coalesce(sum(oc.montant_ht), 0),
      'occurrences', jsonb_agg(oc.id ORDER BY oc.annee)
    ) AS detail
  FROM bancarisation.occurrence oc
  WHERE oc.statut IN ('planifie', 'en_cours', 'a_confirmer')
    AND oc.annee < EXTRACT(YEAR FROM now())::int
  GROUP BY oc.projet_id
),

-- 3.3 Sous-consommation sur exercices échus ------------------------
-- Un budget non consommé signale des mesures potentiellement non réalisées.
sig_sous_conso AS (
  SELECT
    d.projet_id,
    'sous_consommation' AS code,
    CASE WHEN sum(d.realise) / nullif(sum(d.prevu), 0)
              < bancarisation.seuil('sous_conso_critique')
         THEN 'critique' ELSE 'attention' END AS niveau,
    format(
      'Seuls %s%% du budget prévu des exercices échus ont été facturés (%s sur %s).',
      round(100 * sum(d.realise) / nullif(sum(d.prevu), 0)),
      round(sum(d.realise)), round(sum(d.prevu))
    ) AS libelle,
    round(sum(d.realise) / nullif(sum(d.prevu), 0), 4) AS valeur,
    jsonb_build_object(
      'prevu_echu', sum(d.prevu),
      'realise_echu', sum(d.realise),
      'non_consomme', sum(d.prevu) - sum(d.realise)
    ) AS detail
  FROM bancarisation.v_budget_delta_annuel d
  WHERE d.annee < EXTRACT(YEAR FROM now())::int
  GROUP BY d.projet_id
  HAVING sum(d.prevu) > 0
     AND sum(d.realise) / sum(d.prevu) < bancarisation.seuil('sous_conso_seuil')
),

-- 3.4 Dérive budgétaire non justifiée -------------------------------
-- Un écart important ET muet. Un écart motivé n'est pas signalé.
derive AS (
  SELECT
    oc.projet_id,
    sum(oc.montant_initial) AS initial,
    sum(oc.montant_ht) AS prevu,
    sum(oc.montant_ht) - sum(oc.montant_initial) AS ecart,
    count(*) FILTER (
      WHERE abs(coalesce(oc.montant_ht, 0) - oc.montant_initial) >= 1
        AND NOT EXISTS (
          SELECT 1 FROM bancarisation.budget_mouvement m
          WHERE m.occurrence_id = oc.id AND m.motif IS NOT NULL
        )
    ) AS nb_sans_motif
  FROM bancarisation.occurrence oc
  WHERE oc.montant_initial IS NOT NULL
    AND oc.statut <> 'supprime'
  GROUP BY oc.projet_id
),
sig_derive AS (
  SELECT
    d.projet_id,
    'derive_non_justifiee' AS code,
    CASE WHEN abs(d.ecart) / nullif(d.initial, 0)
              >= bancarisation.seuil('derive_critique_pct')
         THEN 'critique' ELSE 'attention' END AS niveau,
    format(
      'Écart de %s%% au budget de référence (%s), dont %s ligne(s) sans motif renseigné.',
      round(100 * d.ecart / nullif(d.initial, 0)),
      round(d.ecart),
      d.nb_sans_motif
    ) AS libelle,
    round(abs(d.ecart) / nullif(d.initial, 0), 4) AS valeur,
    jsonb_build_object(
      'initial', d.initial, 'prevu', d.prevu, 'ecart', d.ecart,
      'lignes_sans_motif', d.nb_sans_motif
    ) AS detail
  FROM derive d
  WHERE d.initial > 0
    AND abs(d.ecart) / d.initial >= bancarisation.seuil('derive_seuil_pct')
    AND d.nb_sans_motif > 0
),

-- 3.5 Silence : dossier sans activité -------------------------------
activite AS (
  SELECT
    p.id AS projet_id,
    GREATEST(
      COALESCE((SELECT max(m.modifie_le) FROM bancarisation.budget_mouvement m
                WHERE m.projet_id = p.id), p.created_at),
      COALESCE((SELECT max(rb.genere_le) FROM bancarisation.rapport_bilan rb
                WHERE rb.projet_id = p.id), p.created_at),
      p.updated_at
    ) AS derniere_activite
  FROM bancarisation.projets p
  WHERE p.statut NOT IN ('archive', 'clos')
),
sig_silence AS (
  SELECT
    a.projet_id,
    'silence' AS code,
    CASE WHEN a.derniere_activite
              < now() - make_interval(months => bancarisation.seuil('silence_critique_mois')::int)
         THEN 'critique' ELSE 'attention' END AS niveau,
    format(
      'Aucune activité enregistrée depuis %s mois (dernière le %s).',
      floor(EXTRACT(EPOCH FROM (now() - a.derniere_activite)) / 2592000)::int,
      to_char(a.derniere_activite, 'DD/MM/YYYY')
    ) AS libelle,
    round(EXTRACT(EPOCH FROM (now() - a.derniere_activite)) / 2592000) AS valeur,
    jsonb_build_object('derniere_activite', a.derniere_activite) AS detail
  FROM activite a
  WHERE a.derniere_activite
        < now() - make_interval(months => bancarisation.seuil('silence_mois')::int)
)

SELECT * FROM sig_bilan
UNION ALL SELECT * FROM sig_retard
UNION ALL SELECT * FROM sig_sous_conso
UNION ALL SELECT * FROM sig_derive
UNION ALL SELECT * FROM sig_silence;

-- ═══════════════════════════════════════════════════════════════
-- 4. Ligne de parc : un projet = une ligne, avec sa gravité et ses
--    chiffres clés. C'est la vue que consomme le tableau principal.
-- ═══════════════════════════════════════════════════════════════

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
  -- Gravité : ordonne la file de contrôle. Dérivée des signaux, jamais
  -- un score arbitraire : 2 = au moins un critique, 1 = attention, 0 = RAS.
  CASE
    WHEN coalesce(sig.nb_critique, 0) > 0 THEN 2
    WHEN coalesce(sig.nb_attention, 0) > 0 THEN 1
    ELSE 0
  END AS gravite,
  coalesce(sig.nb_critique, 0)  AS nb_signaux_critiques,
  coalesce(sig.nb_attention, 0) AS nb_signaux_attention,
  coalesce(sig.signaux, '[]'::jsonb) AS signaux,
  coalesce(fin.total_initial, 0) AS total_initial,
  coalesce(fin.total_prevu, 0)   AS total_prevu,
  coalesce(fin.total_engage, 0)  AS total_engage,
  coalesce(fin.total_realise, 0) AS total_realise,
  coalesce(fin.total_prevu, 0) - coalesce(fin.total_initial, 0) AS delta_total,
  coalesce(fin.prevu_annee, 0)   AS prevu_annee_courante,
  coalesce(fin.realise_annee, 0) AS realise_annee_courante,
  fin.premiere_annee,
  fin.derniere_annee,
  coalesce(occ.nb_occurrences, 0) AS nb_occurrences,
  coalesce(occ.nb_realisees, 0)   AS nb_occurrences_realisees,
  coalesce(occ.nb_reportees, 0)   AS nb_occurrences_reportees,
  coalesce(bil.nb_bilans_valides, 0)   AS nb_bilans_valides,
  coalesce(bil.nb_bilans_manquants, 0) AS nb_bilans_manquants,
  bil.dernier_bilan_valide
FROM bancarisation.projets p
JOIN bancarisation.organisations org ON org.id = p.organisation_id
LEFT JOIN sig ON sig.projet_id = p.id
LEFT JOIN fin ON fin.projet_id = p.id
LEFT JOIN occ ON occ.projet_id = p.id
LEFT JOIN bil ON bil.projet_id = p.id;

-- ═══════════════════════════════════════════════════════════════
-- 5. Agrégat par organisation (+ base de la comparaison à la médiane)
-- ═══════════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW bancarisation.v_parc_organisation AS
SELECT
  o.id AS organisation_id,
  o.nom AS organisation_nom,
  count(v.projet_id) AS nb_projets,
  count(*) FILTER (WHERE v.gravite = 2) AS nb_projets_critiques,
  count(*) FILTER (WHERE v.gravite = 1) AS nb_projets_attention,
  count(*) FILTER (WHERE v.gravite = 0) AS nb_projets_conformes,
  coalesce(sum(v.total_prevu), 0)   AS total_prevu,
  coalesce(sum(v.total_realise), 0) AS total_realise,
  coalesce(sum(v.total_initial), 0) AS total_initial,
  coalesce(sum(v.delta_total), 0)   AS delta_total,
  sum(v.nb_bilans_valides)   AS nb_bilans_valides,
  sum(v.nb_bilans_manquants) AS nb_bilans_manquants,
  -- Taux de remise des bilans : indicateur de rigueur documentaire.
  CASE WHEN sum(v.nb_bilans_valides) + sum(v.nb_bilans_manquants) > 0
       THEN round(
         sum(v.nb_bilans_valides)::numeric
         / (sum(v.nb_bilans_valides) + sum(v.nb_bilans_manquants)), 4)
  END AS taux_remise_bilans,
  CASE WHEN sum(v.total_prevu) > 0
       THEN round(sum(v.total_realise) / sum(v.total_prevu), 4)
  END AS taux_execution,
  CASE WHEN sum(v.nb_occurrences) > 0
       THEN round(sum(v.nb_occurrences_realisees)::numeric / sum(v.nb_occurrences), 4)
  END AS taux_actions_realisees
FROM bancarisation.organisations o
LEFT JOIN bancarisation.v_parc_projet v ON v.organisation_id = o.id
GROUP BY o.id, o.nom;

-- ═══════════════════════════════════════════════════════════════
-- 6. Consolidé financier du parc, par année et par organisation
-- ═══════════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW bancarisation.v_parc_financier_annuel AS
SELECT
  p.organisation_id,
  org.nom AS organisation_nom,
  d.annee,
  count(DISTINCT d.projet_id) AS nb_projets,
  sum(d.initial) AS initial,
  sum(d.prevu)   AS prevu,
  sum(d.engage)  AS engage,
  sum(d.realise) AS realise,
  sum(d.delta_prevu_initial) AS delta
FROM bancarisation.v_budget_delta_annuel d
JOIN bancarisation.projets p ON p.id = d.projet_id
JOIN bancarisation.organisations org ON org.id = p.organisation_id
GROUP BY p.organisation_id, org.nom, d.annee;
