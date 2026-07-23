-- 022_export_geomce.sql
-- Versement GéoMCE : champs projet + historique + référentiel catégories ERC.
-- À exécuter manuellement sur Supabase / Postgres.

-- ── Champs GéoMCE sur le projet (V1 = 1 mesure GéoMCE = 1 projet) ──
ALTER TABLE bancarisation.projets
  ADD COLUMN IF NOT EXISTS geomce_nom varchar(50),
  ADD COLUMN IF NOT EXISTS geomce_nom_verrouille boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS geomce_categorie text,
  ADD COLUMN IF NOT EXISTS geomce_cible text[],
  ADD COLUMN IF NOT EXISTS geomce_description varchar(254),
  ADD COLUMN IF NOT EXISTS geomce_projet_libelle text,
  ADD COLUMN IF NOT EXISTS geomce_procedure_libelle text,
  ADD COLUMN IF NOT EXISTS reference_decision text,
  ADD COLUMN IF NOT EXISTS reference_ei text;

COMMENT ON COLUMN bancarisation.projets.geomce_nom IS
  'Nom mesure tel qu''envoyé à GéoMCE (clé de jointure NOM). Figé après 1er versement.';

-- ── Historique des exports ──
CREATE TABLE IF NOT EXISTS bancarisation.export_geomce (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
  cree_le         timestamptz NOT NULL DEFAULT now(),
  cree_par        text,
  mode            text NOT NULL CHECK (mode IN ('complet', 'geometrie_seule')),
  strategie_geom  text NOT NULL CHECK (strategie_geom IN ('multipart', 'eclate')),
  srid            integer NOT NULL,
  nb_polygones    integer NOT NULL,
  surface_ha      numeric,
  geom_hash       text NOT NULL,
  attributs       jsonb NOT NULL,
  nom_fichier     text NOT NULL,
  storage_path    text NOT NULL,
  statut          text NOT NULL DEFAULT 'genere'
                    CHECK (statut IN ('genere', 'verse', 'abandonne')),
  verse_le        date,
  commentaire     text
);

CREATE INDEX IF NOT EXISTS idx_export_geomce_projet
  ON bancarisation.export_geomce (projet_id, cree_le DESC);

-- ── Référentiel catégories ERC (seed partiel V1 — codes courants + repli) ──
CREATE TABLE IF NOT EXISTS bancarisation.ref_geomce_categorie (
  code    text PRIMARY KEY,
  libelle text NOT NULL,
  niveau  smallint NOT NULL CHECK (niveau BETWEEN 1 AND 4),
  parent  text REFERENCES bancarisation.ref_geomce_categorie(code)
);

INSERT INTO bancarisation.ref_geomce_categorie (code, libelle, niveau, parent) VALUES
  ('E', 'Évitement', 1, NULL),
  ('R', 'Réduction', 1, NULL),
  ('C', 'Compensation', 1, NULL),
  ('A', 'Accompagnement', 1, NULL),
  ('Z', 'Autre / à préciser', 1, NULL),
  ('C1', 'Compensation écologique', 2, 'C'),
  ('C2', 'Compensation forestière', 2, 'C'),
  ('C3', 'Compensation agricole', 2, 'C'),
  ('R1', 'Réduction à la source', 2, 'R'),
  ('E1', 'Évitement géographique', 2, 'E'),
  ('A1', 'Mesure d''accompagnement', 2, 'A'),
  ('Zz', 'Autre (niveau 2)', 2, 'Z'),
  ('C1-1', 'Création / restauration d''habitats', 3, 'C1'),
  ('C1-2', 'Gestion d''habitats', 3, 'C1'),
  ('C1-3', 'Continuités écologiques', 3, 'C1'),
  ('C2-1', 'Boisement / reboisement', 3, 'C2'),
  ('R1-1', 'Adaptation du projet', 3, 'R1'),
  ('E1-1', 'Déplacement d''emprise', 3, 'E1'),
  ('A1-1', 'Suivi écologique', 3, 'A1'),
  ('Zzz', 'Autre (niveau 3)', 3, 'Zz'),
  ('C1-1-a', 'Création de milieux ouverts', 4, 'C1-1'),
  ('C1-1-b', 'Création / restauration de zones humides', 4, 'C1-1'),
  ('C1-1-c', 'Création / restauration de haies / bocage', 4, 'C1-1'),
  ('C1-2-a', 'Gestion de milieux ouverts', 4, 'C1-2'),
  ('C1-2-b', 'Gestion de zones humides', 4, 'C1-2'),
  ('C1-3-a', 'Corridor / trame verte', 4, 'C1-3'),
  ('C2-1-a', 'Plantation forestière', 4, 'C2-1'),
  ('R1-1-a', 'Réduction d''emprise', 4, 'R1-1'),
  ('E1-1-a', 'Évitement d''habitat sensible', 4, 'E1-1'),
  ('A1-1-a', 'Suivi faune / flore', 4, 'A1-1'),
  ('Zzzz', 'Autre (niveau 4) — à préciser dans GéoMCE', 4, 'Zzz')
ON CONFLICT (code) DO NOTHING;
