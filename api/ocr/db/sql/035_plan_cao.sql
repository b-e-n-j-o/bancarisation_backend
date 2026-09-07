-- =============================================================================
-- Migration 035 — Plans CAO (DXF) : persistance aperçu local + groupes de calques
-- =============================================================================
-- geom_local = SRID 0 (repère du dessin). geom / geom_3857 : calage ultérieur.
-- Additive.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS bancarisation.plan_cao (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projet_id         uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
  document_id       uuid REFERENCES bancarisation.documents(id) ON DELETE SET NULL,
  nom_fichier       text NOT NULL,
  dxf_version       text,
  insunits          int,
  facteur_metre     double precision,
  nb_entites        int NOT NULL DEFAULT 0,
  bbox_local        geometry(Polygon, 0),
  calage_mode       text NOT NULL DEFAULT 'non_cale'
                    CHECK (calage_mode IN ('non_cale','srid_direct','deux_points','manuel')),
  srid_cible        int NOT NULL DEFAULT 2154,
  tx                double precision NOT NULL DEFAULT 0,
  ty                double precision NOT NULL DEFAULT 0,
  rotation_rad      double precision NOT NULL DEFAULT 0,
  echelle           double precision NOT NULL DEFAULT 1,
  calque_0_inclus   boolean NOT NULL DEFAULT false,
  statut            text NOT NULL DEFAULT 'analyse'
                    CHECK (statut IN ('en_attente','analyse','cale','echec')),
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  message_erreur    text,
  cree_le           timestamptz NOT NULL DEFAULT now(),
  modifie_le        timestamptz
);

CREATE INDEX IF NOT EXISTS idx_plan_cao_projet
  ON bancarisation.plan_cao (projet_id, cree_le DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_cao_projet_fichier
  ON bancarisation.plan_cao (projet_id, nom_fichier);

CREATE TABLE IF NOT EXISTS bancarisation.plan_cao_groupe (
  plan_id   uuid NOT NULL REFERENCES bancarisation.plan_cao(id) ON DELETE CASCADE,
  id        text NOT NULL,
  nom       text NOT NULL,
  ordre     int NOT NULL DEFAULT 0,
  PRIMARY KEY (plan_id, id)
);

CREATE TABLE IF NOT EXISTS bancarisation.plan_cao_calque (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id     uuid NOT NULL REFERENCES bancarisation.plan_cao(id) ON DELETE CASCADE,
  nom         text NOT NULL,
  groupe_id   text,
  nb_entites  int NOT NULL DEFAULT 0,
  types_dxf   jsonb NOT NULL DEFAULT '{}'::jsonb,
  visible     boolean NOT NULL DEFAULT true,
  UNIQUE (plan_id, nom),
  FOREIGN KEY (plan_id, groupe_id)
    REFERENCES bancarisation.plan_cao_groupe (plan_id, id)
    ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS bancarisation.plan_cao_entite (
  id          bigserial PRIMARY KEY,
  plan_id     uuid NOT NULL REFERENCES bancarisation.plan_cao(id) ON DELETE CASCADE,
  calque      text NOT NULL,
  dxf_type    text NOT NULL,
  handle      text,
  texte       text,
  geom_local  geometry(Geometry, 0) NOT NULL,
  geom        geometry(Geometry, 2154),
  geom_3857   geometry(Geometry, 3857)
);

CREATE INDEX IF NOT EXISTS idx_pce_plan_calque
  ON bancarisation.plan_cao_entite (plan_id, calque);
CREATE INDEX IF NOT EXISTS idx_pce_geom_local
  ON bancarisation.plan_cao_entite USING gist (geom_local);

COMMENT ON TABLE bancarisation.plan_cao IS
  'Plan CAO importé (DXF). Coordonnées locales jusqu''au calage.';
COMMENT ON COLUMN bancarisation.plan_cao_entite.geom_local IS
  'Géométrie brute du dessin (SRID 0), jamais écrasée par le calage.';

COMMIT;
