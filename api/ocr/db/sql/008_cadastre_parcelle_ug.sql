-- Liens spatiaux UG ↔ parcelles cadastrales (après snapshot IGN).
-- Une parcelle peut composer plusieurs UG si elles se chevauchent.

CREATE TABLE IF NOT EXISTS bancarisation.cadastre_parcelle_ug (
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    ug_id           text NOT NULL,
    idu             text NOT NULL,
    section         text,
    numero          text,
    code_insee      text,
    nom_com         text,
    contenance      integer,
    surface_inter_m2 numeric,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (projet_id, ug_id, idu)
);

CREATE INDEX IF NOT EXISTS cadastre_parcelle_ug_projet_idx
    ON bancarisation.cadastre_parcelle_ug (projet_id);
CREATE INDEX IF NOT EXISTS cadastre_parcelle_ug_ug_idx
    ON bancarisation.cadastre_parcelle_ug (projet_id, ug_id);
CREATE INDEX IF NOT EXISTS cadastre_parcelle_ug_idu_idx
    ON bancarisation.cadastre_parcelle_ug (projet_id, idu);

COMMENT ON TABLE bancarisation.cadastre_parcelle_ug IS
    'Croisement spatial : parcelles IGN qui intersectent chaque UG (composition cadastrale).';
