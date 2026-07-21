-- 010_prestataires_enrich.sql
-- Enrichit le référentiel prestataires + lien occurrence / projet.
--
-- Modèle de rattachement :
--   1. occurrence.prestataire_id  → FK directe (source de vérité opérationnelle)
--   2. projet_prestataire         → rattachement EXPLICITE au dossier
--      (contact préféré, prestataire pressenti avant d'avoir une occurrence)
--   3. v_projet_prestataires      → UNE vue qui UNION les deux sources
--      (dérivé des occurrences + liens explicites). Pas besoin de deux vues :
--      projet → occurrences → prestataire couvre déjà le cas courant ;
--      le FULL JOIN / UNION avec projet_prestataire ajoute les rattachements
--      « dossier » sans occurrence.

-- ---------------------------------------------------------------
-- 1. Attributs prestataire
-- ---------------------------------------------------------------

ALTER TABLE bancarisation.prestataires
  ADD COLUMN IF NOT EXISTS siret text NULL,
  ADD COLUMN IF NOT EXISTS forme_juridique text NULL,
  ADD COLUMN IF NOT EXISTS adresse text NULL,
  ADD COLUMN IF NOT EXISTS code_postal text NULL,
  ADD COLUMN IF NOT EXISTS commune text NULL,
  ADD COLUMN IF NOT EXISTS departement text NULL,  -- code INSEE 2–3 car. (ex. '34', '2A')
  ADD COLUMN IF NOT EXISTS email text NULL,
  ADD COLUMN IF NOT EXISTS telephone text NULL,
  ADD COLUMN IF NOT EXISTS interlocuteur text NULL,
  ADD COLUMN IF NOT EXISTS specialites text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS categories_mesure text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS actif boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS notes text NULL;

CREATE INDEX IF NOT EXISTS prestataires_departement_idx
  ON bancarisation.prestataires (departement)
  WHERE actif;

CREATE INDEX IF NOT EXISTS prestataires_specialites_gin
  ON bancarisation.prestataires USING gin (specialites);

CREATE INDEX IF NOT EXISTS prestataires_categories_gin
  ON bancarisation.prestataires USING gin (categories_mesure);

-- ---------------------------------------------------------------
-- 2. Lien occurrence → prestataire (FK)
--    On conserve occurrence.prestataire (text) en dénormalisation d'affichage
--    pour ne pas casser le front actuel ; à terme le front lira via la vue.
-- ---------------------------------------------------------------

ALTER TABLE bancarisation.occurrence
  ADD COLUMN IF NOT EXISTS prestataire_id uuid NULL
    REFERENCES bancarisation.prestataires (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS occurrence_prestataire_idx
  ON bancarisation.occurrence (prestataire_id)
  WHERE prestataire_id IS NOT NULL;

-- Backfill : rattache si le nom texte matche un prestataire du référentiel.
UPDATE bancarisation.occurrence o
SET prestataire_id = p.id
FROM bancarisation.prestataires p
WHERE o.prestataire_id IS NULL
  AND o.prestataire IS NOT NULL
  AND lower(trim(o.prestataire)) = lower(trim(p.nom));

-- ---------------------------------------------------------------
-- 3. Lien explicite projet ↔ prestataire (hors occurrence)
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bancarisation.projet_prestataire (
  projet_id uuid NOT NULL REFERENCES bancarisation.projets (id) ON DELETE CASCADE,
  prestataire_id uuid NOT NULL REFERENCES bancarisation.prestataires (id) ON DELETE CASCADE,
  role text NULL,              -- ex. 'titulaire', 'sous-traitant', 'pressenti'
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT projet_prestataire_pkey PRIMARY KEY (projet_id, prestataire_id)
);

CREATE INDEX IF NOT EXISTS projet_prestataire_presta_idx
  ON bancarisation.projet_prestataire (prestataire_id);

-- ---------------------------------------------------------------
-- 4. Vue calendrier : append prestataire_id (+ libellé référentiel) EN FIN
-- ---------------------------------------------------------------

CREATE OR REPLACE VIEW bancarisation.v_occurrence_calendrier AS
 SELECT o.id,
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
    COALESCE(NULLIF(NULLIF(o.lib_thema, ''::text), 'autre'::text), NULLIF(NULLIF(e.lib_thema, ''::text), 'autre'::text), NULLIF(af.lib_thema, ''::text), 'autre'::text) AS lib_thema,
    o.montant_ht,
    o.montant_ttc,
    o.taux_tva,
    o.prestataire,
    o.ligne_budget_id,
    o.montant_initial,
    o.annee_initiale,
    o.montant_engage,
    o.montant_realise,
    o.prestataire_id,
    COALESCE(p.nom, o.prestataire) AS prestataire_nom
   FROM bancarisation.occurrence o
     LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id
     LEFT JOIN bancarisation.action_fiche af ON af.projet_id = o.projet_id AND af.cle = e.action_cle
     LEFT JOIN bancarisation.prestataires p ON p.id = o.prestataire_id;

-- ---------------------------------------------------------------
-- 5. UNE vue projet ↔ prestataires (occurrences ∪ liens explicites)
-- ---------------------------------------------------------------

CREATE OR REPLACE VIEW bancarisation.v_projet_prestataires AS
WITH depuis_occurrences AS (
  SELECT
    o.projet_id,
    o.prestataire_id,
    'occurrence'::text AS source,
    count(*) FILTER (WHERE o.statut <> 'supprime') AS nb_occurrences,
    coalesce(sum(o.montant_ht) FILTER (WHERE o.statut <> 'supprime'), 0) AS total_prevu_ht
  FROM bancarisation.occurrence o
  WHERE o.prestataire_id IS NOT NULL
  GROUP BY o.projet_id, o.prestataire_id
),
depuis_projet AS (
  SELECT
    pp.projet_id,
    pp.prestataire_id,
    'projet'::text AS source,
    0 AS nb_occurrences,
    0::numeric AS total_prevu_ht
  FROM bancarisation.projet_prestataire pp
)
SELECT
  coalesce(occ.projet_id, prj.projet_id) AS projet_id,
  coalesce(occ.prestataire_id, prj.prestataire_id) AS prestataire_id,
  p.nom AS prestataire_nom,
  p.departement,
  p.commune,
  p.specialites,
  p.categories_mesure,
  p.actif,
  CASE
    WHEN occ.prestataire_id IS NOT NULL AND prj.prestataire_id IS NOT NULL THEN 'les_deux'
    WHEN occ.prestataire_id IS NOT NULL THEN 'occurrence'
    ELSE 'projet'
  END AS source,
  coalesce(occ.nb_occurrences, 0) AS nb_occurrences,
  coalesce(occ.total_prevu_ht, 0) AS total_prevu_ht
FROM depuis_occurrences occ
FULL JOIN depuis_projet prj
  ON prj.projet_id = occ.projet_id
 AND prj.prestataire_id = occ.prestataire_id
JOIN bancarisation.prestataires p
  ON p.id = coalesce(occ.prestataire_id, prj.prestataire_id);

-- ---------------------------------------------------------------
-- 6. Seed attributs synthétiques (sur les 12 ids de 009)
-- ---------------------------------------------------------------

UPDATE bancarisation.prestataires SET
  siret = '532 841 290 00014',
  forme_juridique = 'SARL',
  adresse = '12 rue des Cigales',
  code_postal = '34000',
  commune = 'Montpellier',
  departement = '34',
  email = 'contact@ecosphere-conseil.example',
  telephone = '04 67 00 00 01',
  interlocuteur = 'Marie Dupont',
  specialites = ARRAY['inventaires', 'dossiers CNPN', 'suivi écologique'],
  categories_mesure = ARRAY['SE', 'EP', 'MG']
WHERE id = 'b2000000-0000-4000-8000-000000000001';

UPDATE bancarisation.prestataires SET
  siret = '418 926 571 00028',
  forme_juridique = 'SAS',
  adresse = '8 allée de la Biodiversité',
  code_postal = '31000',
  commune = 'Toulouse',
  departement = '31',
  email = 'occitanie@biotope.example',
  telephone = '05 61 00 00 02',
  interlocuteur = 'Jean Moreau',
  specialites = ARRAY['études d''impact', 'Natura 2000', 'habitats'],
  categories_mesure = ARRAY['EP', 'SE', 'TU']
WHERE id = 'b2000000-0000-4000-8000-000000000002';

UPDATE bancarisation.prestataires SET
  siret = '803 112 445 00019',
  forme_juridique = 'EURL',
  adresse = 'ZAC des Capucins',
  code_postal = '30100',
  commune = 'Alès',
  departement = '30',
  email = 'travaux@genie-eco-midi.example',
  telephone = '04 66 00 00 03',
  interlocuteur = 'Paul Rey',
  specialites = ARRAY['génie écologique', 'recréation de milieux', 'plantations'],
  categories_mesure = ARRAY['TU', 'TE']
WHERE id = 'b2000000-0000-4000-8000-000000000003';

UPDATE bancarisation.prestataires SET
  siret = '791 554 220 00033',
  forme_juridique = 'Association',
  adresse = '3 chemin des Chênes',
  code_postal = '41000',
  commune = 'Blois',
  departement = '41',
  email = 'haies@atelier-des-haies.example',
  telephone = '02 54 00 00 04',
  interlocuteur = 'Claire Martin',
  specialites = ARRAY['haies bocagères', 'agroécologie'],
  categories_mesure = ARRAY['TU', 'TE', 'MG']
WHERE id = 'b2000000-0000-4000-8000-000000000004';

UPDATE bancarisation.prestataires SET
  siret = '529 774 018 00041',
  forme_juridique = 'SARL',
  adresse = '45 avenue du Lac',
  code_postal = '74000',
  commune = 'Annecy',
  departement = '74',
  email = 'info@naturalia-env.example',
  telephone = '04 50 00 00 05',
  interlocuteur = 'Sophie Leroy',
  specialites = ARRAY['suivi faune', 'chiroptères', 'amphibiens'],
  categories_mesure = ARRAY['SE']
WHERE id = 'b2000000-0000-4000-8000-000000000005';

UPDATE bancarisation.prestataires SET
  siret = '844 201 993 00012',
  forme_juridique = 'SAS',
  adresse = '1 place de l''Étang',
  code_postal = '69000',
  commune = 'Millau',
  departement = '12',
  email = 'hello@teroiko.example',
  telephone = '05 65 00 00 06',
  interlocuteur = 'Nicolas Blanc',
  specialites = ARRAY['modélisation', 'biodiversité numérique'],
  categories_mesure = ARRAY['SE', 'MG']
WHERE id = 'b2000000-0000-4000-8000-000000000006';

UPDATE bancarisation.prestataires SET
  siret = '775 666 120 00055',
  forme_juridique = 'Association',
  adresse = 'Maison de la Nature',
  code_postal = '17300',
  commune = 'Rochefort',
  departement = '17',
  email = 'mission.conseil@lpo.example',
  telephone = '05 46 00 00 07',
  interlocuteur = 'Anne Petit',
  specialites = ARRAY['oiseaux', 'plans de gestion', 'animation'],
  categories_mesure = ARRAY['SE', 'MG', 'EP']
WHERE id = 'b2000000-0000-4000-8000-000000000007';

UPDATE bancarisation.prestataires SET
  siret = '390 218 774 00008',
  forme_juridique = 'SARL',
  adresse = 'Route de la Sologne',
  code_postal = '41200',
  commune = 'Romorantin-Lanthenay',
  departement = '41',
  email = 'contact@sologne-nature.example',
  telephone = '02 54 00 00 08',
  interlocuteur = 'Luc Bernard',
  specialites = ARRAY['entretien milieux', 'fauche tardive', 'mares'],
  categories_mesure = ARRAY['TE', 'TU']
WHERE id = 'b2000000-0000-4000-8000-000000000008';

UPDATE bancarisation.prestataires SET
  siret = '812 445 667 00021',
  forme_juridique = 'SAS',
  adresse = 'Quai des Marais',
  code_postal = '13002',
  commune = 'Marseille',
  departement = '13',
  email = 'projets@aquaterra.example',
  telephone = '04 91 00 00 09',
  interlocuteur = 'Inès Garnier',
  specialites = ARRAY['zones humides', 'restauration cours d''eau'],
  categories_mesure = ARRAY['TU', 'TE', 'SE']
WHERE id = 'b2000000-0000-4000-8000-000000000009';

UPDATE bancarisation.prestataires SET
  siret = '478 902 331 00017',
  forme_juridique = 'SARL',
  adresse = '22 boulevard Vert',
  code_postal = '69000',
  commune = 'Rennes',
  departement = '35',
  email = 'bureau@paysages-biodiversite.example',
  telephone = '02 99 00 00 10',
  interlocuteur = 'Thomas Guillot',
  specialites = ARRAY['paysage', 'revegetation', 'VRD écologiques'],
  categories_mesure = ARRAY['TU', 'TE', 'MG']
WHERE id = 'b2000000-0000-4000-8000-00000000000a';

UPDATE bancarisation.prestataires SET
  siret = '501 338 882 00044',
  forme_juridique = 'EURL',
  adresse = '7 impasse des Genêts',
  code_postal = '63000',
  commune = 'Clermont-Ferrand',
  departement = '63',
  email = 'expertise@faune-flore.example',
  telephone = '04 73 00 00 11',
  interlocuteur = 'Élodie Roux',
  specialites = ARRAY['botanique', 'entomologie', 'état initial'],
  categories_mesure = ARRAY['EP', 'SE']
WHERE id = 'b2000000-0000-4000-8000-00000000000b';

UPDATE bancarisation.prestataires SET
  siret = '889 120 556 00009',
  forme_juridique = 'SAS',
  adresse = 'Parc d''activités Compensation',
  code_postal = '69000',
  commune = 'Lyon',
  departement = '69',
  email = 'ops@compenseo.example',
  telephone = '04 78 00 00 12',
  interlocuteur = 'Hugo Fernandes',
  specialites = ARRAY['pilotage compensation', 'AMO', 'reporting'],
  categories_mesure = ARRAY['MG', 'SE', 'TU']
WHERE id = 'b2000000-0000-4000-8000-00000000000c';
