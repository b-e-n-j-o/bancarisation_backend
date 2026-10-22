-- 073_import_modele.sql
-- Import par modèle client : source du journal, paramètres SIG, marquage du projet.


alter table bancarisation.import_projet
  add column if not exists source text not null default 'archive'
    check (source in ('archive', 'modele'));

alter table bancarisation.import_projet
  add column if not exists parametres jsonb not null default '{}'::jsonb;

alter table bancarisation.projets
  add column if not exists origine_import text
    check (origine_import is null or origine_import in ('archive', 'modele'));

alter table bancarisation.projets
  add column if not exists import_id uuid references bancarisation.import_projet(id);

comment on column bancarisation.projets.origine_import is
  'archive = réimport d''un ZIP KerERC ; modele = création depuis projet.xlsx.';

-- Seul le rôle de connexion du backend peut couper l'historique.
-- SET ROLE authenticated ne change pas session_user.
create or replace function bancarisation.log_budget_mouvement()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_motif text := current_setting('bancarisation.motif', true);
  v_par   text := current_setting('bancarisation.modifie_par', true);
begin
  if current_setting('kererc.import_en_cours', true) = 'on'
     and session_user = 'kererc_backend' then
    return new;
  end if;
  if new.statut is distinct from old.statut then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'statut', old.statut, new.statut, v_motif, v_par);
  end if;
  if new.annee is distinct from old.annee then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'annee', old.annee::text, new.annee::text, v_motif, v_par);
  end if;
  if new.mois_debut is distinct from old.mois_debut then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'mois_debut', old.mois_debut::text, new.mois_debut::text, v_motif, v_par);
  end if;
  if new.mois_fin is distinct from old.mois_fin then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'mois_fin', old.mois_fin::text, new.mois_fin::text, v_motif, v_par);
  end if;
  return new;
end $$;

create or replace function bancarisation.log_occurrence_finance()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if current_setting('kererc.import_en_cours', true) = 'on'
     and session_user = 'kererc_backend' then
    return new;
  end if;
  if tg_op = 'INSERT' then
    return new;
  end if;
  if new.montant_ht is distinct from old.montant_ht then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_ht',
            old.montant_ht::text, new.montant_ht::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_engage is distinct from old.montant_engage then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_engage',
            old.montant_engage::text, new.montant_engage::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_realise is distinct from old.montant_realise then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_realise',
            old.montant_realise::text, new.montant_realise::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.montant_ttc is distinct from old.montant_ttc then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'montant_ttc',
            old.montant_ttc::text, new.montant_ttc::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  if new.taux_tva is distinct from old.taux_tva then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.occurrence_id, new.projet_id, 'taux_tva',
            old.taux_tva::text, new.taux_tva::text,
            nullif(current_setting('bancarisation.motif', true), ''),
            nullif(current_setting('bancarisation.modifie_par', true), ''));
  end if;
  return new;
end $$;



