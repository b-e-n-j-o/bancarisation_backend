-- =============================================================================
-- 046 — v_contact_projet lisible sans JWT + seed contacts projet test
-- =============================================================================
-- La vue joignait `projets` en INNER JOIN : la RLS `projets_lecture` masque
-- toutes les lignes au client supabase-js (clé anon). LEFT JOIN conserve les
-- liens `projet_contact` même si le nom de projet n'est pas lisible.
-- =============================================================================

BEGIN;

CREATE OR REPLACE VIEW bancarisation.v_contact_projet
WITH (security_invoker = true) AS
WITH foncier_liens AS (
  SELECT DISTINCT
    COALESCE(ug.projet_id, pa.projet_id) AS projet_id,
    df.titulaire_id AS personne_id,
    CASE
      WHEN df.nature IN ('propriete', 'usufruit', 'nue_propriete') THEN 'proprietaire'
      WHEN df.nature IN (
        'bail_rural', 'bail_rural_clauses_env', 'bail_emphyteotique',
        'commodat', 'aot', 'convention_paturage'
      ) THEN 'preneur_bail'
      WHEN df.nature IN ('ore', 'convention_gestion', 'regime_forestier')
        THEN 'gestionnaire_site'
      WHEN df.nature = 'cmd_safer' THEN 'safer'
      ELSE 'autre'
    END AS role_code,
    df.nature AS role_precision
  FROM bancarisation.droits_fonciers df
  JOIN bancarisation.parcelles pa ON pa.id = df.parcelle_id
  LEFT JOIN bancarisation.v_ug_parcelles ug ON ug.parcelle_id = pa.id
  WHERE df.titulaire_id IS NOT NULL
    AND COALESCE(ug.projet_id, pa.projet_id) IS NOT NULL

  UNION

  SELECT DISTINCT
    COALESCE(ug.projet_id, pa.projet_id),
    df.constituant_id,
    'proprietaire',
    df.nature
  FROM bancarisation.droits_fonciers df
  JOIN bancarisation.parcelles pa ON pa.id = df.parcelle_id
  LEFT JOIN bancarisation.v_ug_parcelles ug ON ug.parcelle_id = pa.id
  WHERE df.constituant_id IS NOT NULL
    AND df.constituant_id IS DISTINCT FROM df.titulaire_id
    AND COALESCE(ug.projet_id, pa.projet_id) IS NOT NULL
)
SELECT
  pc.id AS lien_id,
  pc.projet_id,
  pr.nom AS projet_nom,
  pc.personne_id,
  pc.role_code,
  r.libelle AS role_libelle,
  r.categorie AS role_categorie,
  r.ordre AS role_ordre,
  pc.role_precision,
  pc.principal,
  pc.date_debut,
  pc.date_fin,
  pc.commentaire,
  'manuel'::text AS source,
  COALESCE(NULLIF(trim(concat_ws(' ', pe.prenom, pe.nom)), ''), pe.nom) AS personne_nom,
  pe.prenom,
  pe.nom,
  pe.email,
  pe.telephone,
  pe.telephone_2,
  pe.fonction,
  pe.type_personne,
  pe.structure_id,
  COALESCE(NULLIF(trim(concat_ws(' ', st.prenom, st.nom)), ''), st.nom) AS structure_nom,
  pe.prestataire_id,
  pe.utilisateur_id,
  pe.archive
FROM bancarisation.projet_contact pc
JOIN bancarisation.role_contact_ref r ON r.code = pc.role_code
JOIN bancarisation.personnes pe ON pe.id = pc.personne_id
LEFT JOIN bancarisation.projets pr ON pr.id = pc.projet_id
LEFT JOIN bancarisation.personnes st ON st.id = pe.structure_id

UNION ALL

SELECT
  NULL::uuid,
  fl.projet_id,
  pr.nom,
  fl.personne_id,
  fl.role_code,
  r.libelle,
  r.categorie,
  r.ordre,
  fl.role_precision,
  false,
  NULL::date,
  NULL::date,
  NULL::text,
  'foncier'::text,
  COALESCE(NULLIF(trim(concat_ws(' ', pe.prenom, pe.nom)), ''), pe.nom),
  pe.prenom,
  pe.nom,
  pe.email,
  pe.telephone,
  pe.telephone_2,
  pe.fonction,
  pe.type_personne,
  pe.structure_id,
  COALESCE(NULLIF(trim(concat_ws(' ', st.prenom, st.nom)), ''), st.nom),
  pe.prestataire_id,
  pe.utilisateur_id,
  pe.archive
FROM foncier_liens fl
JOIN bancarisation.role_contact_ref r ON r.code = fl.role_code
JOIN bancarisation.personnes pe ON pe.id = fl.personne_id
LEFT JOIN bancarisation.projets pr ON pr.id = fl.projet_id
LEFT JOIN bancarisation.personnes st ON st.id = pe.structure_id;

GRANT SELECT ON bancarisation.v_contact_projet TO anon, authenticated;

-- Projet démo Test-ecocompensation : rattacher les fiches MOCK_DEMO + Marion Dupont.
INSERT INTO bancarisation.projet_contact (projet_id, personne_id, role_code, principal)
SELECT
  'fa826cb4-debb-4436-8c21-40fcd7557ea8'::uuid,
  p.id,
  CASE
    WHEN p.email = 'marion.dupont@example.org' THEN 'charge_projet'
    WHEN p.nom ILIKE 'Commune du Porge' THEN 'commune'
    WHEN p.nom ILIKE 'Conservatoire%' THEN 'operateur_compensation'
    WHEN p.nom ILIKE 'EARL des Trois Chênes' THEN 'preneur_bail'
    WHEN p.nom ILIKE 'Indivision Marty' THEN 'proprietaire'
    WHEN p.nom ILIKE 'Lafargue' THEN 'proprietaire'
    WHEN p.nom ILIKE 'Dubosc' THEN 'proprietaire'
    WHEN p.nom ILIKE 'SCI du Grand Pré' THEN 'proprietaire'
    ELSE 'autre'
  END,
  (coalesce(p.email, '') = 'marion.dupont@example.org')
FROM bancarisation.personnes p
WHERE p.archive IS NOT TRUE
  AND (
    coalesce(p.notes, '') = 'MOCK_DEMO'
    OR p.email = 'marion.dupont@example.org'
  )
ON CONFLICT ON CONSTRAINT projet_contact_unique DO NOTHING;

COMMIT;

NOTIFY pgrst, 'reload schema';
