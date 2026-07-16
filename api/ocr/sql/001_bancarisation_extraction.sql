-- Schéma bancarisation — fondation + extraction OCR
-- Ordre : organisations → projets → tables d'extraction (FK projet_id)

CREATE SCHEMA IF NOT EXISTS bancarisation;

-- ── Fondation : organisation + projet ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bancarisation.organisations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nom         text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO bancarisation.organisations (id, nom)
VALUES ('a1000000-0000-0000-0000-000000000001', 'Kerelia V0')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS bancarisation.projets (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id     uuid NOT NULL REFERENCES bancarisation.organisations(id),
    nom                 text NOT NULL,
    reference_interne   text,
    commune             text,
    departement         text,
    description         text,
    type_procedure      text,
    date_decision       date,
    duree_annees        int,
    date_fin            date GENERATED ALWAYS AS (
        CASE
            WHEN date_decision IS NOT NULL AND duree_annees IS NOT NULL
            THEN date_decision + make_interval(years => duree_annees)
            ELSE NULL
        END
    ) STORED,
    statut              text NOT NULL DEFAULT 'en_instruction',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS projets_organisation_idx
    ON bancarisation.projets (organisation_id);

-- ── Harmonisation dossier_id → projet_id (bases existantes) ────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bancarisation' AND table_name = 'occurrence' AND column_name = 'dossier_id'
    ) THEN
        ALTER TABLE bancarisation.occurrence RENAME COLUMN dossier_id TO projet_id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bancarisation' AND table_name = 'echeance' AND column_name = 'dossier_id'
    ) THEN
        ALTER TABLE bancarisation.echeance RENAME COLUMN dossier_id TO projet_id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bancarisation' AND table_name = 'extraction_import' AND column_name = 'dossier_id'
    ) THEN
        ALTER TABLE bancarisation.extraction_import RENAME COLUMN dossier_id TO projet_id;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS bancarisation.extraction_import (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    fichier_nom     text NOT NULL,
    fichier_hash    text,
    modele_ocr      text,
    modele_llm      text,
    nb_metadata     int NOT NULL DEFAULT 0,
    nb_actions      int NOT NULL DEFAULT 0,
    nb_echeances    int NOT NULL DEFAULT 0,
    nb_occurrences  int NOT NULL DEFAULT 0,
    nb_non_placables int NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE bancarisation.extraction_import
    ADD COLUMN IF NOT EXISTS nb_metadata int NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS nb_actions int NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS bancarisation.projet_metadata (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id           uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    import_id           uuid REFERENCES bancarisation.extraction_import(id) ON DELETE SET NULL,
    nom_operation       text,
    maitre_ouvrage      text,
    operateur           text,
    communes            text[] NOT NULL DEFAULT '{}',
    arrete_numero       text,
    arrete_date         text,
    horizon_debut       int,
    horizon_fin         int,
    horizon_duree_ans   int,
    metadata_json       jsonb NOT NULL,
    confiance           float,
    champs_a_confirmer  text[] NOT NULL DEFAULT '{}',
    avertissements      text[] NOT NULL DEFAULT '{}',
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (projet_id)
);

CREATE TABLE IF NOT EXISTS bancarisation.action_fiche (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id           uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    import_id           uuid REFERENCES bancarisation.extraction_import(id) ON DELETE SET NULL,
    cle                 text NOT NULL,
    code                text NOT NULL,
    categorie           text NOT NULL,
    titre               text NOT NULL,
    contenu_integral    text NOT NULL,
    fiche_json          jsonb NOT NULL,
    ug_ids              text[] NOT NULL DEFAULT '{}',
    confiance           float,
    champs_a_confirmer  text[] NOT NULL DEFAULT '{}',
    avertissements      text[] NOT NULL DEFAULT '{}',
    UNIQUE (projet_id, cle)
);

CREATE TABLE IF NOT EXISTS bancarisation.echeance (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id               uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    import_id               uuid REFERENCES bancarisation.extraction_import(id) ON DELETE SET NULL,
    cle                     text NOT NULL,
    action_cle              text,
    code_operation          text NOT NULL,
    type_operation          text NOT NULL,
    type_metier             text NOT NULL,
    libelle                 text NOT NULL,
    objectif_long_terme     text,
    objectif_operationnel   text,
    ug_ids                  text[] NOT NULL DEFAULT '{}',
    parcelles               text[] NOT NULL DEFAULT '{}',
    communes                text[] NOT NULL DEFAULT '{}',
    recurrence              jsonb NOT NULL,
    fenetre_debut           text,
    fenetre_fin             text,
    fenetre_traverse_nouvel_an boolean NOT NULL DEFAULT false,
    fenetre_texte_source    text,
    conditions              text[] NOT NULL DEFAULT '{}',
    indicateurs             text[] NOT NULL DEFAULT '{}',
    intervenants            text[] NOT NULL DEFAULT '{}',
    duree_gestion_ans       int,
    source_page             int,
    source_extrait          text,
    confiance               float NOT NULL,
    champs_a_confirmer      text[] NOT NULL DEFAULT '{}',
    avertissements          text[] NOT NULL DEFAULT '{}',
    UNIQUE (projet_id, cle)
);

ALTER TABLE bancarisation.echeance
    ADD COLUMN IF NOT EXISTS action_cle text;

ALTER TABLE bancarisation.action_fiche
    ADD COLUMN IF NOT EXISTS ug_ids text[] NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS bancarisation.occurrence (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id           uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    echeance_id         uuid REFERENCES bancarisation.echeance(id) ON DELETE SET NULL,
    annee               int NOT NULL,
    code                text NOT NULL,
    titre               text NOT NULL,
    categorie           text NOT NULL,
    statut              text NOT NULL DEFAULT 'planifie',
    ug_ids              text[] NOT NULL DEFAULT '{}',
    mois_debut          int,
    mois_fin            int,
    traverse_nouvel_an  boolean NOT NULL DEFAULT false,
    origine             text NOT NULL DEFAULT 'ia',
    confiance           float,
    champs_a_confirmer  text[] NOT NULL DEFAULT '{}',
    avertissements      text[] NOT NULL DEFAULT '{}',
    modifie_le          timestamptz,
    date_realisation    date,
    commentaire         text
);

CREATE UNIQUE INDEX IF NOT EXISTS occurrence_ia_echeance_annee_idx
    ON bancarisation.occurrence (echeance_id, annee)
    WHERE origine = 'ia' AND echeance_id IS NOT NULL;

CREATE OR REPLACE VIEW bancarisation.v_occurrence_calendrier AS
SELECT
    o.id,
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
    e.action_cle
FROM bancarisation.occurrence o
LEFT JOIN bancarisation.echeance e ON e.id = o.echeance_id;
