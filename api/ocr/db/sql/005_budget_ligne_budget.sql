-- Budget — import estimation Excel + lignes extraites (MVP)
-- À exécuter manuellement sur Supabase avant ingestion.

CREATE TABLE IF NOT EXISTS bancarisation.budget_import (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id       uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    fichier_nom     text NOT NULL DEFAULT 'estimation',
    fichier_hash    text,
    modele_llm      text,
    devise          text NOT NULL DEFAULT 'EUR',
    nb_lignes       int NOT NULL DEFAULT 0,
    nb_totaux       int NOT NULL DEFAULT 0,
    nb_avertissements int NOT NULL DEFAULT 0,
    cartographie_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    avertissements  jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS budget_import_projet_idx
    ON bancarisation.budget_import (projet_id, created_at DESC);

CREATE TABLE IF NOT EXISTS bancarisation.ligne_budget (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projet_id           uuid NOT NULL REFERENCES bancarisation.projets(id) ON DELETE CASCADE,
    import_id           uuid REFERENCES bancarisation.budget_import(id) ON DELETE SET NULL,
    libelle_prestation  text NOT NULL,
    libelle_action      text,
    code_mesure         text,
    prestataire         text,
    montant_ht          numeric,
    montant_ttc         numeric,
    taux_tva            numeric,
    unite               text,
    quantite            numeric,
    nb_campagnes        int,
    annees              jsonb NOT NULL DEFAULT '{}'::jsonb,
    ug_ids              text[] NOT NULL DEFAULT '{}',
    est_total           boolean NOT NULL DEFAULT false,
    statut_reel         text,
    action_associee     text,
    confiance           double precision,
    champs_a_confirmer   text[] NOT NULL DEFAULT '{}',
    source_feuille      text,
    source_lignes       int[] NOT NULL DEFAULT '{}',
    ligne_json          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ligne_budget_projet_idx
    ON bancarisation.ligne_budget (projet_id);
CREATE INDEX IF NOT EXISTS ligne_budget_import_idx
    ON bancarisation.ligne_budget (import_id);
CREATE INDEX IF NOT EXISTS ligne_budget_est_total_idx
    ON bancarisation.ligne_budget (projet_id, est_total);
CREATE INDEX IF NOT EXISTS ligne_budget_ug_ids_gin
    ON bancarisation.ligne_budget USING gin (ug_ids);
