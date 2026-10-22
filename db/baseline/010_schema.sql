--
-- PostgreSQL database dump
--

\restrict 2bx13sqcgcAkaaeJGaOXUl1DTlWH1We5i401DA53E4ucaxp5d503XmDgLzi9r6x

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.11 (Postgres.app)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: bancarisation; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA bancarisation;


ALTER SCHEMA bancarisation OWNER TO postgres;

--
-- Name: prive; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA prive;


ALTER SCHEMA prive OWNER TO postgres;

--
-- Name: source_constat; Type: TYPE; Schema: bancarisation; Owner: postgres
--

CREATE TYPE bancarisation.source_constat AS ENUM (
    'inventaire',
    'constat_controle',
    'satellite'
);


ALTER TYPE bancarisation.source_constat OWNER TO postgres;

--
-- Name: statut_agrement; Type: TYPE; Schema: bancarisation; Owner: postgres
--

CREATE TYPE bancarisation.statut_agrement AS ENUM (
    'en_montage',
    'agree',
    'transfere',
    'abroge'
);


ALTER TYPE bancarisation.statut_agrement OWNER TO postgres;

--
-- Name: statut_constat; Type: TYPE; Schema: bancarisation; Owner: postgres
--

CREATE TYPE bancarisation.statut_constat AS ENUM (
    'atteint',
    'partiel',
    'non_atteint',
    'non_evalue'
);


ALTER TYPE bancarisation.statut_constat OWNER TO postgres;

--
-- Name: statut_vente; Type: TYPE; Schema: bancarisation; Owner: postgres
--

CREATE TYPE bancarisation.statut_vente AS ENUM (
    'reserve',
    'vendu',
    'affecte',
    'annule'
);


ALTER TYPE bancarisation.statut_vente OWNER TO postgres;

--
-- Name: type_dispositif; Type: TYPE; Schema: bancarisation; Owner: postgres
--

CREATE TYPE bancarisation.type_dispositif AS ENUM (
    'obligation',
    'site_credits'
);


ALTER TYPE bancarisation.type_dispositif OWNER TO postgres;

--
-- Name: usage_credit; Type: TYPE; Schema: bancarisation; Owner: postgres
--

CREATE TYPE bancarisation.usage_credit AS ENUM (
    'compensation_reglementaire',
    'compensation_volontaire',
    'contribution_volontaire'
);


ALTER TYPE bancarisation.usage_credit OWNER TO postgres;

--
-- Name: accepter_transfert(uuid); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.accepter_transfert(p_transfert uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
declare
  t          bancarisation.transfert_projet;
  v_retires  int;
begin
  if (select auth.uid()) is null then
    raise exception 'Authentification requise' using errcode = '42501';
  end if;

  select * into t from bancarisation.transfert_projet where id = p_transfert for update;

  if t.id is null or t.statut <> 'en_attente'
     or coalesce(prive.role_org(t.org_cible), '') <> 'admin' then
    raise exception 'Transfert introuvable ou droits insuffisants' using errcode = '42501';
  end if;

  if (select organisation_id from bancarisation.projets where id = t.projet_id) is distinct from t.org_source then
    raise exception 'Le projet a changé de propriétaire depuis la demande' using errcode = '40001';
  end if;

  -- 1. Nouveau propriétaire (le trigger de gel laisse passer : la fonction ne s'exécute pas en « authenticated »)
  update bancarisation.projets
     set organisation_id = t.org_cible, updated_at = now()
   where id = t.projet_id;

  -- 2. Retrait des accès personnels des membres de l'organisation cédante (et de ses ancêtres)
  delete from bancarisation.projet_acces pa
   where pa.projet_id = t.projet_id
     and pa.utilisateur_id in (
       select m.utilisateur_id
         from bancarisation.membre_organisation m
         join bancarisation.organisation_closure c on c.ancetre_id = m.organisation_id
        where c.descendant_id = t.org_source);
  get diagnostics v_retires = row_count;

  -- 3. Un partage vers le repreneur n'a plus lieu d'être : il est propriétaire
  delete from bancarisation.projet_acces
   where projet_id = t.projet_id and organisation_id = t.org_cible;

  -- 4. Lecture conservée par le cédant (historique, responsabilité sur sa période)
  if t.garder_lecture_source then
    insert into bancarisation.projet_acces
      (projet_id, organisation_id, qualite, niveau, voir_finances, accorde_par)
    values
      (t.projet_id, t.org_source, 'ancien_proprietaire', 'lecteur', t.finances_source, (select auth.uid()))
    on conflict do nothing;
  end if;

  -- Les partages externes (maître d'ouvrage, DREAL...) sont conservés tels quels.

  -- 5. Clôture et journal, côté cédant et côté repreneur
  update bancarisation.transfert_projet
     set statut = 'accepte', traite_par = (select auth.uid()), traite_le = now()
   where id = t.id;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  select (select auth.uid()), o, t.projet_id, 'transfert.accepte', t.org_cible::text,
         jsonb_build_object('transfert_id', t.id, 'org_source', t.org_source, 'org_cible', t.org_cible,
                            'acces_personnels_retires', v_retires,
                            'lecture_conservee', t.garder_lecture_source)
    from unnest(array[t.org_source, t.org_cible]) as o;
end $$;


ALTER FUNCTION bancarisation.accepter_transfert(p_transfert uuid) OWNER TO postgres;

--
-- Name: annuler_transfert(uuid); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.annuler_transfert(p_transfert uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
declare t bancarisation.transfert_projet;
begin
  select * into t from bancarisation.transfert_projet where id = p_transfert for update;
  if t.id is null or t.statut <> 'en_attente'
     or not (prive.peut_partager(t.projet_id) or coalesce(prive.role_org(t.org_source), '') = 'admin') then
    raise exception 'Transfert introuvable ou droits insuffisants' using errcode = '42501';
  end if;

  update bancarisation.transfert_projet
     set statut = 'annule', traite_par = (select auth.uid()), traite_le = now()
   where id = t.id;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  values ((select auth.uid()), t.org_source, t.projet_id, 'transfert.annule', t.org_cible::text,
          jsonb_build_object('transfert_id', t.id));
end $$;


ALTER FUNCTION bancarisation.annuler_transfert(p_transfert uuid) OWNER TO postgres;

--
-- Name: appliquer_calage_plan(uuid); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.appliquer_calage_plan(p_plan_id uuid) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
DECLARE
  p record;
  a double precision;
  b double precision;
  d double precision;
  e double precision;
  n int;
BEGIN
  SELECT * INTO p FROM bancarisation.plan_cao WHERE id = p_plan_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'plan_cao % introuvable', p_plan_id;
  END IF;

  a :=  p.echelle * cos(p.rotation_rad);
  b := -p.echelle * sin(p.rotation_rad);
  d :=  p.echelle * sin(p.rotation_rad);
  e :=  p.echelle * cos(p.rotation_rad);

  WITH src AS (
    SELECT pe.id,
           COALESCE(pcc.srid_source, p.srid_declare, p.srid_cible) AS srid_src
    FROM bancarisation.plan_cao_entite pe
    LEFT JOIN bancarisation.plan_cao_calque pcc
           ON pcc.plan_id = pe.plan_id AND pcc.nom = pe.calque
    WHERE pe.plan_id = p_plan_id
  ),
  cale AS (
    SELECT pe.id,
           CASE
             -- CRS connu : geom_local est déjà dans srid_src → reprojection
             WHEN p.calage_mode = 'srid_direct'
                  AND s.srid_src IS NOT NULL
                  AND s.srid_src > 0
             THEN ST_Transform(
                    ST_SetSRID(ST_Force2D(pe.geom_local), s.srid_src),
                    p.srid_cible
                  )
             -- Calage 2 points : similitude vers srid_cible (coords chantier)
             ELSE ST_SetSRID(
                    ST_MakeValid(
                      ST_Affine(ST_Force2D(pe.geom_local), a, b, d, e, p.tx, p.ty)
                    ),
                    p.srid_cible
                  )
           END AS g
    FROM bancarisation.plan_cao_entite pe
    JOIN src s ON s.id = pe.id
  )
  UPDATE bancarisation.plan_cao_entite pe
  SET geom = c.g,
      geom_3857 = ST_Transform(c.g, 3857)
  FROM cale c
  WHERE pe.id = c.id;

  GET DIAGNOSTICS n = ROW_COUNT;
  UPDATE bancarisation.plan_cao
     SET statut = 'cale', modifie_le = now()
   WHERE id = p_plan_id;
  RETURN n;
END
$$;


ALTER FUNCTION bancarisation.appliquer_calage_plan(p_plan_id uuid) OWNER TO postgres;

--
-- Name: FUNCTION appliquer_calage_plan(p_plan_id uuid); Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON FUNCTION bancarisation.appliquer_calage_plan(p_plan_id uuid) IS 'srid_direct : ST_Transform depuis le CRS du calque. deux_points : ST_Affine (Helmert) vers srid_cible.';


--
-- Name: appliquer_checklist_modele(uuid, uuid[]); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.appliquer_checklist_modele(p_modele_id uuid, p_occurrence_ids uuid[]) RETURNS integer
    LANGUAGE sql
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
  WITH base AS (
    SELECT o.id AS occurrence_id, coalesce(max(e.ordre), 0) AS ordre_max
    FROM bancarisation.occurrence o
    LEFT JOIN bancarisation.action_etape e ON e.occurrence_id = o.id
    WHERE o.id = ANY(p_occurrence_ids)
    GROUP BY o.id
  ), ins AS (
    INSERT INTO bancarisation.action_etape
      (occurrence_id, ordre, libelle, description, modele_etape_id)
    SELECT b.occurrence_id, b.ordre_max + me.ordre, me.libelle, me.description, me.id
    FROM base b
    CROSS JOIN bancarisation.checklist_modele_etape me
    WHERE me.modele_id = p_modele_id
      AND NOT EXISTS (
        SELECT 1 FROM bancarisation.action_etape x
        WHERE x.occurrence_id = b.occurrence_id
          AND x.modele_etape_id = me.id
      )
    RETURNING 1
  )
  SELECT count(*)::int FROM ins;
$$;


ALTER FUNCTION bancarisation.appliquer_checklist_modele(p_modele_id uuid, p_occurrence_ids uuid[]) OWNER TO postgres;

--
-- Name: contacts_partages(uuid); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.contacts_partages(p_projet uuid) RETURNS TABLE(projet_contact_id uuid, personne_id uuid, role_code text, role_precision text, principal boolean, nom text, prenom text, fonction text, email text, telephone text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select pc.id, p.id, pc.role_code, pc.role_precision, pc.principal,
         p.nom, p.prenom, p.fonction, p.email, p.telephone
    from bancarisation.projet_contact pc
    join bancarisation.personnes p on p.id = pc.personne_id
   where pc.projet_id = p_projet
     and pc.partage_partenaires
     and not p.archive
     and p_projet in (select prive.projets_lisibles())
   order by pc.principal desc, p.nom, p.prenom
$$;


ALTER FUNCTION bancarisation.contacts_partages(p_projet uuid) OWNER TO postgres;

--
-- Name: creer_modele_depuis_action(uuid, text, uuid); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.creer_modele_depuis_action(p_occurrence_id uuid, p_nom text, p_organisation_id uuid) RETURNS uuid
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO bancarisation.checklist_modele (organisation_id, nom)
  VALUES (p_organisation_id, p_nom) RETURNING id INTO v_id;

  INSERT INTO bancarisation.checklist_modele_etape (modele_id, ordre, libelle, description)
  SELECT v_id, (row_number() OVER (ORDER BY ordre))::int, libelle, description
  FROM bancarisation.action_etape
  WHERE occurrence_id = p_occurrence_id;

  RETURN v_id;
END $$;


ALTER FUNCTION bancarisation.creer_modele_depuis_action(p_occurrence_id uuid, p_nom text, p_organisation_id uuid) OWNER TO postgres;

--
-- Name: demander_transfert(uuid, uuid, boolean, boolean, text); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.demander_transfert(p_projet uuid, p_org_cible uuid, p_garder_lecture_source boolean DEFAULT true, p_finances_source boolean DEFAULT false, p_commentaire text DEFAULT NULL::text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
declare
  v_source uuid;
  v_nom    text;
  v_id     uuid;
begin
  if (select auth.uid()) is null then
    raise exception 'Authentification requise' using errcode = '42501';
  end if;

  select organisation_id, nom into v_source, v_nom
    from bancarisation.projets where id = p_projet;

  if v_source is null or not prive.peut_partager(p_projet) then
    raise exception 'Droits insuffisants pour transférer ce projet' using errcode = '42501';
  end if;

  if p_org_cible = v_source then
    raise exception 'Le projet appartient déjà à cette organisation' using errcode = '22023';
  end if;

  if not exists (select 1 from bancarisation.organisations
                  where id = p_org_cible and statut = 'active' and not archivee) then
    raise exception 'Organisation cible introuvable ou inactive' using errcode = 'P0002';
  end if;

  insert into bancarisation.transfert_projet
    (projet_id, projet_nom, org_source, org_cible, garder_lecture_source, finances_source, commentaire, demande_par)
  values
    (p_projet, v_nom, v_source, p_org_cible, p_garder_lecture_source, p_finances_source, p_commentaire, (select auth.uid()))
  returning id into v_id;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  values ((select auth.uid()), v_source, p_projet, 'transfert.demande', p_org_cible::text,
          jsonb_build_object('transfert_id', v_id,
                             'garder_lecture_source', p_garder_lecture_source,
                             'finances_source', p_finances_source));
  return v_id;
end $$;


ALTER FUNCTION bancarisation.demander_transfert(p_projet uuid, p_org_cible uuid, p_garder_lecture_source boolean, p_finances_source boolean, p_commentaire text) OWNER TO postgres;

--
-- Name: log_budget_mouvement(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.log_budget_mouvement() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO ''
    AS $$
declare
  v_motif text := current_setting('bancarisation.motif', true);
  v_par   text := current_setting('bancarisation.modifie_par', true);
begin
  if new.statut is distinct from old.statut then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'statut',
            old.statut, new.statut, v_motif, v_par);
  end if;

  if new.annee is distinct from old.annee then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'annee',
            old.annee::text, new.annee::text, v_motif, v_par);
  end if;

  if new.mois_debut is distinct from old.mois_debut then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'mois_debut',
            old.mois_debut::text, new.mois_debut::text, v_motif, v_par);
  end if;

  if new.mois_fin is distinct from old.mois_fin then
    insert into bancarisation.budget_mouvement
      (occurrence_id, projet_id, champ, ancienne_val, nouvelle_val, motif, modifie_par)
    values (new.id, new.projet_id, 'mois_fin',
            old.mois_fin::text, new.mois_fin::text, v_motif, v_par);
  end if;

  return new;
end $$;


ALTER FUNCTION bancarisation.log_budget_mouvement() OWNER TO postgres;

--
-- Name: FUNCTION log_budget_mouvement(); Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON FUNCTION bancarisation.log_budget_mouvement() IS 'Historique append-only occurrence : statut, année, mois_debut, mois_fin. Les montants sont sur occurrence_finance.';


--
-- Name: log_occurrence_finance(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.log_occurrence_finance() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
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
end;
$$;


ALTER FUNCTION bancarisation.log_occurrence_finance() OWNER TO postgres;

--
-- Name: maj_statut_demande(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.maj_statut_demande() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
DECLARE
  v_auteur text;
  v_statut text;
BEGIN
  SELECT auteur, statut INTO v_auteur, v_statut
  FROM bancarisation.demande WHERE id = NEW.demande_id;

  IF v_statut = 'close' THEN
    UPDATE bancarisation.demande SET maj_le = now() WHERE id = NEW.demande_id;
    RETURN NEW;
  END IF;

  UPDATE bancarisation.demande
  SET maj_le = now(),
      statut = CASE
        -- Réponse du camp opposé à celui qui a ouvert la demande
        WHEN NEW.acteur <> v_auteur THEN 'repondue'
        -- Relance par l'auteur : la demande redevient en attente
        ELSE 'ouverte'
      END
  WHERE id = NEW.demande_id;

  RETURN NEW;
END;
$$;


ALTER FUNCTION bancarisation.maj_statut_demande() OWNER TO postgres;

--
-- Name: mes_droits_projets(uuid[]); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.mes_droits_projets(p_ids uuid[] DEFAULT NULL::uuid[]) RETURNS TABLE(projet_id uuid, niveau integer, interne boolean, finances boolean, partage boolean, qualite text)
    LANGUAGE sql STABLE
    SET search_path TO ''
    AS $$
  select p.id,
         prive.niveau_projet(p.id),
         prive.est_interne(p.id),
         prive.voit_finances(p.id),
         prive.peut_partager(p.id),
         case
           when prive.est_interne(p.id) then null
           else (
             select pa.qualite
               from bancarisation.projet_acces pa
              where pa.projet_id = p.id
                and (pa.expire_le is null or pa.expire_le > now())
                and (pa.utilisateur_id = auth.uid()
                     or pa.organisation_id in (
                          select a.organisation_id from prive.mes_appartenances() a))
              order by case pa.niveau
                         when 'gestionnaire' then 3
                         when 'contributeur' then 2
                         else 1
                       end desc
              limit 1)
         end
    from bancarisation.projets p
   where p_ids is null or p.id = any(p_ids);
$$;


ALTER FUNCTION bancarisation.mes_droits_projets(p_ids uuid[]) OWNER TO postgres;

--
-- Name: mon_contexte(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.mon_contexte() RETURNS jsonb
    LANGUAGE sql STABLE
    SET search_path TO ''
    AS $$
  select jsonb_build_object(
    'utilisateur_id', auth.uid(),
    'profil', (
      select jsonb_build_object(
               'nom', p.nom,
               'prenom', p.prenom,
               'admin_plateforme', p.admin_plateforme)
        from bancarisation.profils p
       where p.utilisateur_id = auth.uid()),
    'appartenances', coalesce((
      select jsonb_agg(jsonb_build_object(
               'organisation_id', o.id,
               'nom', o.nom,
               'nature', o.nature,
               'type', o.type,
               'parent_id', o.parent_id,
               'role', m.role,
               'portee', m.portee,
               'acces_global', m.acces_global) order by o.nom)
        from bancarisation.membre_organisation m
        join bancarisation.organisations o on o.id = m.organisation_id
       where m.utilisateur_id = auth.uid()
         and m.statut = 'actif'
         and o.statut = 'active'), '[]'::jsonb)
  );
$$;


ALTER FUNCTION bancarisation.mon_contexte() OWNER TO postgres;

--
-- Name: normalize_ug_id(text); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.normalize_ug_id(raw text) RETURNS text
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
    SELECT NULLIF(lower(regexp_replace(coalesce(raw, ''), '[^a-zA-Z0-9]', '', 'g')), '');
$$;


ALTER FUNCTION bancarisation.normalize_ug_id(raw text) OWNER TO postgres;

--
-- Name: normalize_ug_ids(text[]); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.normalize_ug_ids(arr text[]) RETURNS text[]
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
    SELECT coalesce(
        array_agg(DISTINCT n ORDER BY n) FILTER (WHERE n IS NOT NULL AND n <> ''),
        '{}'::text[]
    )
    FROM unnest(coalesce(arr, '{}'::text[])) AS u(v)
    CROSS JOIN LATERAL (SELECT bancarisation.normalize_ug_id(v) AS n) s;
$$;


ALTER FUNCTION bancarisation.normalize_ug_ids(arr text[]) OWNER TO postgres;

--
-- Name: peut_exporter_projet(uuid); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.peut_exporter_projet(p_projet_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select exists (
    select 1
      from bancarisation.projets p
      join bancarisation.organisation_closure c
        on c.descendant_id = p.organisation_id
      join bancarisation.membre_organisation m
        on m.organisation_id = c.ancetre_id
       and m.utilisateur_id = (select auth.uid())
       and m.statut = 'actif'
       and m.role = 'admin'
       and (m.portee = 'branche' or c.profondeur = 0)
      join bancarisation.organisations o
        on o.id = m.organisation_id
       and o.statut = 'active'
     where p.id = p_projet_id
  );
$$;


ALTER FUNCTION bancarisation.peut_exporter_projet(p_projet_id uuid) OWNER TO postgres;

--
-- Name: peut_lire_archive_export(uuid); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.peut_lire_archive_export(p_organisation uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select exists (
    select 1 from prive.mes_appartenances() a
     where a.organisation_id = p_organisation
       and a.role = 'admin'
  );
$$;


ALTER FUNCTION bancarisation.peut_lire_archive_export(p_organisation uuid) OWNER TO postgres;

--
-- Name: proteger_rapport_valide(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.proteger_rapport_valide() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
BEGIN
  IF OLD.statut = 'valide' THEN
    IF NEW.id            IS DISTINCT FROM OLD.id
       OR NEW.projet_id  IS DISTINCT FROM OLD.projet_id
       OR NEW.annee      IS DISTINCT FROM OLD.annee
       OR NEW.version    IS DISTINCT FROM OLD.version
       OR NEW.statut     IS DISTINCT FROM OLD.statut
       OR NEW.genere_le  IS DISTINCT FROM OLD.genere_le
       OR NEW.genere_par IS DISTINCT FROM OLD.genere_par
       OR NEW.valide_le  IS DISTINCT FROM OLD.valide_le
       OR NEW.valide_par IS DISTINCT FROM OLD.valide_par
       OR NEW.borne_donnees IS DISTINCT FROM OLD.borne_donnees
       OR NEW.baseline_id   IS DISTINCT FROM OLD.baseline_id
       OR NEW.donnees    IS DISTINCT FROM OLD.donnees
       OR NEW.controles  IS DISTINCT FROM OLD.controles
    THEN
      RAISE EXCEPTION 'Bilan validé : immuable (seul document_id peut être rattaché). Générer une nouvelle version.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION bancarisation.proteger_rapport_valide() OWNER TO postgres;

--
-- Name: rattacher_export_transfert(uuid, uuid); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.rattacher_export_transfert(p_transfert uuid, p_export uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
begin
  update bancarisation.transfert_projet t
     set export_id = p_export
   where t.id = p_transfert
     and t.demande_par = (select auth.uid())
     and t.statut = 'en_attente'
     and exists (
       select 1 from bancarisation.export_projet e
        where e.id = p_export
          and e.projet_id = t.projet_id
          and e.demande_par = (select auth.uid())
     );
  if not found then
    raise exception 'Transfert ou export introuvable' using errcode = '42501';
  end if;
end;
$$;


ALTER FUNCTION bancarisation.rattacher_export_transfert(p_transfert uuid, p_export uuid) OWNER TO postgres;

--
-- Name: rebuild_organisation_closure(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.rebuild_organisation_closure() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'pg_catalog'
    AS $$
begin
  -- Table de très petite taille (quelques dizaines de lignes en
  -- pratique) : reconstruction intégrale à chaque changement.
  delete from organisation_closure;

  with recursive arbre as (
    select id as ancetre_id, id as descendant_id, 0 as profondeur
    from organisations
    union all
    select a.ancetre_id, o.id, a.profondeur + 1
    from arbre a
    join organisations o on o.parent_id = a.descendant_id
    where a.profondeur < 10          -- garde-fou anti-cycle
  )
  insert into organisation_closure (ancetre_id, descendant_id, profondeur)
  select ancetre_id, descendant_id, min(profondeur)
  from arbre
  group by ancetre_id, descendant_id;

  if exists (
    select 1 from organisation_closure
    where ancetre_id = descendant_id and profondeur > 0
  ) then
    raise exception 'Cycle détecté dans la hiérarchie des organisations';
  end if;
end;
$$;


ALTER FUNCTION bancarisation.rebuild_organisation_closure() OWNER TO postgres;

--
-- Name: refuser_transfert(uuid, text); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.refuser_transfert(p_transfert uuid, p_motif text DEFAULT NULL::text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
declare t bancarisation.transfert_projet;
begin
  select * into t from bancarisation.transfert_projet where id = p_transfert for update;
  if t.id is null or t.statut <> 'en_attente'
     or coalesce(prive.role_org(t.org_cible), '') <> 'admin' then
    raise exception 'Transfert introuvable ou droits insuffisants' using errcode = '42501';
  end if;

  update bancarisation.transfert_projet
     set statut = 'refuse', motif_refus = p_motif, traite_par = (select auth.uid()), traite_le = now()
   where id = t.id;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  select (select auth.uid()), o, t.projet_id, 'transfert.refuse', t.org_cible::text,
         jsonb_build_object('transfert_id', t.id, 'motif', p_motif)
    from unnest(array[t.org_source, t.org_cible]) as o;
end $$;


ALTER FUNCTION bancarisation.refuser_transfert(p_transfert uuid, p_motif text) OWNER TO postgres;

--
-- Name: seuil(text); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.seuil(p_code text) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
  SELECT valeur FROM bancarisation.parametre_signal WHERE code = p_code
$$;


ALTER FUNCTION bancarisation.seuil(p_code text) OWNER TO postgres;

--
-- Name: tg_action_etape_before(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.tg_action_etape_before() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
BEGIN
  IF NEW.projet_id IS NULL THEN
    SELECT o.projet_id INTO NEW.projet_id
    FROM bancarisation.occurrence o WHERE o.id = NEW.occurrence_id;
  END IF;

  IF TG_OP = 'INSERT' THEN
    IF NEW.fait THEN NEW.fait_le := coalesce(NEW.fait_le, now()); END IF;
  ELSIF NEW.fait AND NOT OLD.fait THEN
    NEW.fait_le := coalesce(NEW.fait_le, now());
  ELSIF NOT NEW.fait AND OLD.fait THEN
    NEW.fait_le  := NULL;
    NEW.fait_par := NULL;
  END IF;

  NEW.updated_at := now();
  RETURN NEW;
END $$;


ALTER FUNCTION bancarisation.tg_action_etape_before() OWNER TO postgres;

--
-- Name: tg_note_interne_before(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.tg_note_interne_before() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'pg_temp'
    AS $$
BEGIN
  IF NEW.occurrence_id IS NOT NULL THEN
    SELECT o.projet_id INTO NEW.projet_id
    FROM bancarisation.occurrence o
    WHERE o.id = NEW.occurrence_id;
    IF NEW.projet_id IS NULL THEN
      RAISE EXCEPTION 'Occurrence % introuvable — projet_id de la note impossible à résoudre.', NEW.occurrence_id;
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.contenu IS DISTINCT FROM OLD.contenu THEN
    NEW.modifie_le := now();
  END IF;
  RETURN NEW;
END $$;


ALTER FUNCTION bancarisation.tg_note_interne_before() OWNER TO postgres;

--
-- Name: tg_occurrence_responsable_before(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.tg_occurrence_responsable_before() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  if new.projet_id is null then
    select o.projet_id into new.projet_id
    from bancarisation.occurrence o
    where o.id = new.occurrence_id;
  end if;
  return new;
end $$;


ALTER FUNCTION bancarisation.tg_occurrence_responsable_before() OWNER TO postgres;

--
-- Name: trg_credit_vente_affecte_terminal(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.trg_credit_vente_affecte_terminal() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
BEGIN
  IF OLD.statut = 'affecte' AND NEW.statut <> 'affecte' THEN
    RAISE EXCEPTION
      'Le statut affecte est terminal (vente %) : aucune reversion possible', OLD.id;
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION bancarisation.trg_credit_vente_affecte_terminal() OWNER TO postgres;

--
-- Name: trg_credit_vente_stock(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.trg_credit_vente_stock() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'public', 'extensions', 'pg_catalog'
    AS $$
DECLARE
  v_emise  numeric(12,4);
  v_vendue numeric(12,4);
BEGIN
  SELECT quantite_emise INTO v_emise
    FROM bancarisation.credit_lot WHERE id = NEW.lot_id;

  SELECT COALESCE(SUM(quantite), 0) INTO v_vendue
    FROM bancarisation.credit_vente
    WHERE lot_id = NEW.lot_id
      AND statut <> 'annule'
      AND id <> COALESCE(NEW.id, -1);

  IF (v_vendue + NEW.quantite) > v_emise THEN
    RAISE EXCEPTION
      'Survente du lot % : emis=%, deja engage=%, tentative=%',
      NEW.lot_id, v_emise, v_vendue, NEW.quantite;
  END IF;

  RETURN NEW;
END;
$$;


ALTER FUNCTION bancarisation.trg_credit_vente_stock() OWNER TO postgres;

--
-- Name: trg_organisation_closure(); Type: FUNCTION; Schema: bancarisation; Owner: postgres
--

CREATE FUNCTION bancarisation.trg_organisation_closure() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bancarisation', 'pg_catalog'
    AS $$
begin
  perform rebuild_organisation_closure();
  return null;
end;
$$;


ALTER FUNCTION bancarisation.trg_organisation_closure() OWNER TO postgres;

--
-- Name: acces_restreint(uuid); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.acces_restreint(p_projet uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  with mes_acces as (
    select pa.qualite
      from bancarisation.projet_acces pa
     where pa.projet_id = p_projet
       and (pa.expire_le is null or pa.expire_le > now())
       and (pa.utilisateur_id = (select auth.uid())
            or pa.organisation_id in (select organisation_id from prive.mes_appartenances()))
  )
  select not prive.est_interne(p_projet)
     and exists (select 1 from mes_acces)
     and not exists (
           select 1 from mes_acces
            where qualite is null or qualite not in ('maitre_ouvrage', 'prestataire'))
$$;


ALTER FUNCTION prive.acces_restreint(p_projet uuid) OWNER TO postgres;

--
-- Name: createur_gestionnaire(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.createur_gestionnaire() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
begin
  if (select auth.uid()) is not null then
    insert into bancarisation.projet_acces (projet_id, utilisateur_id, niveau, accorde_par)
    values (new.id, (select auth.uid()), 'gestionnaire', (select auth.uid()))
    on conflict do nothing;
  end if;
  return new;
end $$;


ALTER FUNCTION prive.createur_gestionnaire() OWNER TO postgres;

--
-- Name: creer_profil(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.creer_profil() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
begin
  insert into bancarisation.profils (utilisateur_id) values (new.id)
  on conflict do nothing;
  return new;
end $$;


ALTER FUNCTION prive.creer_profil() OWNER TO postgres;

--
-- Name: deriver_rattachement(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.deriver_rattachement() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $_$
declare
  v_cible  text := tg_argv[0];
  v_valeur uuid;
  v_fk     text;   -- texte : la clé parente peut être uuid, integer, bigint...
  v_type   text;
  i        int  := 1;
begin
  while i < tg_nargs and v_valeur is null loop
    v_fk := to_jsonb(new) ->> tg_argv[i + 1];
    if v_fk is not null then
      -- type réel de la colonne id du parent, pour garder l'usage de son index
      select format_type(a.atttypid, a.atttypmod) into v_type
        from pg_catalog.pg_attribute a
       where a.attrelid = format('bancarisation.%I', tg_argv[i])::regclass
         and a.attname = 'id';
      execute format('select %I from bancarisation.%I where id = $1::%s', v_cible, tg_argv[i], v_type)
        into v_valeur using v_fk;
    end if;
    i := i + 2;
  end loop;
  new := jsonb_populate_record(new, jsonb_build_object(v_cible, v_valeur));
  return new;
end $_$;


ALTER FUNCTION prive.deriver_rattachement() OWNER TO postgres;

--
-- Name: est_interne(uuid); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.est_interne(p_projet uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select exists (
           select 1 from bancarisation.projets p
             join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
            where p.id = p_projet)
     and prive.niveau_projet(p_projet) > 0
$$;


ALTER FUNCTION prive.est_interne(p_projet uuid) OWNER TO postgres;

--
-- Name: figer_organisation_projet(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.figer_organisation_projet() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO ''
    AS $$
begin
  if new.organisation_id is distinct from old.organisation_id and current_user = 'authenticated' then
    raise exception 'Changement d''organisation propriétaire interdit';
  end if;
  return new;
end $$;


ALTER FUNCTION prive.figer_organisation_projet() OWNER TO postgres;

--
-- Name: interdire_modification(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.interdire_modification() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  raise exception 'journal_audit est en écriture seule';
end $$;


ALTER FUNCTION prive.interdire_modification() OWNER TO postgres;

--
-- Name: journaliser(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.journaliser() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
declare
  r     jsonb := to_jsonb(coalesce(new, old));
  v_org uuid;
begin
  if r ? 'projet_id' then   -- partage : organisation propriétaire du projet
    select organisation_id into v_org from bancarisation.projets where id = (r ->> 'projet_id')::uuid;
  else
    v_org := (r ->> 'organisation_id')::uuid;
  end if;

  insert into bancarisation.journal_audit (acteur_id, organisation_id, projet_id, action, cible, details)
  values (
    (select auth.uid()),
    v_org,
    (r ->> 'projet_id')::uuid,
    tg_table_name || '.' || lower(tg_op),
    coalesce(r ->> 'utilisateur_id', r ->> 'organisation_id'),
    jsonb_build_object(
      'avant', case when tg_op <> 'INSERT' then to_jsonb(old) end,
      'apres', case when tg_op <> 'DELETE' then to_jsonb(new) end)
  );
  return coalesce(new, old);
end $$;


ALTER FUNCTION prive.journaliser() OWNER TO postgres;

--
-- Name: mes_appartenances(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.mes_appartenances() RETURNS TABLE(organisation_id uuid, role text, acces_global text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select c.descendant_id, m.role, m.acces_global
    from bancarisation.membre_organisation m
    join bancarisation.organisation_closure c
      on c.ancetre_id = m.organisation_id
     and (m.portee = 'branche' or c.profondeur = 0)
    join bancarisation.organisations o
      on o.id = m.organisation_id and o.statut = 'active'
   where m.utilisateur_id = (select auth.uid())
     and m.statut = 'actif'
$$;


ALTER FUNCTION prive.mes_appartenances() OWNER TO postgres;

--
-- Name: niveau_projet(uuid); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.niveau_projet(p_projet uuid) RETURNS integer
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select coalesce(max(n), 0) from (
    select case when a.role = 'admin' then 4 else prive.niveau_valeur(a.acces_global) end as n
      from bancarisation.projets p
      join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
     where p.id = p_projet
    union all
    select prive.niveau_valeur(pa.niveau)
      from bancarisation.projet_acces pa
     where pa.projet_id = p_projet
       and pa.utilisateur_id = (select auth.uid())
       and (pa.expire_le is null or pa.expire_le > now())
    union all
    select prive.niveau_valeur(pa.niveau)
      from bancarisation.projet_acces pa
      join prive.mes_appartenances() a on a.organisation_id = pa.organisation_id
     where pa.projet_id = p_projet
       and (pa.expire_le is null or pa.expire_le > now())
  ) s
$$;


ALTER FUNCTION prive.niveau_projet(p_projet uuid) OWNER TO postgres;

--
-- Name: niveau_valeur(text); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.niveau_valeur(p_niveau text) RETURNS integer
    LANGUAGE sql IMMUTABLE
    SET search_path TO ''
    AS $$
  select case p_niveau when 'gestionnaire' then 4 when 'contributeur' then 3 when 'lecteur' then 2 end
$$;


ALTER FUNCTION prive.niveau_valeur(p_niveau text) OWNER TO postgres;

--
-- Name: note_interne_organisation(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.note_interne_organisation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO ''
    AS $$
begin
  if tg_op = 'INSERT' then
    if new.organisation_id is null then
      new.organisation_id := prive.organisation_auteur(new.projet_id);
    end if;
  else
    new.organisation_id := old.organisation_id;   -- une note ne change jamais d'organisation
  end if;
  return new;
end $$;


ALTER FUNCTION prive.note_interne_organisation() OWNER TO postgres;

--
-- Name: occurrence_finance_projet(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.occurrence_finance_projet() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $$
begin
  select o.projet_id into new.projet_id
    from bancarisation.occurrence o
   where o.id = new.occurrence_id;
  if new.projet_id is null then
    raise exception 'Occurrence % introuvable', new.occurrence_id
      using errcode = '23503';
  end if;
  new.modifie_le := now();
  return new;
end $$;


ALTER FUNCTION prive.occurrence_finance_projet() OWNER TO postgres;

--
-- Name: organisation_auteur(uuid); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.organisation_auteur(p_projet uuid) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select coalesce(
    -- membre de l'organisation propriétaire (ou de sa branche)
    (select p.organisation_id
       from bancarisation.projets p
       join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
      where p.id = p_projet
      limit 1),
    -- sinon, l'organisation par laquelle j'ai accès au projet
    (select pa.organisation_id
       from bancarisation.projet_acces pa
       join prive.mes_appartenances() a on a.organisation_id = pa.organisation_id
      where pa.projet_id = p_projet
        and (pa.expire_le is null or pa.expire_le > now())
      order by prive.niveau_valeur(pa.niveau) desc
      limit 1))
$$;


ALTER FUNCTION prive.organisation_auteur(p_projet uuid) OWNER TO postgres;

--
-- Name: organisations_lisibles(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.organisations_lisibles() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select organisation_id from prive.mes_appartenances()
  union
  select p.organisation_id from bancarisation.projets p
   where p.id in (select prive.projets_lisibles())
  union
  select pa.organisation_id from bancarisation.projet_acces pa
   where pa.organisation_id is not null
     and pa.projet_id in (select prive.projets_lisibles())
$$;


ALTER FUNCTION prive.organisations_lisibles() OWNER TO postgres;

--
-- Name: peut_partager(uuid); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.peut_partager(p_projet uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select prive.niveau_projet(p_projet) = 4 and prive.est_interne(p_projet)
$$;


ALTER FUNCTION prive.peut_partager(p_projet uuid) OWNER TO postgres;

--
-- Name: projets_lisibles(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.projets_lisibles() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select p.id
    from bancarisation.projets p
    join prive.mes_appartenances() a on a.organisation_id = p.organisation_id
   where a.role = 'admin' or a.acces_global is not null or p.cree_par = (select auth.uid())
  union
  select pa.projet_id
    from bancarisation.projet_acces pa
   where pa.utilisateur_id = (select auth.uid())
     and (pa.expire_le is null or pa.expire_le > now())
  union
  select pa.projet_id
    from bancarisation.projet_acces pa
    join prive.mes_appartenances() a on a.organisation_id = pa.organisation_id
   where pa.expire_le is null or pa.expire_le > now()
$$;


ALTER FUNCTION prive.projets_lisibles() OWNER TO postgres;

--
-- Name: renseigner_createur(); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.renseigner_createur() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO ''
    AS $$
begin
  new.cree_par := coalesce(new.cree_par, (select auth.uid()));
  return new;
end $$;


ALTER FUNCTION prive.renseigner_createur() OWNER TO postgres;

--
-- Name: role_org(uuid); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.role_org(p_org uuid) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select case when bool_or(role = 'admin') then 'admin'
              when count(*) > 0 then 'membre' end
    from prive.mes_appartenances()
   where organisation_id = p_org
$$;


ALTER FUNCTION prive.role_org(p_org uuid) OWNER TO postgres;

--
-- Name: voit_finances(uuid); Type: FUNCTION; Schema: prive; Owner: postgres
--

CREATE FUNCTION prive.voit_finances(p_projet uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO ''
    AS $$
  select prive.est_interne(p_projet)
      or exists (
           select 1 from bancarisation.projet_acces pa
            where pa.projet_id = p_projet
              and pa.voir_finances
              and (pa.expire_le is null or pa.expire_le > now())
              and (pa.utilisateur_id = (select auth.uid())
                   or pa.organisation_id in (select organisation_id from prive.mes_appartenances())))
$$;


ALTER FUNCTION prive.voit_finances(p_projet uuid) OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: acte_dreal; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.acte_dreal (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    type text NOT NULL,
    statut text DEFAULT 'brouillon'::text NOT NULL,
    objet text,
    contenu jsonb,
    delai_reponse date,
    emis_le timestamp with time zone,
    clos_le timestamp with time zone,
    document_id uuid,
    demande_id uuid,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT acte_dreal_statut_check CHECK ((statut = ANY (ARRAY['brouillon'::text, 'emis'::text, 'clos'::text]))),
    CONSTRAINT acte_dreal_type_check CHECK ((type = ANY (ARRAY['validation'::text, 'demande_complement'::text, 'mise_en_demeure'::text, 'arrete_modificatif'::text])))
);

ALTER TABLE ONLY bancarisation.acte_dreal FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.acte_dreal OWNER TO postgres;

--
-- Name: action_etape; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.action_etape (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurrence_id uuid NOT NULL,
    projet_id uuid NOT NULL,
    ordre integer NOT NULL,
    libelle text NOT NULL,
    description text,
    assigne_a uuid,
    echeance date,
    fait boolean DEFAULT false NOT NULL,
    fait_le timestamp with time zone,
    fait_par uuid,
    modele_etape_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT action_etape_libelle_check CHECK ((length(TRIM(BOTH FROM libelle)) > 0))
);

ALTER TABLE ONLY bancarisation.action_etape FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.action_etape OWNER TO postgres;

--
-- Name: TABLE action_etape; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.action_etape IS 'Checklist d''une action. Ne mute jamais occurrence.statut.';


--
-- Name: COLUMN action_etape.occurrence_id; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.action_etape.occurrence_id IS 'FK NO ACTION : un ré-import ne doit pas supprimer silencieusement les étapes.';


--
-- Name: action_fiche; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.action_fiche (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    import_id uuid,
    cle text NOT NULL,
    code text NOT NULL,
    categorie text NOT NULL,
    titre text NOT NULL,
    contenu_integral text NOT NULL,
    fiche_json jsonb NOT NULL,
    confiance double precision,
    champs_a_confirmer text[] DEFAULT '{}'::text[] NOT NULL,
    avertissements text[] DEFAULT '{}'::text[] NOT NULL,
    ug_ids text[] DEFAULT '{}'::text[] NOT NULL,
    lib_thema text DEFAULT 'autre'::text NOT NULL
);

ALTER TABLE ONLY bancarisation.action_fiche FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.action_fiche OWNER TO postgres;

--
-- Name: action_fiche_arrete; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.action_fiche_arrete (
    action_fiche_id uuid NOT NULL,
    arrete_id uuid NOT NULL,
    projet_id uuid
);

ALTER TABLE ONLY bancarisation.action_fiche_arrete FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.action_fiche_arrete OWNER TO postgres;

--
-- Name: annotation_terrain; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.annotation_terrain (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    ug_id text,
    note text DEFAULT ''::text NOT NULL,
    titre text,
    geom public.geometry(Geometry,4326) NOT NULL,
    document_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    auteur text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_kind text DEFAULT 'draw'::text NOT NULL,
    source_geom_key text NOT NULL,
    source_annotation_id uuid,
    source_ug_geom_id text,
    observed_at date DEFAULT CURRENT_DATE NOT NULL,
    CONSTRAINT annotation_terrain_source_kind_check CHECK ((source_kind = ANY (ARRAY['draw'::text, 'import'::text, 'ug'::text, 'annotation'::text])))
);

ALTER TABLE ONLY bancarisation.annotation_terrain FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.annotation_terrain OWNER TO postgres;

--
-- Name: TABLE annotation_terrain; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.annotation_terrain IS 'Notes / photos terrain géolocalisées (géométrie quelconque EPSG:4326) pour le poste de travail carto.';


--
-- Name: COLUMN annotation_terrain.geom; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.annotation_terrain.geom IS 'Emprise de la note (EPSG:4326) : Point, MultiPoint, LineString, MultiLineString, Polygon, MultiPolygon ou GeometryCollection.';


--
-- Name: COLUMN annotation_terrain.source_geom_key; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.annotation_terrain.source_geom_key IS 'Clé de lignée : notes partageant la même emprise (historique).';


--
-- Name: COLUMN annotation_terrain.observed_at; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.annotation_terrain.observed_at IS 'Date métier de la note (visite / opération), distincte de created_at.';


--
-- Name: arrete; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.arrete (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    type text NOT NULL,
    reference text,
    autorite text,
    beneficiaire text,
    numero_dossier text,
    date_signature date,
    date_notification date,
    document_id uuid,
    extraction jsonb,
    extraction_modele text,
    confiance numeric,
    origine text DEFAULT 'ia'::text NOT NULL,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    modifie_le timestamp with time zone,
    CONSTRAINT arrete_origine_check CHECK ((origine = ANY (ARRAY['ia'::text, 'user'::text]))),
    CONSTRAINT arrete_type_check CHECK ((type = ANY (ARRAY['declaration_loi_eau'::text, 'autorisation_env'::text, 'derogation_ep'::text, 'arrete_modificatif'::text, 'autre'::text])))
);

ALTER TABLE ONLY bancarisation.arrete FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.arrete OWNER TO postgres;

--
-- Name: arrete_prescription; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.arrete_prescription (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    arrete_id uuid NOT NULL,
    action_fiche_id uuid,
    article text,
    intitule text NOT NULL,
    categorie text,
    nature text,
    cible_valeur numeric,
    cible_unite text,
    echeance date,
    recurrence jsonb,
    page_source integer,
    texte_source text,
    confiance numeric,
    origine text DEFAULT 'ia'::text NOT NULL,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    modifie_le timestamp with time zone,
    code text,
    opposable boolean DEFAULT true NOT NULL,
    phase text,
    destinataire text,
    livrable text,
    indicateur text,
    obligation_de_resultat boolean DEFAULT false NOT NULL,
    temporalite jsonb,
    autorite_destinataire jsonb,
    remarque text,
    projet_id uuid,
    CONSTRAINT arrete_prescription_categorie_check CHECK (((categorie IS NULL) OR (categorie = ANY (ARRAY['compensation'::text, 'evitement'::text, 'reduction'::text, 'accompagnement'::text, 'suivi'::text, 'chantier'::text, 'administratif'::text, 'gouvernance'::text, 'information'::text, 'donnees'::text])))),
    CONSTRAINT arrete_prescription_nature_check CHECK ((nature = ANY (ARRAY['calendaire'::text, 'recurrente'::text, 'permanente'::text, 'ponctuelle'::text, 'seuil'::text]))),
    CONSTRAINT arrete_prescription_origine_check CHECK ((origine = ANY (ARRAY['ia'::text, 'user'::text])))
);

ALTER TABLE ONLY bancarisation.arrete_prescription FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.arrete_prescription OWNER TO postgres;

--
-- Name: COLUMN arrete_prescription.code; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.arrete_prescription.code IS 'Identifiant court stable issu de l''extraction (ex. ART3-P2).';


--
-- Name: COLUMN arrete_prescription.opposable; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.arrete_prescription.opposable IS 'False pour les articles procéduraux (recours, droits des tiers, publication).';


--
-- Name: COLUMN arrete_prescription.temporalite; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.arrete_prescription.temporalite IS 'Règle réglementaire extraite (délai relatif, récurrence, paliers, durée). Ne jamais y lire une date d''occurrence calendaire.';


--
-- Name: bilan_suivi; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.bilan_suivi (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    annee integer NOT NULL,
    statut text DEFAULT 'attendu'::text NOT NULL,
    depose_le timestamp with time zone,
    statue_le timestamp with time zone,
    document_id uuid,
    commentaire_dreal text,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    rapport_suivi_id uuid,
    CONSTRAINT bilan_suivi_statut_check CHECK ((statut = ANY (ARRAY['attendu'::text, 'depose'::text, 'en_relecture'::text, 'complement_demande'::text, 'valide'::text, 'rejete'::text])))
);

ALTER TABLE ONLY bancarisation.bilan_suivi FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.bilan_suivi OWNER TO postgres;

--
-- Name: budget_baseline; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.budget_baseline (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    figee_le timestamp with time zone DEFAULT now() NOT NULL,
    libelle text DEFAULT 'Baseline'::text NOT NULL,
    commentaire text,
    mode text DEFAULT 'completer'::text NOT NULL,
    nb_occurrences integer NOT NULL,
    total_ht numeric NOT NULL
);

ALTER TABLE ONLY bancarisation.budget_baseline FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.budget_baseline OWNER TO postgres;

--
-- Name: budget_import; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.budget_import (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    fichier_nom text DEFAULT 'estimation'::text NOT NULL,
    fichier_hash text,
    modele_llm text,
    devise text DEFAULT 'EUR'::text NOT NULL,
    nb_lignes integer DEFAULT 0 NOT NULL,
    nb_totaux integer DEFAULT 0 NOT NULL,
    nb_avertissements integer DEFAULT 0 NOT NULL,
    cartographie_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    avertissements jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.budget_import FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.budget_import OWNER TO postgres;

--
-- Name: budget_mouvement; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.budget_mouvement (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurrence_id uuid NOT NULL,
    projet_id uuid NOT NULL,
    champ text NOT NULL,
    ancienne_val text,
    nouvelle_val text,
    motif text,
    modifie_par text,
    modifie_le timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.budget_mouvement FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.budget_mouvement OWNER TO postgres;

--
-- Name: cadastre_parcelle; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.cadastre_parcelle (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    ug_id text NOT NULL,
    idu text NOT NULL,
    section text,
    numero text,
    code_insee text,
    nom_com text,
    contenance integer,
    geom public.geometry(MultiPolygon,4326),
    geom_3857 public.geometry(MultiPolygon,3857) NOT NULL,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    buffer_m numeric,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.cadastre_parcelle FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.cadastre_parcelle OWNER TO postgres;

--
-- Name: TABLE cadastre_parcelle; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.cadastre_parcelle IS 'Parcelles cadastrales IGN (API Carto) dans un buffer autour de chaque UG.';


--
-- Name: cadastre_parcelle_ug; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.cadastre_parcelle_ug (
    projet_id uuid NOT NULL,
    ug_id text NOT NULL,
    idu text NOT NULL,
    section text,
    numero text,
    code_insee text,
    nom_com text,
    contenance integer,
    surface_inter_m2 numeric,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.cadastre_parcelle_ug FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.cadastre_parcelle_ug OWNER TO postgres;

--
-- Name: TABLE cadastre_parcelle_ug; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.cadastre_parcelle_ug IS 'Croisement spatial : parcelles IGN qui intersectent chaque UG (composition cadastrale).';


--
-- Name: checklist_modele; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.checklist_modele (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organisation_id uuid,
    nom text NOT NULL,
    description text,
    archive boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.checklist_modele FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.checklist_modele OWNER TO postgres;

--
-- Name: checklist_modele_etape; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.checklist_modele_etape (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    modele_id uuid NOT NULL,
    ordre integer NOT NULL,
    libelle text NOT NULL,
    description text,
    role_code text,
    organisation_id uuid
);

ALTER TABLE ONLY bancarisation.checklist_modele_etape FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.checklist_modele_etape OWNER TO postgres;

--
-- Name: constat_controle; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.constat_controle (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_fiche_id uuid,
    occurrence_id uuid,
    date_constat date DEFAULT CURRENT_DATE NOT NULL,
    auteur text,
    mode text NOT NULL,
    conformite text NOT NULL,
    commentaire text,
    documents jsonb,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    projet_id uuid,
    CONSTRAINT constat_cible_non_vide CHECK (((action_fiche_id IS NOT NULL) OR (occurrence_id IS NOT NULL))),
    CONSTRAINT constat_controle_conformite_check CHECK ((conformite = ANY (ARRAY['conforme'::text, 'reserve'::text, 'non_conforme'::text]))),
    CONSTRAINT constat_controle_mode_check CHECK ((mode = ANY (ARRAY['sur_piece'::text, 'sur_place'::text])))
);

ALTER TABLE ONLY bancarisation.constat_controle FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.constat_controle OWNER TO postgres;

--
-- Name: constat_gain; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.constat_gain (
    id bigint NOT NULL,
    lot_id bigint NOT NULL,
    annee_palier integer,
    libelle_palier text,
    date_campagne date NOT NULL,
    statut bancarisation.statut_constat DEFAULT 'non_evalue'::bancarisation.statut_constat NOT NULL,
    source bancarisation.source_constat DEFAULT 'inventaire'::bancarisation.source_constat NOT NULL,
    constat_controle_id uuid,
    rapport_doc_id uuid,
    commentaire text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    projet_id uuid
);

ALTER TABLE ONLY bancarisation.constat_gain FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.constat_gain OWNER TO postgres;

--
-- Name: constat_gain_id_seq; Type: SEQUENCE; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.constat_gain ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME bancarisation.constat_gain_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: couche_non_georeferencee; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.couche_non_georeferencee (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    depot_id text NOT NULL,
    couche text NOT NULL,
    fichier text NOT NULL,
    document_id uuid,
    nb_entites integer,
    epsg_propose integer,
    statut text DEFAULT 'en_attente_epsg'::text NOT NULL,
    motif text DEFAULT 'couche non géoréférencée'::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT couche_non_georeferencee_statut_check CHECK ((statut = ANY (ARRAY['en_attente_epsg'::text, 'reprise'::text, 'echec'::text])))
);

ALTER TABLE ONLY bancarisation.couche_non_georeferencee FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.couche_non_georeferencee OWNER TO postgres;

--
-- Name: TABLE couche_non_georeferencee; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.couche_non_georeferencee IS 'Couche SIG déposée sans CRS : fichier en bucket, EPSG à saisir pour relancer l''écriture.';


--
-- Name: credit_lot; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.credit_lot (
    id bigint NOT NULL,
    site_agrement_id bigint NOT NULL,
    libelle text,
    composante_milieu text,
    methode_calcul text,
    unite text DEFAULT 'UCRR'::text NOT NULL,
    quantite_emise numeric(12,4) NOT NULL,
    date_emission date,
    ug_codes text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    projet_id uuid,
    CONSTRAINT credit_lot_quantite_emise_check CHECK ((quantite_emise > (0)::numeric))
);

ALTER TABLE ONLY bancarisation.credit_lot FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.credit_lot OWNER TO postgres;

--
-- Name: credit_lot_id_seq; Type: SEQUENCE; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.credit_lot ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME bancarisation.credit_lot_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: credit_palier; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.credit_palier (
    id bigint NOT NULL,
    lot_id bigint NOT NULL,
    annee_palier integer NOT NULL,
    libelle text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    projet_id uuid
);

ALTER TABLE ONLY bancarisation.credit_palier FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.credit_palier OWNER TO postgres;

--
-- Name: credit_palier_id_seq; Type: SEQUENCE; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.credit_palier ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME bancarisation.credit_palier_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: credit_vente; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.credit_vente (
    id bigint NOT NULL,
    lot_id bigint NOT NULL,
    quantite numeric(12,4) NOT NULL,
    usage_credit bancarisation.usage_credit NOT NULL,
    acheteur_nom text,
    acheteur_siren text,
    arrete_acheteur text,
    prix_unitaire_ht numeric(12,2),
    prix_total_ht numeric(12,2),
    date_reservation date,
    date_vente date,
    date_affectation date,
    certificat_doc_id uuid,
    statut bancarisation.statut_vente DEFAULT 'reserve'::bancarisation.statut_vente NOT NULL,
    annee_encaissement integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    projet_id uuid,
    CONSTRAINT credit_vente_quantite_check CHECK ((quantite > (0)::numeric))
);

ALTER TABLE ONLY bancarisation.credit_vente FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.credit_vente OWNER TO postgres;

--
-- Name: credit_vente_id_seq; Type: SEQUENCE; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.credit_vente ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME bancarisation.credit_vente_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: demande; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.demande (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    occurrence_id uuid,
    annee integer,
    signal_code text,
    objet text NOT NULL,
    statut text DEFAULT 'ouverte'::text NOT NULL,
    auteur text DEFAULT 'dreal'::text NOT NULL,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    maj_le timestamp with time zone DEFAULT now() NOT NULL,
    clos_le timestamp with time zone,
    CONSTRAINT demande_auteur_chk CHECK ((auteur = ANY (ARRAY['dreal'::text, 'be'::text]))),
    CONSTRAINT demande_statut_chk CHECK ((statut = ANY (ARRAY['ouverte'::text, 'repondue'::text, 'close'::text])))
);

ALTER TABLE ONLY bancarisation.demande FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.demande OWNER TO postgres;

--
-- Name: demande_message; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.demande_message (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    demande_id uuid NOT NULL,
    acteur text NOT NULL,
    auteur_nom text,
    corps text NOT NULL,
    documents_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    origine text DEFAULT 'application'::text NOT NULL,
    email_message_id text,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    projet_id uuid,
    CONSTRAINT demande_message_acteur_chk CHECK ((acteur = ANY (ARRAY['dreal'::text, 'be'::text]))),
    CONSTRAINT demande_message_origine_chk CHECK ((origine = ANY (ARRAY['application'::text, 'email'::text])))
);

ALTER TABLE ONLY bancarisation.demande_message FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.demande_message OWNER TO postgres;

--
-- Name: documents; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    occurrence_id uuid,
    nom text NOT NULL,
    nom_fichier text,
    bucket_path text NOT NULL,
    taille_octets bigint,
    type_mime text,
    categorie text DEFAULT 'technique'::text NOT NULL,
    date_document date,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sous_type text
);

ALTER TABLE ONLY bancarisation.documents FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.documents OWNER TO postgres;

--
-- Name: droit_documents; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.droit_documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    droit_id uuid NOT NULL,
    document_id uuid NOT NULL,
    type_piece text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    organisation_id uuid
);

ALTER TABLE ONLY bancarisation.droit_documents FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.droit_documents OWNER TO postgres;

--
-- Name: droits_fonciers; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.droits_fonciers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organisation_id uuid NOT NULL,
    parcelle_id uuid NOT NULL,
    nature text NOT NULL,
    titulaire_id uuid,
    constituant_id uuid,
    quote_part text,
    indivision boolean DEFAULT false NOT NULL,
    nb_indivisaires integer,
    tous_signataires boolean,
    date_debut date,
    date_fin date,
    duree_annees integer,
    tacite_reconduction boolean DEFAULT false NOT NULL,
    statut text DEFAULT 'identifie'::text NOT NULL,
    publie_spf boolean DEFAULT false NOT NULL,
    reference_publication text,
    accord_preneur boolean,
    observations text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    assiette text DEFAULT 'parcelle_entiere'::text NOT NULL,
    ug_code text,
    CONSTRAINT droits_fonciers_assiette_check CHECK ((assiette = ANY (ARRAY['parcelle_entiere'::text, 'concerne_ug'::text, 'hors_ug'::text]))),
    CONSTRAINT droits_fonciers_nature_check CHECK ((nature = ANY (ARRAY['propriete'::text, 'usufruit'::text, 'nue_propriete'::text, 'bail_rural'::text, 'bail_rural_clauses_env'::text, 'bail_emphyteotique'::text, 'ore'::text, 'convention_gestion'::text, 'cmd_safer'::text, 'convention_paturage'::text, 'commodat'::text, 'aot'::text, 'servitude'::text, 'droit_chasse'::text, 'regime_forestier'::text, 'occupation_sans_titre'::text, 'autre'::text]))),
    CONSTRAINT droits_fonciers_statut_check CHECK ((statut = ANY (ARRAY['identifie'::text, 'en_negociation'::text, 'signe'::text, 'publie'::text, 'expire'::text, 'resilie'::text])))
);

ALTER TABLE ONLY bancarisation.droits_fonciers FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.droits_fonciers OWNER TO postgres;

--
-- Name: COLUMN droits_fonciers.assiette; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.droits_fonciers.assiette IS 'Périmètre du titre : parcelle entière, intersection avec une UG, ou reste hors UG.';


--
-- Name: COLUMN droits_fonciers.ug_code; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.droits_fonciers.ug_code IS 'Code UG (ug1, …) quand assiette = concerne_ug.';


--
-- Name: echeance; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.echeance (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    import_id uuid,
    cle text NOT NULL,
    action_cle text,
    code_operation text NOT NULL,
    type_operation text NOT NULL,
    type_metier text NOT NULL,
    libelle text NOT NULL,
    objectif_long_terme text,
    objectif_operationnel text,
    ug_ids text[] DEFAULT '{}'::text[] NOT NULL,
    parcelles text[] DEFAULT '{}'::text[] NOT NULL,
    communes text[] DEFAULT '{}'::text[] NOT NULL,
    recurrence jsonb NOT NULL,
    fenetre_debut text,
    fenetre_fin text,
    fenetre_traverse_nouvel_an boolean DEFAULT false NOT NULL,
    fenetre_texte_source text,
    conditions text[] DEFAULT '{}'::text[] NOT NULL,
    indicateurs text[] DEFAULT '{}'::text[] NOT NULL,
    intervenants text[] DEFAULT '{}'::text[] NOT NULL,
    duree_gestion_ans integer,
    source_page integer,
    source_extrait text,
    confiance double precision NOT NULL,
    champs_a_confirmer text[] DEFAULT '{}'::text[] NOT NULL,
    avertissements text[] DEFAULT '{}'::text[] NOT NULL,
    lib_thema text DEFAULT 'autre'::text NOT NULL
);

ALTER TABLE ONLY bancarisation.echeance FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.echeance OWNER TO postgres;

--
-- Name: emprise_projet; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.emprise_projet (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    libelle text DEFAULT 'Emprise projet'::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    geom public.geometry(MultiPolygon),
    geom_3857 public.geometry(MultiPolygon,3857) NOT NULL,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_fichier text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    attributs jsonb DEFAULT '[]'::jsonb NOT NULL,
    categorie_erc text
);

ALTER TABLE ONLY bancarisation.emprise_projet FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.emprise_projet OWNER TO postgres;

--
-- Name: COLUMN emprise_projet.attributs; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.emprise_projet.attributs IS 'Table attributaire source (lignes = features du .shp / couche), sans géométrie.';


--
-- Name: export_geomce; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.export_geomce (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    cree_par text,
    mode text NOT NULL,
    strategie_geom text NOT NULL,
    srid integer NOT NULL,
    nb_polygones integer NOT NULL,
    surface_ha numeric,
    geom_hash text NOT NULL,
    attributs jsonb NOT NULL,
    nom_fichier text NOT NULL,
    storage_path text NOT NULL,
    statut text DEFAULT 'genere'::text NOT NULL,
    verse_le date,
    commentaire text,
    CONSTRAINT export_geomce_mode_check CHECK ((mode = ANY (ARRAY['complet'::text, 'geometrie_seule'::text]))),
    CONSTRAINT export_geomce_statut_check CHECK ((statut = ANY (ARRAY['genere'::text, 'verse'::text, 'abandonne'::text]))),
    CONSTRAINT export_geomce_strategie_geom_check CHECK ((strategie_geom = ANY (ARRAY['multipart'::text, 'eclate'::text])))
);

ALTER TABLE ONLY bancarisation.export_geomce FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.export_geomce OWNER TO postgres;

--
-- Name: export_projet; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.export_projet (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    organisation_id uuid NOT NULL,
    demande_par uuid NOT NULL,
    options jsonb DEFAULT '{}'::jsonb NOT NULL,
    statut text DEFAULT 'en_attente'::text NOT NULL,
    progression jsonb DEFAULT '{}'::jsonb NOT NULL,
    storage_path text,
    taille_octets bigint,
    sha256 text,
    manifest jsonb,
    erreur text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    termine_le timestamp with time zone,
    expire_le timestamp with time zone,
    nb_telechargements integer DEFAULT 0 NOT NULL,
    dernier_telechargement_le timestamp with time zone,
    CONSTRAINT export_projet_statut_check CHECK ((statut = ANY (ARRAY['en_attente'::text, 'en_cours'::text, 'termine'::text, 'termine_avec_avertissements'::text, 'echec'::text, 'expire'::text])))
);

ALTER TABLE ONLY bancarisation.export_projet FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.export_projet OWNER TO postgres;

--
-- Name: TABLE export_projet; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.export_projet IS 'Journal des exports complets. La ligne reste après expiration du ZIP.';


--
-- Name: COLUMN export_projet.organisation_id; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.export_projet.organisation_id IS 'Organisation propriétaire du projet au moment de la demande.';


--
-- Name: COLUMN export_projet.sha256; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.export_projet.sha256 IS 'Empreinte SHA-256 du ZIP. Non modifiable par authenticated.';


--
-- Name: extraction_import; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.extraction_import (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    fichier_nom text NOT NULL,
    fichier_hash text,
    modele_ocr text,
    modele_llm text,
    nb_metadata integer DEFAULT 0 NOT NULL,
    nb_actions integer DEFAULT 0 NOT NULL,
    nb_echeances integer DEFAULT 0 NOT NULL,
    nb_occurrences integer DEFAULT 0 NOT NULL,
    nb_non_placables integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.extraction_import FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.extraction_import OWNER TO postgres;

--
-- Name: geomce_communes; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.geomce_communes (
    identifiant integer,
    projet text,
    theme text,
    maitre_ouvrage text,
    liste_communes text,
    type_procedure text,
    date_decision timestamp without time zone,
    classe text,
    type text,
    categorie text,
    sous_categorie text,
    duree text,
    origine_si text,
    dossier_no text,
    l_dep text,
    geom_2154 public.geometry(MultiPolygon,2154),
    geom_3857 public.geometry(Geometry,3857)
);

ALTER TABLE ONLY bancarisation.geomce_communes FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.geomce_communes OWNER TO postgres;

--
-- Name: geomce_lin; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.geomce_lin (
    identifiant integer,
    projet text,
    theme text,
    maitre_ouvrage text,
    liste_communes text,
    type_procedure text,
    date_decision timestamp without time zone,
    classe text,
    type text,
    categorie text,
    sous_categorie text,
    duree text,
    origine_si text,
    dossier_no text,
    l_dep text,
    geom_2154 public.geometry(MultiLineString,2154),
    geom_3857 public.geometry(Geometry,3857)
);

ALTER TABLE ONLY bancarisation.geomce_lin FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.geomce_lin OWNER TO postgres;

--
-- Name: geomce_pct; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.geomce_pct (
    identifiant integer,
    projet text,
    theme text,
    maitre_ouvrage text,
    liste_communes text,
    type_procedure text,
    date_decision timestamp without time zone,
    classe text,
    type text,
    categorie text,
    sous_categorie text,
    duree text,
    origine_si text,
    dossier_no text,
    l_dep text,
    geom_2154 public.geometry(MultiPoint,2154),
    geom_3857 public.geometry(Geometry,3857)
);

ALTER TABLE ONLY bancarisation.geomce_pct FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.geomce_pct OWNER TO postgres;

--
-- Name: geomce_surf; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.geomce_surf (
    identifiant integer,
    projet text,
    theme text,
    maitre_ouvrage text,
    liste_communes text,
    type_procedure text,
    date_decision timestamp without time zone,
    classe text,
    type text,
    categorie text,
    sous_categorie text,
    duree text,
    origine_si text,
    dossier_no text,
    l_dep text,
    geom_2154 public.geometry(MultiPolygon,2154),
    geom_3857 public.geometry(Geometry,3857)
);

ALTER TABLE ONLY bancarisation.geomce_surf FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.geomce_surf OWNER TO postgres;

--
-- Name: geometrie_mouvement; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.geometrie_mouvement (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    table_source text NOT NULL,
    entite_id uuid NOT NULL,
    ancien_statut text,
    nouveau_statut text,
    ancien_ug_id text,
    nouveau_ug_id text,
    motif text,
    auteur text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT geometrie_mouvement_table_source_check CHECK ((table_source = ANY (ARRAY['surf'::text, 'lin'::text, 'pct'::text])))
);

ALTER TABLE ONLY bancarisation.geometrie_mouvement FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.geometrie_mouvement OWNER TO postgres;

--
-- Name: TABLE geometrie_mouvement; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.geometrie_mouvement IS 'Journal append-only des changements de rattachement d''une entité géométrique.';


--
-- Name: invitation; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.invitation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    organisation_id uuid NOT NULL,
    role text,
    portee text DEFAULT 'entite'::text NOT NULL,
    utilisateur_id uuid,
    invite_par uuid,
    nouveau_compte boolean NOT NULL,
    revoquee_le timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    type text DEFAULT 'bureau'::text NOT NULL,
    projets jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT invitation_portee_check CHECK ((portee = ANY (ARRAY['entite'::text, 'branche'::text]))),
    CONSTRAINT invitation_role_check CHECK (((role IS NULL) OR (role = ANY (ARRAY['admin'::text, 'membre'::text])))),
    CONSTRAINT invitation_type_check CHECK ((type = ANY (ARRAY['bureau'::text, 'partenaire'::text])))
);

ALTER TABLE ONLY bancarisation.invitation FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.invitation OWNER TO postgres;

--
-- Name: journal_actions; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.journal_actions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid,
    acteur text,
    action text NOT NULL,
    cible_type text,
    cible_id text,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    cree_le timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.journal_actions FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.journal_actions OWNER TO postgres;

--
-- Name: journal_audit; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.journal_audit (
    id bigint NOT NULL,
    horodatage timestamp with time zone DEFAULT now() NOT NULL,
    acteur_id uuid,
    organisation_id uuid,
    projet_id uuid,
    action text NOT NULL,
    cible text,
    details jsonb DEFAULT '{}'::jsonb NOT NULL
);

ALTER TABLE ONLY bancarisation.journal_audit FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.journal_audit OWNER TO postgres;

--
-- Name: journal_audit_id_seq; Type: SEQUENCE; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.journal_audit ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME bancarisation.journal_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ligne_budget; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.ligne_budget (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    import_id uuid,
    libelle_prestation text NOT NULL,
    libelle_action text,
    code_mesure text,
    prestataire text,
    montant_ht numeric,
    montant_ttc numeric,
    taux_tva numeric,
    unite text,
    quantite numeric,
    nb_campagnes integer,
    annees jsonb DEFAULT '{}'::jsonb NOT NULL,
    ug_ids text[] DEFAULT '{}'::text[] NOT NULL,
    est_total boolean DEFAULT false NOT NULL,
    statut_reel text,
    action_associee text,
    confiance double precision,
    champs_a_confirmer text[] DEFAULT '{}'::text[] NOT NULL,
    source_feuille text,
    source_lignes integer[] DEFAULT '{}'::integer[] NOT NULL,
    ligne_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.ligne_budget FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.ligne_budget OWNER TO postgres;

--
-- Name: membre; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.membre (
    user_id uuid NOT NULL,
    organisation_id uuid,
    role text NOT NULL,
    actif boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT membre_org_coherence_chk CHECK ((((role = 'operateur'::text) AND (organisation_id IS NOT NULL)) OR (role <> 'operateur'::text))),
    CONSTRAINT membre_role_chk CHECK ((role = ANY (ARRAY['controleur'::text, 'operateur'::text, 'admin'::text])))
);

ALTER TABLE ONLY bancarisation.membre FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.membre OWNER TO postgres;

--
-- Name: membre_organisation; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.membre_organisation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    utilisateur_id uuid NOT NULL,
    organisation_id uuid NOT NULL,
    role text DEFAULT 'membre'::text NOT NULL,
    portee text DEFAULT 'entite'::text NOT NULL,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    acces_global text,
    statut text DEFAULT 'invite'::text NOT NULL,
    invite_par uuid,
    CONSTRAINT membre_organisation_acces_global_check CHECK ((acces_global = ANY (ARRAY['lecteur'::text, 'contributeur'::text, 'gestionnaire'::text]))),
    CONSTRAINT membre_organisation_portee_check CHECK ((portee = ANY (ARRAY['entite'::text, 'branche'::text]))),
    CONSTRAINT membre_organisation_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'membre'::text]))),
    CONSTRAINT membre_organisation_statut_check CHECK ((statut = ANY (ARRAY['invite'::text, 'actif'::text, 'suspendu'::text])))
);

ALTER TABLE ONLY bancarisation.membre_organisation FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.membre_organisation OWNER TO postgres;

--
-- Name: note_interne; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.note_interne (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    occurrence_id uuid,
    auteur_id uuid,
    contenu text NOT NULL,
    mentions uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    document_id uuid,
    etape_id uuid,
    epinglee boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    modifie_le timestamp with time zone,
    supprime_le timestamp with time zone,
    organisation_id uuid,
    CONSTRAINT note_interne_contenu_check CHECK ((length(TRIM(BOTH FROM contenu)) > 0))
);

ALTER TABLE ONLY bancarisation.note_interne FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.note_interne OWNER TO postgres;

--
-- Name: TABLE note_interne; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.note_interne IS 'Discussion interne. Jamais le canal DREAL. Invisible au rôle controleur.';


--
-- Name: COLUMN note_interne.occurrence_id; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.note_interne.occurrence_id IS 'NULL = note de projet. FK NO ACTION (ré-import).';


--
-- Name: occurrence; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.occurrence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    echeance_id uuid,
    annee integer NOT NULL,
    code text NOT NULL,
    titre text NOT NULL,
    categorie text NOT NULL,
    statut text DEFAULT 'planifie'::text NOT NULL,
    ug_ids text[] DEFAULT '{}'::text[] NOT NULL,
    mois_debut integer,
    mois_fin integer,
    traverse_nouvel_an boolean DEFAULT false NOT NULL,
    origine text DEFAULT 'ia'::text NOT NULL,
    confiance double precision,
    champs_a_confirmer text[] DEFAULT '{}'::text[] NOT NULL,
    avertissements text[] DEFAULT '{}'::text[] NOT NULL,
    modifie_le timestamp with time zone,
    date_realisation date,
    commentaire text,
    lib_thema text DEFAULT 'autre'::text NOT NULL,
    prestataire text,
    ligne_budget_id uuid,
    prestataire_id uuid,
    date_realisation_fin date,
    surface_m2 numeric,
    responsable_id uuid,
    CONSTRAINT occurrence_realisation_periode_chk CHECK (((date_realisation_fin IS NULL) OR ((date_realisation IS NOT NULL) AND (date_realisation_fin >= date_realisation)))),
    CONSTRAINT occurrence_surface_m2_chk CHECK (((surface_m2 IS NULL) OR (surface_m2 >= (0)::numeric)))
);

ALTER TABLE ONLY bancarisation.occurrence FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.occurrence OWNER TO postgres;

--
-- Name: COLUMN occurrence.date_realisation; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.occurrence.date_realisation IS 'Premier jour de réalisation effective (statut realise).';


--
-- Name: COLUMN occurrence.date_realisation_fin; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.occurrence.date_realisation_fin IS 'Dernier jour de réalisation si l''opération s''étale ; NULL = journée unique.';


--
-- Name: COLUMN occurrence.surface_m2; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.occurrence.surface_m2 IS 'Surface concernée par l''action, en m². NULL = surface totale des UG liées.';


--
-- Name: COLUMN occurrence.responsable_id; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.occurrence.responsable_id IS 'Personne responsable de l''action. Hors trigger log_budget_mouvement.';


--
-- Name: occurrence_finance; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.occurrence_finance (
    occurrence_id uuid NOT NULL,
    projet_id uuid NOT NULL,
    montant_ht numeric,
    montant_engage numeric,
    montant_realise numeric,
    montant_initial numeric,
    annee_initiale integer,
    modifie_le timestamp with time zone DEFAULT now() NOT NULL,
    montant_ttc numeric,
    taux_tva numeric
);

ALTER TABLE ONLY bancarisation.occurrence_finance FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.occurrence_finance OWNER TO postgres;

--
-- Name: TABLE occurrence_finance; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.occurrence_finance IS 'Montants d''une occurrence. Visibles seulement si voit_finances. Pas de ligne si aucun montant.';


--
-- Name: occurrence_responsable; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.occurrence_responsable (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurrence_id uuid NOT NULL,
    projet_id uuid NOT NULL,
    personne_id uuid NOT NULL,
    ordre integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.occurrence_responsable FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.occurrence_responsable OWNER TO postgres;

--
-- Name: TABLE occurrence_responsable; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.occurrence_responsable IS 'Personnes en charge d''une occurrence. Liées aux contacts du projet.';


--
-- Name: organisation_closure; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.organisation_closure (
    ancetre_id uuid NOT NULL,
    descendant_id uuid NOT NULL,
    profondeur integer NOT NULL
);

ALTER TABLE ONLY bancarisation.organisation_closure FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.organisation_closure OWNER TO postgres;

--
-- Name: organisations; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.organisations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    nom text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    parent_id uuid,
    type text DEFAULT 'structure'::text NOT NULL,
    code text,
    archivee boolean DEFAULT false NOT NULL,
    nature text,
    statut text DEFAULT 'active'::text NOT NULL,
    CONSTRAINT chk_organisations_pas_auto_parent CHECK ((parent_id IS DISTINCT FROM id)),
    CONSTRAINT organisations_nature_check CHECK ((nature = ANY (ARRAY['bureau_etudes'::text, 'operateur'::text, 'maitre_ouvrage'::text, 'service_instructeur'::text, 'asl'::text, 'autre'::text]))),
    CONSTRAINT organisations_statut_check CHECK ((statut = ANY (ARRAY['active'::text, 'suspendue'::text]))),
    CONSTRAINT organisations_type_check CHECK ((type = ANY (ARRAY['structure'::text, 'agence'::text])))
);

ALTER TABLE ONLY bancarisation.organisations FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.organisations OWNER TO postgres;

--
-- Name: parametre_signal; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.parametre_signal (
    code text NOT NULL,
    valeur numeric NOT NULL,
    libelle text NOT NULL,
    maj_le timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.parametre_signal FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.parametre_signal OWNER TO postgres;

--
-- Name: parcelles; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.parcelles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organisation_id uuid NOT NULL,
    projet_id uuid,
    idu text,
    code_insee text,
    commune text,
    prefixe text,
    section text,
    numero text,
    contenance_m2 numeric,
    millesime_cadastre text,
    geom public.geometry(MultiPolygon,2154),
    geom_4326 public.geometry(MultiPolygon,4326) GENERATED ALWAYS AS (public.st_transform(geom, 4326)) STORED,
    surface_calculee_m2 numeric GENERATED ALWAYS AS (public.st_area(geom)) STORED,
    source text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.parcelles FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.parcelles OWNER TO postgres;

--
-- Name: personnes; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.personnes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organisation_id uuid NOT NULL,
    type_personne text NOT NULL,
    nom text NOT NULL,
    prenom text,
    forme_juridique text,
    siret text,
    adresse text,
    code_postal text,
    commune text,
    email text,
    telephone text,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    structure_id uuid,
    prestataire_id uuid,
    fonction text,
    telephone_2 text,
    utilisateur_id uuid,
    archive boolean DEFAULT false NOT NULL,
    CONSTRAINT personnes_type_personne_check CHECK ((type_personne = ANY (ARRAY['physique'::text, 'morale'::text])))
);

ALTER TABLE ONLY bancarisation.personnes FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.personnes OWNER TO postgres;

--
-- Name: COLUMN personnes.structure_id; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.personnes.structure_id IS 'Personne morale de rattachement (auto-FK).';


--
-- Name: COLUMN personnes.utilisateur_id; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.personnes.utilisateur_id IS 'Compte auth.users facultatif. Unique par organisation : le même compte peut avoir une fiche dans plusieurs annuaires. L''audit (qui a fait quoi) passe par auth.uid() et profils, pas par cette colonne.';


--
-- Name: COLUMN personnes.archive; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.personnes.archive IS 'Archivage logique — pas de suppression physique.';


--
-- Name: plan_cao; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.plan_cao (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    document_id uuid,
    nom_fichier text NOT NULL,
    dxf_version text,
    insunits integer,
    facteur_metre double precision,
    nb_entites integer DEFAULT 0 NOT NULL,
    bbox_local public.geometry(Polygon),
    calage_mode text DEFAULT 'non_cale'::text NOT NULL,
    srid_cible integer DEFAULT 2154 NOT NULL,
    tx double precision DEFAULT 0 NOT NULL,
    ty double precision DEFAULT 0 NOT NULL,
    rotation_rad double precision DEFAULT 0 NOT NULL,
    echelle double precision DEFAULT 1 NOT NULL,
    calque_0_inclus boolean DEFAULT true NOT NULL,
    statut text DEFAULT 'analyse'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    message_erreur text,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    modifie_le timestamp with time zone,
    srid_declare integer,
    srid_declare_origine text,
    multi_crs boolean DEFAULT false NOT NULL,
    analyse_crs jsonb,
    CONSTRAINT plan_cao_calage_mode_check CHECK ((calage_mode = ANY (ARRAY['non_cale'::text, 'srid_direct'::text, 'deux_points'::text, 'manuel'::text]))),
    CONSTRAINT plan_cao_srid_declare_origine_check CHECK ((srid_declare_origine = ANY (ARRAY['formulaire'::text, 'geodata'::text, 'detection'::text]))),
    CONSTRAINT plan_cao_statut_check CHECK ((statut = ANY (ARRAY['en_attente'::text, 'analyse'::text, 'cale'::text, 'echec'::text])))
);

ALTER TABLE ONLY bancarisation.plan_cao FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.plan_cao OWNER TO postgres;

--
-- Name: TABLE plan_cao; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.plan_cao IS 'Plan CAO importé (DXF). Coordonnées locales jusqu''au calage.';


--
-- Name: COLUMN plan_cao.calage_mode; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao.calage_mode IS 'non_cale | srid_direct | deux_points | manuel. geom_local jamais écrasé.';


--
-- Name: COLUMN plan_cao.calque_0_inclus; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao.calque_0_inclus IS 'True : le calque 0 est extrait (points cotés, blocs). False uniquement si exclus à l''import.';


--
-- Name: COLUMN plan_cao.srid_declare; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao.srid_declare IS 'CRS déclaré au dépôt (formulaire / GEODATA). NULL = local, inconnu, ou « je ne sais pas ». Proposition, jamais vérité imposée.';


--
-- Name: COLUMN plan_cao.srid_declare_origine; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao.srid_declare_origine IS 'D''où vient srid_declare : formulaire, geodata Civil 3D, ou detection.';


--
-- Name: COLUMN plan_cao.multi_crs; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao.multi_crs IS 'True si le recoupement géographique confirme plusieurs CRS dans le même DXF.';


--
-- Name: COLUMN plan_cao.analyse_crs; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao.analyse_crs IS 'Rapport JSON de dxf_crs_clusters (clusters, recoupement, srid par calque, comparaison).';


--
-- Name: plan_cao_calque; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.plan_cao_calque (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    nom text NOT NULL,
    groupe_id text,
    nb_entites integer DEFAULT 0 NOT NULL,
    types_dxf jsonb DEFAULT '{}'::jsonb NOT NULL,
    visible boolean DEFAULT true NOT NULL,
    couleur text,
    aci integer,
    eteint boolean DEFAULT false NOT NULL,
    gele boolean DEFAULT false NOT NULL,
    verrouille boolean DEFAULT false NOT NULL,
    porte_altimetrie boolean DEFAULT false NOT NULL,
    z_min double precision,
    z_max double precision,
    srid_source integer,
    srid_origine text DEFAULT 'herite'::text NOT NULL,
    srid_confiance text,
    cluster_id integer,
    srid_ambigu boolean DEFAULT false NOT NULL,
    projet_id uuid,
    CONSTRAINT plan_cao_calque_srid_confiance_check CHECK ((srid_confiance = ANY (ARRAY['haute'::text, 'a_verifier'::text, 'inconnue'::text]))),
    CONSTRAINT plan_cao_calque_srid_origine_check CHECK ((srid_origine = ANY (ARRAY['herite'::text, 'detection'::text, 'user'::text])))
);

ALTER TABLE ONLY bancarisation.plan_cao_calque FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.plan_cao_calque OWNER TO postgres;

--
-- Name: COLUMN plan_cao_calque.eteint; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao_calque.eteint IS 'Calque éteint dans AutoCAD : masqué par défaut, données conservées.';


--
-- Name: COLUMN plan_cao_calque.porte_altimetrie; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao_calque.porte_altimetrie IS 'True si les Z du calque varient (courbes de niveau, points cotés).';


--
-- Name: COLUMN plan_cao_calque.srid_source; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao_calque.srid_source IS 'EPSG du calque. NULL = on retombe sur plan_cao.srid_declare puis srid_cible.';


--
-- Name: COLUMN plan_cao_calque.srid_ambigu; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao_calque.srid_ambigu IS 'True : le calque est réparti sur plusieurs clusters (deux provenances mélangées). À faire trancher, pas à résoudre auto.';


--
-- Name: plan_cao_entite; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.plan_cao_entite (
    id bigint NOT NULL,
    plan_id uuid NOT NULL,
    calque text NOT NULL,
    dxf_type text NOT NULL,
    handle text,
    texte text,
    geom_local public.geometry NOT NULL,
    geom public.geometry(Geometry,2154),
    geom_3857 public.geometry(Geometry,3857),
    bloc text,
    attributs jsonb DEFAULT '{}'::jsonb NOT NULL,
    couleur text,
    projet_id uuid
);

ALTER TABLE ONLY bancarisation.plan_cao_entite FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.plan_cao_entite OWNER TO postgres;

--
-- Name: COLUMN plan_cao_entite.geom_local; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao_entite.geom_local IS 'Géométrie brute du dessin (SRID 0), XY ou XYZ. Jamais écrasée par le calage.';


--
-- Name: COLUMN plan_cao_entite.bloc; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao_entite.bloc IS 'Nom du bloc INSERT dont l''entité est issue (ex. ARBRE).';


--
-- Name: COLUMN plan_cao_entite.attributs; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao_entite.attributs IS 'ATTRIB du bloc parent (NUMERO, ESSENCE, DIAM_TRONC, fil d''eau…).';


--
-- Name: COLUMN plan_cao_entite.couleur; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.plan_cao_entite.couleur IS 'Couleur résolue (#rrggbb) : true_color > ACI > ByBlock > ByLayer.';


--
-- Name: plan_cao_entite_id_seq; Type: SEQUENCE; Schema: bancarisation; Owner: postgres
--

CREATE SEQUENCE bancarisation.plan_cao_entite_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE bancarisation.plan_cao_entite_id_seq OWNER TO postgres;

--
-- Name: plan_cao_entite_id_seq; Type: SEQUENCE OWNED BY; Schema: bancarisation; Owner: postgres
--

ALTER SEQUENCE bancarisation.plan_cao_entite_id_seq OWNED BY bancarisation.plan_cao_entite.id;


--
-- Name: plan_cao_groupe; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.plan_cao_groupe (
    plan_id uuid NOT NULL,
    id text NOT NULL,
    nom text NOT NULL,
    ordre integer DEFAULT 0 NOT NULL,
    projet_id uuid
);

ALTER TABLE ONLY bancarisation.plan_cao_groupe FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.plan_cao_groupe OWNER TO postgres;

--
-- Name: prescription_couverture; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.prescription_couverture (
    prescription_id uuid NOT NULL,
    echeance_id uuid NOT NULL,
    mode text DEFAULT 'ia'::text NOT NULL,
    confiance numeric,
    note text,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    credit_vente_id bigint,
    projet_id uuid,
    CONSTRAINT prescription_couverture_mode_check CHECK ((mode = ANY (ARRAY['ia'::text, 'user'::text])))
);

ALTER TABLE ONLY bancarisation.prescription_couverture FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.prescription_couverture OWNER TO postgres;

--
-- Name: prestataires; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.prestataires (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    nom text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    siret text,
    forme_juridique text,
    adresse text,
    code_postal text,
    commune text,
    departement text,
    email text,
    telephone text,
    interlocuteur text,
    specialites text[] DEFAULT '{}'::text[] NOT NULL,
    categories_mesure text[] DEFAULT '{}'::text[] NOT NULL,
    actif boolean DEFAULT true NOT NULL,
    notes text,
    siret_norm text GENERATED ALWAYS AS (NULLIF(regexp_replace(COALESCE(siret, ''::text), '[^0-9]'::text, ''::text, 'g'::text), ''::text)) STORED,
    code_naf text,
    tva text
);

ALTER TABLE ONLY bancarisation.prestataires FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.prestataires OWNER TO postgres;

--
-- Name: profils; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.profils (
    utilisateur_id uuid NOT NULL,
    nom text,
    prenom text,
    admin_plateforme boolean DEFAULT false NOT NULL,
    cree_le timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.profils FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.profils OWNER TO postgres;

--
-- Name: projet_acces; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.projet_acces (
    projet_id uuid NOT NULL,
    organisation_id uuid,
    qualite text,
    niveau text DEFAULT 'lecteur'::text NOT NULL,
    cree_le timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    utilisateur_id uuid,
    voir_finances boolean DEFAULT false NOT NULL,
    expire_le timestamp with time zone,
    accorde_par uuid,
    CONSTRAINT projet_acces_niveau_check CHECK ((niveau = ANY (ARRAY['lecteur'::text, 'contributeur'::text, 'gestionnaire'::text]))),
    CONSTRAINT projet_acces_qualite_check CHECK (((qualite IS NULL) OR (qualite = ANY (ARRAY['operateur'::text, 'maitre_ouvrage'::text, 'bureau_etudes'::text, 'controle'::text, 'ancien_proprietaire'::text, 'prestataire'::text])))),
    CONSTRAINT projet_acces_un_beneficiaire CHECK ((num_nonnulls(organisation_id, utilisateur_id) = 1))
);

ALTER TABLE ONLY bancarisation.projet_acces FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.projet_acces OWNER TO postgres;

--
-- Name: projet_contact; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.projet_contact (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    personne_id uuid NOT NULL,
    role_code text NOT NULL,
    role_precision text,
    principal boolean DEFAULT false NOT NULL,
    date_debut date,
    date_fin date,
    commentaire text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    partage_partenaires boolean DEFAULT false NOT NULL
);

ALTER TABLE ONLY bancarisation.projet_contact FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.projet_contact OWNER TO postgres;

--
-- Name: TABLE projet_contact; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.projet_contact IS 'Intervenant d''un projet : une même personne peut avoir plusieurs rôles, sur plusieurs projets.';


--
-- Name: COLUMN projet_contact.partage_partenaires; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.projet_contact.partage_partenaires IS 'Visible des partenaires du projet (maître d''ouvrage, instructeur…) via contacts_partages(). Faux par défaut : le bureau choisit qui il expose. Ne jamais partager un propriétaire ou un titulaire de droits fonciers.';


--
-- Name: projet_geometries; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.projet_geometries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    document_id uuid,
    nom text,
    feature_index integer DEFAULT 0 NOT NULL,
    geometry_type text,
    geometry_geojson jsonb NOT NULL,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_fichier text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.projet_geometries FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.projet_geometries OWNER TO postgres;

--
-- Name: projet_metadata; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.projet_metadata (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    import_id uuid,
    nom_operation text,
    maitre_ouvrage text,
    operateur text,
    communes text[] DEFAULT '{}'::text[] NOT NULL,
    arrete_numero text,
    arrete_date text,
    horizon_debut integer,
    horizon_fin integer,
    horizon_duree_ans integer,
    metadata_json jsonb NOT NULL,
    confiance double precision,
    champs_a_confirmer text[] DEFAULT '{}'::text[] NOT NULL,
    avertissements text[] DEFAULT '{}'::text[] NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.projet_metadata FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.projet_metadata OWNER TO postgres;

--
-- Name: projet_prestataire; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.projet_prestataire (
    projet_id uuid NOT NULL,
    prestataire_id uuid NOT NULL,
    role text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.projet_prestataire FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.projet_prestataire OWNER TO postgres;

--
-- Name: projets; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.projets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organisation_id uuid NOT NULL,
    nom text NOT NULL,
    reference_interne text,
    commune text,
    departement text,
    description text,
    type_procedure text,
    date_decision date,
    duree_annees integer,
    date_fin date GENERATED ALWAYS AS (
CASE
    WHEN ((date_decision IS NOT NULL) AND (duree_annees IS NOT NULL)) THEN (date_decision + make_interval(years => duree_annees))
    ELSE NULL::timestamp without time zone
END) STORED,
    statut text DEFAULT 'en_instruction'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    geomce_nom character varying(50),
    geomce_nom_verrouille boolean DEFAULT false NOT NULL,
    geomce_categorie text,
    geomce_cible text[],
    geomce_description character varying(254),
    geomce_projet_libelle text,
    geomce_procedure_libelle text,
    reference_decision text,
    reference_ei text,
    statut_controle_override text,
    partager_budget_dreal boolean DEFAULT false NOT NULL,
    type_dispositif bancarisation.type_dispositif DEFAULT 'obligation'::bancarisation.type_dispositif NOT NULL,
    cree_par uuid,
    CONSTRAINT projets_statut_controle_override_check CHECK ((statut_controle_override = ANY (ARRAY['en_suivi'::text, 'a_instruire'::text, 'complement_attente'::text, 'non_conforme'::text, 'mise_en_demeure'::text, 'clos'::text])))
);

ALTER TABLE ONLY bancarisation.projets FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.projets OWNER TO postgres;

--
-- Name: COLUMN projets.geomce_nom; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.projets.geomce_nom IS 'Nom mesure tel qu''envoyé à GéoMCE (clé de jointure NOM). Figé après 1er versement.';


--
-- Name: COLUMN projets.partager_budget_dreal; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.projets.partager_budget_dreal IS 'Si true, le budget du projet est visible en lecture côté suivi DREAL. Défaut false.';


--
-- Name: rapport_bilan; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.rapport_bilan (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    annee integer NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    statut text DEFAULT 'brouillon'::text NOT NULL,
    genere_le timestamp with time zone DEFAULT now() NOT NULL,
    genere_par text,
    valide_le timestamp with time zone,
    valide_par text,
    borne_donnees timestamp with time zone,
    baseline_id uuid,
    donnees jsonb DEFAULT '{}'::jsonb NOT NULL,
    controles jsonb DEFAULT '[]'::jsonb NOT NULL,
    document_id uuid,
    type_bilan text DEFAULT 'financier'::text NOT NULL,
    CONSTRAINT rapport_bilan_statut_chk CHECK ((statut = ANY (ARRAY['brouillon'::text, 'valide'::text]))),
    CONSTRAINT rapport_bilan_type_bilan_check CHECK ((type_bilan = ANY (ARRAY['ecologique'::text, 'financier'::text])))
);

ALTER TABLE ONLY bancarisation.rapport_bilan FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.rapport_bilan OWNER TO postgres;

--
-- Name: rapport_suivi; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.rapport_suivi (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    annee integer NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    statut text DEFAULT 'genere'::text NOT NULL,
    genere_le timestamp with time zone DEFAULT now() NOT NULL,
    genere_par text,
    depose_le timestamp with time zone,
    borne_donnees timestamp with time zone,
    donnees jsonb DEFAULT '{}'::jsonb NOT NULL,
    controles jsonb DEFAULT '[]'::jsonb NOT NULL,
    document_id uuid,
    bilan_suivi_id uuid,
    CONSTRAINT rapport_suivi_statut_check CHECK ((statut = ANY (ARRAY['genere'::text, 'depose'::text])))
);

ALTER TABLE ONLY bancarisation.rapport_suivi FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.rapport_suivi OWNER TO postgres;

--
-- Name: TABLE rapport_suivi; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON TABLE bancarisation.rapport_suivi IS 'Snapshot versionné du bilan de suivi écologique (occurrences + preuves + commentaires BE).';


--
-- Name: ref_geomce_categorie; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.ref_geomce_categorie (
    code text NOT NULL,
    libelle text NOT NULL,
    niveau smallint NOT NULL,
    parent text,
    CONSTRAINT ref_geomce_categorie_niveau_check CHECK (((niveau >= 1) AND (niveau <= 4)))
);

ALTER TABLE ONLY bancarisation.ref_geomce_categorie FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.ref_geomce_categorie OWNER TO postgres;

--
-- Name: role_contact_ref; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.role_contact_ref (
    code text NOT NULL,
    libelle text NOT NULL,
    categorie text NOT NULL,
    ordre integer DEFAULT 0 NOT NULL,
    CONSTRAINT role_contact_ref_categorie_chk CHECK ((categorie = ANY (ARRAY['maitrise_ouvrage'::text, 'operateur'::text, 'foncier'::text, 'prestataire'::text, 'controle'::text, 'collectivite'::text, 'tiers'::text])))
);

ALTER TABLE ONLY bancarisation.role_contact_ref FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.role_contact_ref OWNER TO postgres;

--
-- Name: satellite_captures; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.satellite_captures (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    ug_id text NOT NULL,
    acquisition_date date NOT NULL,
    scene_id text,
    cloud_cover double precision,
    bbox_4326 jsonb NOT NULL,
    epsg integer DEFAULT 3035 NOT NULL,
    resolution_m integer DEFAULT 10 NOT NULL,
    width_px integer,
    height_px integer,
    bucket_path_png text,
    bucket_path_tif text,
    document_id_png uuid,
    document_id_tif uuid,
    status text DEFAULT 'ready'::text NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT satellite_captures_status_check CHECK ((status = ANY (ARRAY['ready'::text, 'failed'::text, 'pending'::text])))
);

ALTER TABLE ONLY bancarisation.satellite_captures FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.satellite_captures OWNER TO postgres;

--
-- Name: site_agrement; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.site_agrement (
    id bigint NOT NULL,
    projet_id uuid NOT NULL,
    numero_agrement text,
    prefet_region text,
    arrete_id uuid,
    surface_totale_ha numeric(12,4),
    surface_concernee_ha numeric(12,4),
    avis_instance text,
    statut bancarisation.statut_agrement DEFAULT 'en_montage'::bancarisation.statut_agrement NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY bancarisation.site_agrement FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.site_agrement OWNER TO postgres;

--
-- Name: site_agrement_id_seq; Type: SEQUENCE; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.site_agrement ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME bancarisation.site_agrement_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: transfert_projet; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.transfert_projet (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    projet_nom text,
    org_source uuid NOT NULL,
    org_cible uuid NOT NULL,
    statut text DEFAULT 'en_attente'::text NOT NULL,
    garder_lecture_source boolean DEFAULT true NOT NULL,
    finances_source boolean DEFAULT false NOT NULL,
    commentaire text,
    motif_refus text,
    demande_par uuid,
    demande_le timestamp with time zone DEFAULT now() NOT NULL,
    traite_par uuid,
    traite_le timestamp with time zone,
    export_id uuid,
    CONSTRAINT transfert_projet_check CHECK ((org_source <> org_cible)),
    CONSTRAINT transfert_projet_statut_check CHECK ((statut = ANY (ARRAY['en_attente'::text, 'accepte'::text, 'refuse'::text, 'annule'::text])))
);

ALTER TABLE ONLY bancarisation.transfert_projet FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.transfert_projet OWNER TO postgres;

--
-- Name: COLUMN transfert_projet.export_id; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.transfert_projet.export_id IS 'Export complet lancé à la création de la demande, au nom de l''organisation cédante.';


--
-- Name: ug_lin_parcelles; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.ug_lin_parcelles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ug_id uuid NOT NULL,
    parcelle_id uuid NOT NULL,
    longueur_incluse_m numeric,
    geom_intersection public.geometry(MultiLineString,2154),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    projet_id uuid
);

ALTER TABLE ONLY bancarisation.ug_lin_parcelles FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.ug_lin_parcelles OWNER TO postgres;

--
-- Name: ug_pct_parcelles; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.ug_pct_parcelles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ug_id uuid NOT NULL,
    parcelle_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    projet_id uuid
);

ALTER TABLE ONLY bancarisation.ug_pct_parcelles FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.ug_pct_parcelles OWNER TO postgres;

--
-- Name: ug_surf_parcelles; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.ug_surf_parcelles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ug_id uuid NOT NULL,
    parcelle_id uuid NOT NULL,
    emprise_partielle boolean DEFAULT false NOT NULL,
    surface_incluse_m2 numeric,
    geom_intersection public.geometry(MultiPolygon,2154),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    part_ug_pct numeric,
    projet_id uuid
);

ALTER TABLE ONLY bancarisation.ug_surf_parcelles FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.ug_surf_parcelles OWNER TO postgres;

--
-- Name: COLUMN ug_surf_parcelles.part_ug_pct; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.ug_surf_parcelles.part_ug_pct IS 'Pourcentage de la superficie de la parcelle concerné par l''intersection avec l''UG.';


--
-- Name: unites_de_gestion_lin; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.unites_de_gestion_lin (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    ug_id text,
    libelle text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    geom public.geometry(MultiLineString),
    geom_3857 public.geometry(MultiLineString,3857) NOT NULL,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_fichier text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    attributs jsonb DEFAULT '[]'::jsonb NOT NULL,
    statut text DEFAULT 'ug'::text NOT NULL,
    couche text,
    index_entite integer,
    depot_id text,
    zone_id text,
    motif text,
    origine text DEFAULT 'ia'::text NOT NULL,
    confiance text,
    modifie_le timestamp with time zone,
    categorie_erc text,
    CONSTRAINT ug_lin_statut_coherent CHECK (((statut = 'ug'::text) = (ug_id IS NOT NULL))),
    CONSTRAINT unites_de_gestion_lin_origine_check CHECK ((origine = ANY (ARRAY['ia'::text, 'user'::text]))),
    CONSTRAINT unites_de_gestion_lin_statut_check CHECK ((statut = ANY (ARRAY['ug'::text, 'contexte'::text, 'non_affectee'::text, 'ecartee'::text])))
);

ALTER TABLE ONLY bancarisation.unites_de_gestion_lin FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.unites_de_gestion_lin OWNER TO postgres;

--
-- Name: COLUMN unites_de_gestion_lin.attributs; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.unites_de_gestion_lin.attributs IS 'Table attributaire source (lignes = features du .shp / couche), sans géométrie.';


--
-- Name: unites_de_gestion_pct; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.unites_de_gestion_pct (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    ug_id text,
    libelle text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    geom public.geometry(MultiPoint),
    geom_3857 public.geometry(MultiPoint,3857) NOT NULL,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_fichier text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    attributs jsonb DEFAULT '[]'::jsonb NOT NULL,
    statut text DEFAULT 'ug'::text NOT NULL,
    couche text,
    index_entite integer,
    depot_id text,
    zone_id text,
    motif text,
    origine text DEFAULT 'ia'::text NOT NULL,
    confiance text,
    modifie_le timestamp with time zone,
    categorie_erc text,
    CONSTRAINT ug_pct_statut_coherent CHECK (((statut = 'ug'::text) = (ug_id IS NOT NULL))),
    CONSTRAINT unites_de_gestion_pct_origine_check CHECK ((origine = ANY (ARRAY['ia'::text, 'user'::text]))),
    CONSTRAINT unites_de_gestion_pct_statut_check CHECK ((statut = ANY (ARRAY['ug'::text, 'contexte'::text, 'non_affectee'::text, 'ecartee'::text])))
);

ALTER TABLE ONLY bancarisation.unites_de_gestion_pct FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.unites_de_gestion_pct OWNER TO postgres;

--
-- Name: COLUMN unites_de_gestion_pct.attributs; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.unites_de_gestion_pct.attributs IS 'Table attributaire source (lignes = features du .shp / couche), sans géométrie.';


--
-- Name: unites_de_gestion_surf; Type: TABLE; Schema: bancarisation; Owner: postgres
--

CREATE TABLE bancarisation.unites_de_gestion_surf (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    projet_id uuid NOT NULL,
    ug_id text,
    libelle text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    geom public.geometry(MultiPolygon),
    geom_3857 public.geometry(MultiPolygon,3857) NOT NULL,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_fichier text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    attributs jsonb DEFAULT '[]'::jsonb NOT NULL,
    statut text DEFAULT 'ug'::text NOT NULL,
    couche text,
    index_entite integer,
    depot_id text,
    zone_id text,
    motif text,
    origine text DEFAULT 'ia'::text NOT NULL,
    confiance text,
    modifie_le timestamp with time zone,
    categorie_erc text,
    CONSTRAINT ug_surf_statut_coherent CHECK (((statut = 'ug'::text) = (ug_id IS NOT NULL))),
    CONSTRAINT unites_de_gestion_surf_origine_check CHECK ((origine = ANY (ARRAY['ia'::text, 'user'::text]))),
    CONSTRAINT unites_de_gestion_surf_statut_check CHECK ((statut = ANY (ARRAY['ug'::text, 'contexte'::text, 'non_affectee'::text, 'ecartee'::text])))
);

ALTER TABLE ONLY bancarisation.unites_de_gestion_surf FORCE ROW LEVEL SECURITY;


ALTER TABLE bancarisation.unites_de_gestion_surf OWNER TO postgres;

--
-- Name: COLUMN unites_de_gestion_surf.attributs; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON COLUMN bancarisation.unites_de_gestion_surf.attributs IS 'Table attributaire source (lignes = features du .shp / couche), sans géométrie.';


--
-- Name: v_occurrence_action_fiche; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_occurrence_action_fiche WITH (security_invoker='true') AS
 SELECT o.id AS occurrence_id,
    o.projet_id,
    o.echeance_id,
    o.annee,
    o.statut AS statut_execution,
    o.date_realisation,
    af.id AS action_fiche_id,
    af.code AS action_code,
    af.titre AS action_titre,
    af.categorie AS action_categorie
   FROM ((bancarisation.occurrence o
     LEFT JOIN bancarisation.echeance e ON ((e.id = o.echeance_id)))
     LEFT JOIN bancarisation.action_fiche af ON (((af.projet_id = o.projet_id) AND (af.cle = e.action_cle))));


ALTER VIEW bancarisation.v_occurrence_action_fiche OWNER TO postgres;

--
-- Name: v_action_fiche_conformite; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_action_fiche_conformite WITH (security_invoker='true') AS
 WITH constats_direct AS (
         SELECT constat_controle.action_fiche_id,
            constat_controle.conformite,
            constat_controle.date_constat
           FROM bancarisation.constat_controle
          WHERE (constat_controle.action_fiche_id IS NOT NULL)
        ), constats_occ AS (
         SELECT v.action_fiche_id,
            c.conformite,
            c.date_constat
           FROM (bancarisation.constat_controle c
             JOIN bancarisation.v_occurrence_action_fiche v ON ((v.occurrence_id = c.occurrence_id)))
          WHERE ((c.occurrence_id IS NOT NULL) AND (v.action_fiche_id IS NOT NULL))
        ), tous AS (
         SELECT constats_direct.action_fiche_id,
            constats_direct.conformite,
            constats_direct.date_constat
           FROM constats_direct
        UNION ALL
         SELECT constats_occ.action_fiche_id,
            constats_occ.conformite,
            constats_occ.date_constat
           FROM constats_occ
        ), dernier AS (
         SELECT DISTINCT ON (tous.action_fiche_id) tous.action_fiche_id,
            tous.conformite,
            tous.date_constat
           FROM tous
          ORDER BY tous.action_fiche_id, tous.date_constat DESC
        ), nb_cst AS (
         SELECT tous.action_fiche_id,
            (count(*))::integer AS nb_constats
           FROM tous
          GROUP BY tous.action_fiche_id
        )
 SELECT af.id AS action_fiche_id,
    af.projet_id,
    af.code,
    af.titre,
    af.categorie,
    COALESCE(d.conformite, 'non_evalue'::text) AS conformite,
    d.date_constat AS dernier_constat_le,
    COALESCE(n.nb_constats, 0) AS nb_constats,
    (count(p.id))::integer AS nb_prescriptions
   FROM (((bancarisation.action_fiche af
     LEFT JOIN dernier d ON ((d.action_fiche_id = af.id)))
     LEFT JOIN nb_cst n ON ((n.action_fiche_id = af.id)))
     LEFT JOIN bancarisation.arrete_prescription p ON ((p.action_fiche_id = af.id)))
  GROUP BY af.id, af.projet_id, af.code, af.titre, af.categorie, d.conformite, d.date_constat, n.nb_constats;


ALTER VIEW bancarisation.v_action_fiche_conformite OWNER TO postgres;

--
-- Name: v_action_portefeuille; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_action_portefeuille WITH (security_invoker='true') AS
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
    f.montant_ht,
    f.montant_ttc,
    f.taux_tva,
    o.prestataire,
    o.ligne_budget_id,
    f.montant_initial,
    f.annee_initiale,
    f.montant_engage,
    f.montant_realise,
    o.prestataire_id,
    COALESCE(prst.nom, o.prestataire) AS prestataire_nom,
    e.recurrence AS echeance_recurrence,
    o.date_realisation_fin,
    o.surface_m2,
    o.responsable_id,
    COALESCE(NULLIF(TRIM(BOTH FROM concat_ws(' '::text, resp.prenom, resp.nom)), ''::text), resp.nom) AS responsable_nom,
    p.nom AS projet_nom,
    p.reference_interne AS projet_reference,
    p.departement AS projet_departement,
    p.organisation_id,
    org.nom AS organisation_nom,
    fen.date_fin_fenetre,
    ((o.statut <> ALL (ARRAY['realise'::text, 'supprime'::text])) AND (fen.date_fin_fenetre < CURRENT_DATE)) AS en_retard,
    et.nb_etapes,
    et.nb_etapes_faites,
    et.etape_courante,
    nt.nb_notes,
    nt.derniere_note_le
   FROM ((((((((((bancarisation.occurrence o
     LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
     LEFT JOIN bancarisation.echeance e ON ((e.id = o.echeance_id)))
     LEFT JOIN bancarisation.action_fiche af ON (((af.projet_id = o.projet_id) AND (af.cle = e.action_cle))))
     LEFT JOIN bancarisation.prestataires prst ON ((prst.id = o.prestataire_id)))
     LEFT JOIN bancarisation.personnes resp ON ((resp.id = o.responsable_id)))
     LEFT JOIN bancarisation.projets p ON ((p.id = o.projet_id)))
     LEFT JOIN bancarisation.organisations org ON ((org.id = p.organisation_id)))
     CROSS JOIN LATERAL ( SELECT (((make_date((o.annee +
                CASE
                    WHEN COALESCE(o.traverse_nouvel_an, false) THEN 1
                    ELSE 0
                END), COALESCE(o.mois_fin, 12), 1) + '1 mon'::interval) - '1 day'::interval))::date AS date_fin_fenetre) fen)
     LEFT JOIN LATERAL ( SELECT (count(*))::integer AS nb_etapes,
            (count(*) FILTER (WHERE ae.fait))::integer AS nb_etapes_faites,
            ( SELECT e2.libelle
                   FROM bancarisation.action_etape e2
                  WHERE ((e2.occurrence_id = o.id) AND (NOT e2.fait))
                  ORDER BY e2.ordre
                 LIMIT 1) AS etape_courante
           FROM bancarisation.action_etape ae
          WHERE (ae.occurrence_id = o.id)) et ON (true))
     LEFT JOIN LATERAL ( SELECT (count(*))::integer AS nb_notes,
            max(n.created_at) AS derniere_note_le
           FROM bancarisation.note_interne n
          WHERE ((n.occurrence_id = o.id) AND (n.supprime_le IS NULL))) nt ON (true));


ALTER VIEW bancarisation.v_action_portefeuille OWNER TO postgres;

--
-- Name: v_projet_statut_controle; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_projet_statut_controle WITH (security_invoker='true') AS
 SELECT id AS projet_id,
        CASE
            WHEN (EXISTS ( SELECT 1
               FROM bancarisation.acte_dreal a
              WHERE ((a.projet_id = pr.id) AND (a.type = 'mise_en_demeure'::text) AND (a.statut = 'emis'::text)))) THEN 'mise_en_demeure'::text
            WHEN (EXISTS ( SELECT 1
               FROM bancarisation.v_action_fiche_conformite c
              WHERE ((c.projet_id = pr.id) AND (c.conformite = 'non_conforme'::text)))) THEN 'non_conforme'::text
            WHEN (EXISTS ( SELECT 1
               FROM bancarisation.acte_dreal a
              WHERE ((a.projet_id = pr.id) AND (a.type = 'demande_complement'::text) AND (a.statut = 'emis'::text)))) THEN 'complement_attente'::text
            WHEN (EXISTS ( SELECT 1
               FROM bancarisation.bilan_suivi b
              WHERE ((b.projet_id = pr.id) AND (b.statut = ANY (ARRAY['depose'::text, 'en_relecture'::text]))))) THEN 'a_instruire'::text
            ELSE 'en_suivi'::text
        END AS statut_controle
   FROM bancarisation.projets pr;


ALTER VIEW bancarisation.v_projet_statut_controle OWNER TO postgres;

--
-- Name: v_projet_statut_effectif; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_projet_statut_effectif WITH (security_invoker='true') AS
 SELECT p.id AS projet_id,
    COALESCE(p.statut_controle_override, v.statut_controle) AS statut_controle,
    (p.statut_controle_override IS NOT NULL) AS force_manuel
   FROM (bancarisation.projets p
     JOIN bancarisation.v_projet_statut_controle v ON ((v.projet_id = p.id)));


ALTER VIEW bancarisation.v_projet_statut_effectif OWNER TO postgres;

--
-- Name: v_bannette_a_traiter; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_bannette_a_traiter WITH (security_invoker='true') AS
 SELECT ('bilan:'::text || (b.id)::text) AS id,
    b.projet_id,
    p.nom AS projet_nom,
    o.nom AS organisation_nom,
    'bilan_a_valider'::text AS motif,
    ((('Bilan '::text || b.annee) || ' — '::text) || b.statut) AS libelle,
    NULL::date AS echeance,
    2 AS priorite,
    se.statut_controle,
    b.id AS bilan_id,
    NULL::uuid AS acte_id
   FROM (((bancarisation.bilan_suivi b
     JOIN bancarisation.projets p ON ((p.id = b.projet_id)))
     JOIN bancarisation.organisations o ON ((o.id = p.organisation_id)))
     JOIN bancarisation.v_projet_statut_effectif se ON ((se.projet_id = b.projet_id)))
  WHERE (b.statut = ANY (ARRAY['depose'::text, 'en_relecture'::text]))
UNION ALL
 SELECT ('acte:'::text || (a.id)::text) AS id,
    a.projet_id,
    p.nom AS projet_nom,
    o.nom AS organisation_nom,
        CASE
            WHEN (a.statut = 'brouillon'::text) THEN 'acte_brouillon'::text
            WHEN (a.type = 'mise_en_demeure'::text) THEN 'mise_en_demeure_suivi'::text
            WHEN (a.type = 'demande_complement'::text) THEN 'complement_delai'::text
            ELSE 'a_instruire'::text
        END AS motif,
    COALESCE(a.objet, a.type) AS libelle,
    a.delai_reponse AS echeance,
        CASE
            WHEN (a.type = 'mise_en_demeure'::text) THEN 3
            WHEN ((a.type = 'demande_complement'::text) AND (a.statut = 'emis'::text)) THEN 2
            ELSE 1
        END AS priorite,
    se.statut_controle,
    NULL::uuid AS bilan_id,
    a.id AS acte_id
   FROM (((bancarisation.acte_dreal a
     JOIN bancarisation.projets p ON ((p.id = a.projet_id)))
     JOIN bancarisation.organisations o ON ((o.id = p.organisation_id)))
     JOIN bancarisation.v_projet_statut_effectif se ON ((se.projet_id = a.projet_id)))
  WHERE ((a.statut = ANY (ARRAY['brouillon'::text, 'emis'::text])) AND (a.type = ANY (ARRAY['demande_complement'::text, 'mise_en_demeure'::text, 'validation'::text])));


ALTER VIEW bancarisation.v_bannette_a_traiter OWNER TO postgres;

--
-- Name: v_budget_annuel; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_budget_annuel WITH (security_invoker='true') AS
 SELECT o.projet_id,
    o.annee,
    count(*) FILTER (WHERE (f.montant_ht IS NOT NULL)) AS nb_lignes_chiffrees,
    count(*) AS nb_occurrences,
    sum(f.montant_ht) AS total_ht,
    sum(f.montant_realise) AS total_ht_realise,
    sum(f.montant_engage) AS total_ht_engage
   FROM (bancarisation.occurrence o
     LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
  WHERE (o.statut <> ALL (ARRAY['supprime'::text, 'archive'::text]))
  GROUP BY o.projet_id, o.annee;


ALTER VIEW bancarisation.v_budget_annuel OWNER TO postgres;

--
-- Name: v_budget_delta_annuel; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_budget_delta_annuel WITH (security_invoker='true') AS
 WITH courant AS (
         SELECT o.projet_id,
            o.annee,
            sum(f.montant_ht) AS prevu,
            sum(f.montant_engage) AS engage,
            sum(f.montant_realise) AS realise
           FROM (bancarisation.occurrence o
             LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
          WHERE (o.statut <> 'supprime'::text)
          GROUP BY o.projet_id, o.annee
        ), initial AS (
         SELECT o.projet_id,
            f.annee_initiale AS annee,
            sum(f.montant_initial) AS initial
           FROM (bancarisation.occurrence o
             JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
          WHERE ((f.montant_initial IS NOT NULL) AND (f.annee_initiale IS NOT NULL))
          GROUP BY o.projet_id, f.annee_initiale
        )
 SELECT COALESCE(c.projet_id, i.projet_id) AS projet_id,
    COALESCE(c.annee, i.annee) AS annee,
    i.initial,
    c.prevu,
    c.engage,
    c.realise,
    (c.prevu - i.initial) AS delta_prevu_initial
   FROM (courant c
     FULL JOIN initial i ON (((i.projet_id = c.projet_id) AND (i.annee = c.annee))));


ALTER VIEW bancarisation.v_budget_delta_annuel OWNER TO postgres;

--
-- Name: v_budget_ecarts; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_budget_ecarts WITH (security_invoker='true') AS
 SELECT o.projet_id,
    COALESCE(f.annee_initiale, o.annee) AS annee_ref,
    sum(f.montant_initial) FILTER (WHERE ((o.statut = 'supprime'::text) AND (f.montant_initial IS NOT NULL))) AS annule,
    sum(f.montant_ht) FILTER (WHERE ((o.statut <> 'supprime'::text) AND (f.annee_initiale IS NOT NULL) AND (o.annee <> f.annee_initiale))) AS glisse_sortant,
    sum(f.montant_ht) FILTER (WHERE ((o.statut <> 'supprime'::text) AND (f.montant_initial IS NULL))) AS ajoute,
    sum((f.montant_ht - f.montant_initial)) FILTER (WHERE ((o.statut <> 'supprime'::text) AND (f.montant_initial IS NOT NULL) AND (o.annee = f.annee_initiale))) AS revision_prix
   FROM (bancarisation.occurrence o
     LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
  GROUP BY o.projet_id, COALESCE(f.annee_initiale, o.annee);


ALTER VIEW bancarisation.v_budget_ecarts OWNER TO postgres;

--
-- Name: v_ug_parcelles; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_ug_parcelles WITH (security_invoker='true') AS
 SELECT 'surf'::text AS ug_type,
    u.ug_id,
    l.parcelle_id,
    max(l.surface_incluse_m2) AS metrique,
    'm2'::text AS unite,
    max(COALESCE(NULLIF(TRIM(BOTH FROM u.libelle), ''::text), u.ug_id)) AS libelle,
    u.ug_id AS ug_code,
    u.projet_id,
    max(l.part_ug_pct) AS part_ug_pct
   FROM (bancarisation.ug_surf_parcelles l
     JOIN bancarisation.unites_de_gestion_surf u ON ((u.id = l.ug_id)))
  WHERE ((u.statut = 'ug'::text) AND (u.ug_id IS NOT NULL))
  GROUP BY u.projet_id, u.ug_id, l.parcelle_id
UNION ALL
 SELECT 'lin'::text AS ug_type,
    u.ug_id,
    l.parcelle_id,
    max(l.longueur_incluse_m) AS metrique,
    'm'::text AS unite,
    max(COALESCE(NULLIF(TRIM(BOTH FROM u.libelle), ''::text), u.ug_id)) AS libelle,
    u.ug_id AS ug_code,
    u.projet_id,
    NULL::numeric AS part_ug_pct
   FROM (bancarisation.ug_lin_parcelles l
     JOIN bancarisation.unites_de_gestion_lin u ON ((u.id = l.ug_id)))
  WHERE ((u.statut = 'ug'::text) AND (u.ug_id IS NOT NULL))
  GROUP BY u.projet_id, u.ug_id, l.parcelle_id
UNION ALL
 SELECT 'pct'::text AS ug_type,
    u.ug_id,
    l.parcelle_id,
    NULL::numeric AS metrique,
    NULL::text AS unite,
    max(COALESCE(NULLIF(TRIM(BOTH FROM u.libelle), ''::text), u.ug_id)) AS libelle,
    u.ug_id AS ug_code,
    u.projet_id,
    NULL::numeric AS part_ug_pct
   FROM (bancarisation.ug_pct_parcelles l
     JOIN bancarisation.unites_de_gestion_pct u ON ((u.id = l.ug_id)))
  WHERE ((u.statut = 'ug'::text) AND (u.ug_id IS NOT NULL))
  GROUP BY u.projet_id, u.ug_id, l.parcelle_id;


ALTER VIEW bancarisation.v_ug_parcelles OWNER TO postgres;

--
-- Name: v_contact_projet; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_contact_projet WITH (security_invoker='true') AS
 WITH foncier_liens AS (
         SELECT DISTINCT COALESCE(ug.projet_id, pa.projet_id) AS projet_id,
            df.titulaire_id AS personne_id,
                CASE
                    WHEN (df.nature = ANY (ARRAY['propriete'::text, 'usufruit'::text, 'nue_propriete'::text])) THEN 'proprietaire'::text
                    WHEN (df.nature = ANY (ARRAY['bail_rural'::text, 'bail_rural_clauses_env'::text, 'bail_emphyteotique'::text, 'commodat'::text, 'aot'::text, 'convention_paturage'::text])) THEN 'preneur_bail'::text
                    WHEN (df.nature = ANY (ARRAY['ore'::text, 'convention_gestion'::text, 'regime_forestier'::text])) THEN 'gestionnaire_site'::text
                    WHEN (df.nature = 'cmd_safer'::text) THEN 'safer'::text
                    ELSE 'autre'::text
                END AS role_code,
            df.nature AS role_precision
           FROM ((bancarisation.droits_fonciers df
             JOIN bancarisation.parcelles pa ON ((pa.id = df.parcelle_id)))
             LEFT JOIN bancarisation.v_ug_parcelles ug ON ((ug.parcelle_id = pa.id)))
          WHERE ((df.titulaire_id IS NOT NULL) AND (COALESCE(ug.projet_id, pa.projet_id) IS NOT NULL))
        UNION
         SELECT DISTINCT COALESCE(ug.projet_id, pa.projet_id) AS "coalesce",
            df.constituant_id,
            'proprietaire'::text,
            df.nature
           FROM ((bancarisation.droits_fonciers df
             JOIN bancarisation.parcelles pa ON ((pa.id = df.parcelle_id)))
             LEFT JOIN bancarisation.v_ug_parcelles ug ON ((ug.parcelle_id = pa.id)))
          WHERE ((df.constituant_id IS NOT NULL) AND (df.constituant_id IS DISTINCT FROM df.titulaire_id) AND (COALESCE(ug.projet_id, pa.projet_id) IS NOT NULL))
        )
 SELECT pc.id AS lien_id,
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
    COALESCE(NULLIF(TRIM(BOTH FROM concat_ws(' '::text, pe.prenom, pe.nom)), ''::text), pe.nom) AS personne_nom,
    pe.prenom,
    pe.nom,
    pe.email,
    pe.telephone,
    pe.telephone_2,
    pe.fonction,
    pe.type_personne,
    pe.structure_id,
    COALESCE(NULLIF(TRIM(BOTH FROM concat_ws(' '::text, st.prenom, st.nom)), ''::text), st.nom) AS structure_nom,
    pe.prestataire_id,
    pe.utilisateur_id,
    pe.archive
   FROM ((((bancarisation.projet_contact pc
     JOIN bancarisation.role_contact_ref r ON ((r.code = pc.role_code)))
     JOIN bancarisation.personnes pe ON ((pe.id = pc.personne_id)))
     LEFT JOIN bancarisation.projets pr ON ((pr.id = pc.projet_id)))
     LEFT JOIN bancarisation.personnes st ON ((st.id = pe.structure_id)))
UNION ALL
 SELECT NULL::uuid AS lien_id,
    fl.projet_id,
    pr.nom AS projet_nom,
    fl.personne_id,
    fl.role_code,
    r.libelle AS role_libelle,
    r.categorie AS role_categorie,
    r.ordre AS role_ordre,
    fl.role_precision,
    false AS principal,
    NULL::date AS date_debut,
    NULL::date AS date_fin,
    NULL::text AS commentaire,
    'foncier'::text AS source,
    COALESCE(NULLIF(TRIM(BOTH FROM concat_ws(' '::text, pe.prenom, pe.nom)), ''::text), pe.nom) AS personne_nom,
    pe.prenom,
    pe.nom,
    pe.email,
    pe.telephone,
    pe.telephone_2,
    pe.fonction,
    pe.type_personne,
    pe.structure_id,
    COALESCE(NULLIF(TRIM(BOTH FROM concat_ws(' '::text, st.prenom, st.nom)), ''::text), st.nom) AS structure_nom,
    pe.prestataire_id,
    pe.utilisateur_id,
    pe.archive
   FROM ((((foncier_liens fl
     JOIN bancarisation.role_contact_ref r ON ((r.code = fl.role_code)))
     JOIN bancarisation.personnes pe ON ((pe.id = fl.personne_id)))
     LEFT JOIN bancarisation.projets pr ON ((pr.id = fl.projet_id)))
     LEFT JOIN bancarisation.personnes st ON ((st.id = pe.structure_id)));


ALTER VIEW bancarisation.v_contact_projet OWNER TO postgres;

--
-- Name: v_credit_stock; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_credit_stock AS
SELECT
    NULL::bigint AS lot_id,
    NULL::bigint AS site_agrement_id,
    NULL::uuid AS projet_id,
    NULL::text AS libelle,
    NULL::text AS composante_milieu,
    NULL::text AS unite,
    NULL::date AS date_emission,
    NULL::numeric(12,4) AS quantite_emise,
    NULL::numeric AS qte_reservee,
    NULL::numeric AS qte_vendue,
    NULL::numeric AS qte_affectee,
    NULL::numeric AS qte_disponible;


ALTER VIEW bancarisation.v_credit_stock OWNER TO postgres;

--
-- Name: v_credit_suivi; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_credit_suivi WITH (security_invoker='true') AS
 SELECT p.lot_id,
    l.site_agrement_id,
    sa.projet_id,
    l.libelle AS lot_libelle,
    p.annee_palier,
    p.libelle AS libelle_palier,
    max(cg.date_campagne) AS dernier_constat,
    max((cg.statut)::text) AS dernier_statut,
    (((p.annee_palier)::numeric <= EXTRACT(year FROM now())) AND (count(cg.id) = 0)) AS manque_constat
   FROM (((bancarisation.credit_palier p
     JOIN bancarisation.credit_lot l ON ((l.id = p.lot_id)))
     JOIN bancarisation.site_agrement sa ON ((sa.id = l.site_agrement_id)))
     LEFT JOIN bancarisation.constat_gain cg ON (((cg.lot_id = p.lot_id) AND (cg.annee_palier = p.annee_palier))))
  GROUP BY p.lot_id, l.site_agrement_id, sa.projet_id, l.libelle, p.annee_palier, p.libelle;


ALTER VIEW bancarisation.v_credit_suivi OWNER TO postgres;

--
-- Name: v_fil_interne; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_fil_interne WITH (security_invoker='true') AS
 WITH evt AS (
         SELECT 'note'::text AS type,
            n.id,
            n.projet_id,
            n.occurrence_id,
            n.auteur_id AS personne_id,
            n.contenu AS texte,
            n.created_at AS horodatage,
            n.document_id,
            n.mentions,
            n.modifie_le,
            n.epinglee
           FROM bancarisation.note_interne n
          WHERE (n.supprime_le IS NULL)
        UNION ALL
         SELECT 'etape'::text AS text,
            e.id,
            e.projet_id,
            e.occurrence_id,
            e.fait_par,
            e.libelle,
            e.fait_le,
            NULL::uuid AS uuid,
            '{}'::uuid[] AS uuid,
            NULL::timestamp with time zone AS timestamptz,
            false
           FROM bancarisation.action_etape e
          WHERE (e.fait AND (e.fait_le IS NOT NULL))
        )
 SELECT evt.type,
    evt.id,
    evt.projet_id,
    evt.occurrence_id,
    evt.personne_id,
    evt.texte,
    evt.horodatage,
    evt.document_id,
    evt.mentions,
    evt.modifie_le,
    evt.epinglee,
    p.nom AS projet_nom,
    p.reference_interne AS projet_reference,
    p.organisation_id,
    o.code AS action_code,
    o.titre AS action_titre,
    o.annee AS action_annee,
    COALESCE(NULLIF(TRIM(BOTH FROM concat_ws(' '::text, pe.prenom, pe.nom)), ''::text), pe.nom) AS personne_nom
   FROM (((evt
     JOIN bancarisation.projets p ON ((p.id = evt.projet_id)))
     LEFT JOIN bancarisation.occurrence o ON ((o.id = evt.occurrence_id)))
     LEFT JOIN bancarisation.personnes pe ON ((pe.id = evt.personne_id)));


ALTER VIEW bancarisation.v_fil_interne OWNER TO postgres;

--
-- Name: v_frontend_mesures_projets; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_frontend_mesures_projets WITH (security_invoker='true') AS
 SELECT ('surface_'::text || geomce_surf.identifiant) AS mesure_id,
    geomce_surf.identifiant AS original_id,
    'surface'::text AS type_geometrie,
    geomce_surf.projet,
    geomce_surf.dossier_no,
    geomce_surf.theme,
    geomce_surf.maitre_ouvrage,
    geomce_surf.type_procedure,
    geomce_surf.date_decision,
    geomce_surf.classe,
    geomce_surf.type,
    geomce_surf.categorie,
    geomce_surf.sous_categorie,
    geomce_surf.duree,
    geomce_surf.origine_si,
    geomce_surf.l_dep,
    geomce_surf.geom_2154,
    geomce_surf.geom_3857
   FROM bancarisation.geomce_surf
UNION ALL
 SELECT ('lineaire_'::text || geomce_lin.identifiant) AS mesure_id,
    geomce_lin.identifiant AS original_id,
    'lineaire'::text AS type_geometrie,
    geomce_lin.projet,
    geomce_lin.dossier_no,
    geomce_lin.theme,
    geomce_lin.maitre_ouvrage,
    geomce_lin.type_procedure,
    geomce_lin.date_decision,
    geomce_lin.classe,
    geomce_lin.type,
    geomce_lin.categorie,
    geomce_lin.sous_categorie,
    geomce_lin.duree,
    geomce_lin.origine_si,
    geomce_lin.l_dep,
    geomce_lin.geom_2154,
    geomce_lin.geom_3857
   FROM bancarisation.geomce_lin
UNION ALL
 SELECT ('ponctuel_'::text || geomce_pct.identifiant) AS mesure_id,
    geomce_pct.identifiant AS original_id,
    'ponctuel'::text AS type_geometrie,
    geomce_pct.projet,
    geomce_pct.dossier_no,
    geomce_pct.theme,
    geomce_pct.maitre_ouvrage,
    geomce_pct.type_procedure,
    geomce_pct.date_decision,
    geomce_pct.classe,
    geomce_pct.type,
    geomce_pct.categorie,
    geomce_pct.sous_categorie,
    geomce_pct.duree,
    geomce_pct.origine_si,
    geomce_pct.l_dep,
    geomce_pct.geom_2154,
    geomce_pct.geom_3857
   FROM bancarisation.geomce_pct;


ALTER VIEW bancarisation.v_frontend_mesures_projets OWNER TO postgres;

--
-- Name: v_frontend_mesures_par_departement; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_frontend_mesures_par_departement WITH (security_invoker='true') AS
 SELECT (regexp_match(l_dep, '([0-9]{1,3}[A-Z]?)'::text))[1] AS departement,
    (count(*))::integer AS nb_mesures
   FROM bancarisation.v_frontend_mesures_projets
  WHERE (l_dep IS NOT NULL)
  GROUP BY (regexp_match(l_dep, '([0-9]{1,3}[A-Z]?)'::text))[1];


ALTER VIEW bancarisation.v_frontend_mesures_par_departement OWNER TO postgres;

--
-- Name: v_mesure_conformite; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_mesure_conformite WITH (security_invoker='true') AS
 SELECT action_fiche_id AS mesure_id,
    projet_id,
    code,
    titre,
    categorie,
    conformite,
    dernier_constat_le,
    nb_constats,
    nb_prescriptions
   FROM bancarisation.v_action_fiche_conformite;


ALTER VIEW bancarisation.v_mesure_conformite OWNER TO postgres;

--
-- Name: v_occurrence_calendrier; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_occurrence_calendrier WITH (security_invoker='true') AS
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
    f.montant_ht,
    f.montant_ttc,
    f.taux_tva,
    o.prestataire,
    o.ligne_budget_id,
    f.montant_initial,
    f.annee_initiale,
    f.montant_engage,
    f.montant_realise,
    o.prestataire_id,
    COALESCE(p.nom, o.prestataire) AS prestataire_nom,
    e.recurrence AS echeance_recurrence,
    o.date_realisation_fin,
    o.surface_m2,
    o.responsable_id,
    COALESCE(NULLIF(TRIM(BOTH FROM concat_ws(' '::text, resp.prenom, resp.nom)), ''::text), resp.nom) AS responsable_nom
   FROM (((((bancarisation.occurrence o
     LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
     LEFT JOIN bancarisation.echeance e ON ((e.id = o.echeance_id)))
     LEFT JOIN bancarisation.action_fiche af ON (((af.projet_id = o.projet_id) AND (af.cle = e.action_cle))))
     LEFT JOIN bancarisation.prestataires p ON ((p.id = o.prestataire_id)))
     LEFT JOIN bancarisation.personnes resp ON ((resp.id = o.responsable_id)));


ALTER VIEW bancarisation.v_occurrence_calendrier OWNER TO postgres;

--
-- Name: v_organisation_arbre; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_organisation_arbre WITH (security_invoker='true') AS
 SELECT o.id,
    o.nom,
    o.code,
    o.type,
    o.parent_id,
    p.nom AS parent_nom,
    (COALESCE((p.nom || ' / '::text), ''::text) || o.nom) AS chemin,
    ( SELECT count(*) AS count
           FROM bancarisation.organisation_closure c
          WHERE ((c.ancetre_id = o.id) AND (c.profondeur > 0))) AS nb_descendants
   FROM (bancarisation.organisations o
     LEFT JOIN bancarisation.organisations p ON ((p.id = o.parent_id)))
  WHERE (NOT o.archivee);


ALTER VIEW bancarisation.v_organisation_arbre OWNER TO postgres;

--
-- Name: v_parc_bilan_matrice; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_parc_bilan_matrice WITH (security_invoker='true') AS
 WITH fenetre AS (
         SELECT p.id AS projet_id,
            p.organisation_id,
            gs.annee,
            (EXTRACT(year FROM now()))::integer AS annee_courante
           FROM (bancarisation.projets p
             CROSS JOIN LATERAL generate_series((EXTRACT(year FROM p.date_decision))::integer, (EXTRACT(year FROM COALESCE((p.date_fin)::timestamp with time zone, (now() + '1 year'::interval))))::integer) gs(annee))
          WHERE (p.date_decision IS NOT NULL)
        ), meilleur AS (
         SELECT DISTINCT ON (rb.projet_id, rb.annee) rb.projet_id,
            rb.annee,
            rb.id,
            rb.statut,
            rb.version,
            rb.valide_le
           FROM bancarisation.rapport_bilan rb
          ORDER BY rb.projet_id, rb.annee, (rb.statut = 'valide'::text) DESC, rb.version DESC
        )
 SELECT f.projet_id,
    f.organisation_id,
    f.annee,
    COALESCE(m.statut,
        CASE
            WHEN (f.annee < f.annee_courante) THEN 'manquant'::text
            ELSE 'a_venir'::text
        END) AS etat,
    m.id AS rapport_id,
    m.version,
    m.valide_le
   FROM (fenetre f
     LEFT JOIN meilleur m ON (((m.projet_id = f.projet_id) AND (m.annee = f.annee))));


ALTER VIEW bancarisation.v_parc_bilan_matrice OWNER TO postgres;

--
-- Name: v_parc_financier_annuel; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_parc_financier_annuel WITH (security_invoker='true') AS
 SELECT p.organisation_id,
    org.nom AS organisation_nom,
    d.annee,
    count(DISTINCT d.projet_id) AS nb_projets,
    sum(d.initial) AS initial,
    sum(d.prevu) AS prevu,
    sum(d.engage) AS engage,
    sum(d.realise) AS realise,
    sum(d.delta_prevu_initial) AS delta
   FROM ((bancarisation.v_budget_delta_annuel d
     JOIN bancarisation.projets p ON ((p.id = d.projet_id)))
     JOIN bancarisation.organisations org ON ((org.id = p.organisation_id)))
  GROUP BY p.organisation_id, org.nom, d.annee;


ALTER VIEW bancarisation.v_parc_financier_annuel OWNER TO postgres;

--
-- Name: v_projet_annee_obligation; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_projet_annee_obligation WITH (security_invoker='true') AS
 SELECT p.id AS projet_id,
    p.organisation_id,
    gs.annee
   FROM (bancarisation.projets p
     CROSS JOIN LATERAL generate_series((EXTRACT(year FROM p.date_decision))::integer, LEAST((EXTRACT(year FROM COALESCE((p.date_fin)::timestamp with time zone, now())))::integer, ((EXTRACT(year FROM now()))::integer - 1))) gs(annee))
  WHERE ((p.date_decision IS NOT NULL) AND (p.statut <> 'archive'::text));


ALTER VIEW bancarisation.v_projet_annee_obligation OWNER TO postgres;

--
-- Name: v_parc_signal; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_parc_signal WITH (security_invoker='true') AS
 WITH sig_bilan AS (
         SELECT o.projet_id,
            'bilan_manquant'::text AS code,
                CASE
                    WHEN ((count(*))::numeric >= bancarisation.seuil('bilan_manquant_critique'::text)) THEN 'critique'::text
                    ELSE 'attention'::text
                END AS niveau,
            format('%s bilan(s) annuel(s) non remis : %s.'::text, count(*), string_agg((o.annee)::text, ', '::text ORDER BY o.annee)) AS libelle,
            (count(*))::numeric AS valeur,
            jsonb_build_object('annees', jsonb_agg(o.annee ORDER BY o.annee)) AS detail
           FROM bancarisation.v_projet_annee_obligation o
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM bancarisation.rapport_bilan rb
                  WHERE ((rb.projet_id = o.projet_id) AND (rb.annee = o.annee) AND (rb.statut = 'valide'::text)))))
          GROUP BY o.projet_id
        ), sig_retard AS (
         SELECT oc.projet_id,
            'retard_execution'::text AS code,
                CASE
                    WHEN ((max(((EXTRACT(year FROM now()))::integer - oc.annee)))::numeric >= bancarisation.seuil('retard_critique_ans'::text)) THEN 'critique'::text
                    ELSE 'attention'::text
                END AS niveau,
            format('%s action(s) d''exercices échus non soldées, la plus ancienne de %s.'::text, count(*), min(oc.annee)) AS libelle,
            (count(*))::numeric AS valeur,
            jsonb_build_object('annee_min', min(oc.annee), 'montant_concerne', sum(f.montant_ht), 'occurrences', jsonb_agg(oc.id ORDER BY oc.annee)) AS detail
           FROM (bancarisation.occurrence oc
             LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = oc.id)))
          WHERE ((oc.statut = ANY (ARRAY['planifie'::text, 'en_cours'::text, 'a_confirmer'::text])) AND (oc.annee < (EXTRACT(year FROM now()))::integer))
          GROUP BY oc.projet_id
        ), sig_sous_conso AS (
         SELECT d.projet_id,
            'sous_consommation'::text AS code,
                CASE
                    WHEN ((sum(d.realise) / NULLIF(sum(d.prevu), (0)::numeric)) < bancarisation.seuil('sous_conso_critique'::text)) THEN 'critique'::text
                    ELSE 'attention'::text
                END AS niveau,
            format('Seuls %s%% du budget prévu des exercices échus ont été facturés (%s sur %s).'::text, round((((100)::numeric * sum(d.realise)) / NULLIF(sum(d.prevu), (0)::numeric))), round(sum(d.realise)), round(sum(d.prevu))) AS libelle,
            round((sum(d.realise) / NULLIF(sum(d.prevu), (0)::numeric)), 4) AS valeur,
            jsonb_build_object('prevu_echu', sum(d.prevu), 'realise_echu', sum(d.realise), 'non_consomme', (sum(d.prevu) - sum(d.realise))) AS detail
           FROM bancarisation.v_budget_delta_annuel d
          WHERE (d.annee < (EXTRACT(year FROM now()))::integer)
          GROUP BY d.projet_id
         HAVING ((sum(d.prevu) > (0)::numeric) AND ((sum(d.realise) / sum(d.prevu)) < bancarisation.seuil('sous_conso_seuil'::text)))
        ), derive AS (
         SELECT oc.projet_id,
            sum(f.montant_initial) AS initial,
            sum(f.montant_ht) AS prevu,
            (sum(f.montant_ht) - sum(f.montant_initial)) AS ecart,
            count(*) FILTER (WHERE ((abs((COALESCE(f.montant_ht, (0)::numeric) - f.montant_initial)) >= (1)::numeric) AND (NOT (EXISTS ( SELECT 1
                   FROM bancarisation.budget_mouvement m
                  WHERE ((m.occurrence_id = oc.id) AND (m.motif IS NOT NULL))))))) AS nb_sans_motif
           FROM (bancarisation.occurrence oc
             JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = oc.id)))
          WHERE ((f.montant_initial IS NOT NULL) AND (oc.statut <> 'supprime'::text))
          GROUP BY oc.projet_id
        ), sig_derive AS (
         SELECT d.projet_id,
            'derive_non_justifiee'::text AS code,
                CASE
                    WHEN ((abs(d.ecart) / NULLIF(d.initial, (0)::numeric)) >= bancarisation.seuil('derive_critique_pct'::text)) THEN 'critique'::text
                    ELSE 'attention'::text
                END AS niveau,
            format('Écart de %s%% au budget de référence (%s), dont %s ligne(s) sans motif renseigné.'::text, round((((100)::numeric * d.ecart) / NULLIF(d.initial, (0)::numeric))), round(d.ecart), d.nb_sans_motif) AS libelle,
            round((abs(d.ecart) / NULLIF(d.initial, (0)::numeric)), 4) AS valeur,
            jsonb_build_object('initial', d.initial, 'prevu', d.prevu, 'ecart', d.ecart, 'lignes_sans_motif', d.nb_sans_motif) AS detail
           FROM derive d
          WHERE ((d.initial > (0)::numeric) AND ((abs(d.ecart) / d.initial) >= bancarisation.seuil('derive_seuil_pct'::text)) AND (d.nb_sans_motif > 0))
        ), activite AS (
         SELECT p.id AS projet_id,
            GREATEST(COALESCE(( SELECT max(m.modifie_le) AS max
                   FROM bancarisation.budget_mouvement m
                  WHERE (m.projet_id = p.id)), p.created_at), COALESCE(( SELECT max(rb.genere_le) AS max
                   FROM bancarisation.rapport_bilan rb
                  WHERE (rb.projet_id = p.id)), p.created_at), p.updated_at) AS derniere_activite
           FROM bancarisation.projets p
          WHERE (p.statut <> ALL (ARRAY['archive'::text, 'clos'::text]))
        ), sig_silence AS (
         SELECT a.projet_id,
            'silence'::text AS code,
                CASE
                    WHEN (a.derniere_activite < (now() - make_interval(months => (bancarisation.seuil('silence_critique_mois'::text))::integer))) THEN 'critique'::text
                    ELSE 'attention'::text
                END AS niveau,
            format('Aucune activité enregistrée depuis %s mois (dernière le %s).'::text, (floor((EXTRACT(epoch FROM (now() - a.derniere_activite)) / (2592000)::numeric)))::integer, to_char(a.derniere_activite, 'DD/MM/YYYY'::text)) AS libelle,
            round((EXTRACT(epoch FROM (now() - a.derniere_activite)) / (2592000)::numeric)) AS valeur,
            jsonb_build_object('derniere_activite', a.derniere_activite) AS detail
           FROM activite a
          WHERE (a.derniere_activite < (now() - make_interval(months => (bancarisation.seuil('silence_mois'::text))::integer)))
        )
 SELECT sig_bilan.projet_id,
    sig_bilan.code,
    sig_bilan.niveau,
    sig_bilan.libelle,
    sig_bilan.valeur,
    sig_bilan.detail
   FROM sig_bilan
UNION ALL
 SELECT sig_retard.projet_id,
    sig_retard.code,
    sig_retard.niveau,
    sig_retard.libelle,
    sig_retard.valeur,
    sig_retard.detail
   FROM sig_retard
UNION ALL
 SELECT sig_sous_conso.projet_id,
    sig_sous_conso.code,
    sig_sous_conso.niveau,
    sig_sous_conso.libelle,
    sig_sous_conso.valeur,
    sig_sous_conso.detail
   FROM sig_sous_conso
UNION ALL
 SELECT sig_derive.projet_id,
    sig_derive.code,
    sig_derive.niveau,
    sig_derive.libelle,
    sig_derive.valeur,
    sig_derive.detail
   FROM sig_derive
UNION ALL
 SELECT sig_silence.projet_id,
    sig_silence.code,
    sig_silence.niveau,
    sig_silence.libelle,
    sig_silence.valeur,
    sig_silence.detail
   FROM sig_silence;


ALTER VIEW bancarisation.v_parc_signal OWNER TO postgres;

--
-- Name: v_parc_projet; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_parc_projet WITH (security_invoker='true') AS
 WITH sig AS (
         SELECT v_parc_signal.projet_id,
            count(*) FILTER (WHERE (v_parc_signal.niveau = 'critique'::text)) AS nb_critique,
            count(*) FILTER (WHERE (v_parc_signal.niveau = 'attention'::text)) AS nb_attention,
            jsonb_agg(jsonb_build_object('code', v_parc_signal.code, 'niveau', v_parc_signal.niveau, 'libelle', v_parc_signal.libelle, 'valeur', v_parc_signal.valeur, 'detail', v_parc_signal.detail) ORDER BY (v_parc_signal.niveau = 'critique'::text) DESC, v_parc_signal.code) AS signaux
           FROM bancarisation.v_parc_signal
          GROUP BY v_parc_signal.projet_id
        ), fin AS (
         SELECT v_budget_delta_annuel.projet_id,
            sum(v_budget_delta_annuel.initial) AS total_initial,
            sum(v_budget_delta_annuel.prevu) AS total_prevu,
            sum(v_budget_delta_annuel.engage) AS total_engage,
            sum(v_budget_delta_annuel.realise) AS total_realise,
            sum(v_budget_delta_annuel.prevu) FILTER (WHERE (v_budget_delta_annuel.annee = (EXTRACT(year FROM now()))::integer)) AS prevu_annee,
            sum(v_budget_delta_annuel.realise) FILTER (WHERE (v_budget_delta_annuel.annee = (EXTRACT(year FROM now()))::integer)) AS realise_annee,
            min(v_budget_delta_annuel.annee) AS premiere_annee,
            max(v_budget_delta_annuel.annee) AS derniere_annee
           FROM bancarisation.v_budget_delta_annuel
          GROUP BY v_budget_delta_annuel.projet_id
        ), occ AS (
         SELECT occurrence.projet_id,
            count(*) AS nb_occurrences,
            count(*) FILTER (WHERE (occurrence.statut = 'realise'::text)) AS nb_realisees,
            count(*) FILTER (WHERE (occurrence.statut = 'repousse'::text)) AS nb_reportees
           FROM bancarisation.occurrence
          WHERE (occurrence.statut <> 'supprime'::text)
          GROUP BY occurrence.projet_id
        ), bil AS (
         SELECT v_parc_bilan_matrice.projet_id,
            count(*) FILTER (WHERE (v_parc_bilan_matrice.etat = 'manquant'::text)) AS nb_bilans_manquants,
            count(*) FILTER (WHERE (v_parc_bilan_matrice.etat = 'valide'::text)) AS nb_bilans_valides,
            max(v_parc_bilan_matrice.annee) FILTER (WHERE (v_parc_bilan_matrice.etat = 'valide'::text)) AS dernier_bilan_valide
           FROM bancarisation.v_parc_bilan_matrice
          GROUP BY v_parc_bilan_matrice.projet_id
        )
 SELECT p.id AS projet_id,
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
            WHEN (COALESCE(sig.nb_critique, (0)::bigint) > 0) THEN 2
            WHEN (COALESCE(sig.nb_attention, (0)::bigint) > 0) THEN 1
            ELSE 0
        END AS gravite,
    COALESCE(sig.nb_critique, (0)::bigint) AS nb_signaux_critiques,
    COALESCE(sig.nb_attention, (0)::bigint) AS nb_signaux_attention,
    COALESCE(sig.signaux, '[]'::jsonb) AS signaux,
        CASE
            WHEN p.partager_budget_dreal THEN COALESCE(fin.total_initial, (0)::numeric)
            ELSE (0)::numeric
        END AS total_initial,
        CASE
            WHEN p.partager_budget_dreal THEN COALESCE(fin.total_prevu, (0)::numeric)
            ELSE (0)::numeric
        END AS total_prevu,
        CASE
            WHEN p.partager_budget_dreal THEN COALESCE(fin.total_engage, (0)::numeric)
            ELSE (0)::numeric
        END AS total_engage,
        CASE
            WHEN p.partager_budget_dreal THEN COALESCE(fin.total_realise, (0)::numeric)
            ELSE (0)::numeric
        END AS total_realise,
        CASE
            WHEN p.partager_budget_dreal THEN (COALESCE(fin.total_prevu, (0)::numeric) - COALESCE(fin.total_initial, (0)::numeric))
            ELSE (0)::numeric
        END AS delta_total,
        CASE
            WHEN p.partager_budget_dreal THEN COALESCE(fin.prevu_annee, (0)::numeric)
            ELSE (0)::numeric
        END AS prevu_annee_courante,
        CASE
            WHEN p.partager_budget_dreal THEN COALESCE(fin.realise_annee, (0)::numeric)
            ELSE (0)::numeric
        END AS realise_annee_courante,
        CASE
            WHEN p.partager_budget_dreal THEN fin.premiere_annee
            ELSE NULL::integer
        END AS premiere_annee,
        CASE
            WHEN p.partager_budget_dreal THEN fin.derniere_annee
            ELSE NULL::integer
        END AS derniere_annee,
    COALESCE(occ.nb_occurrences, (0)::bigint) AS nb_occurrences,
    COALESCE(occ.nb_realisees, (0)::bigint) AS nb_occurrences_realisees,
    COALESCE(occ.nb_reportees, (0)::bigint) AS nb_occurrences_reportees,
    COALESCE(bil.nb_bilans_valides, (0)::bigint) AS nb_bilans_valides,
    COALESCE(bil.nb_bilans_manquants, (0)::bigint) AS nb_bilans_manquants,
    bil.dernier_bilan_valide,
    p.partager_budget_dreal
   FROM (((((bancarisation.projets p
     JOIN bancarisation.organisations org ON ((org.id = p.organisation_id)))
     LEFT JOIN sig ON ((sig.projet_id = p.id)))
     LEFT JOIN fin ON ((fin.projet_id = p.id)))
     LEFT JOIN occ ON ((occ.projet_id = p.id)))
     LEFT JOIN bil ON ((bil.projet_id = p.id)));


ALTER VIEW bancarisation.v_parc_projet OWNER TO postgres;

--
-- Name: v_parc_organisation; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_parc_organisation WITH (security_invoker='true') AS
 SELECT o.id AS organisation_id,
    o.nom AS organisation_nom,
    count(v.projet_id) AS nb_projets,
    count(*) FILTER (WHERE (v.gravite = 2)) AS nb_projets_critiques,
    count(*) FILTER (WHERE (v.gravite = 1)) AS nb_projets_attention,
    count(*) FILTER (WHERE (v.gravite = 0)) AS nb_projets_conformes,
    COALESCE(sum(v.total_prevu), (0)::numeric) AS total_prevu,
    COALESCE(sum(v.total_realise), (0)::numeric) AS total_realise,
    COALESCE(sum(v.total_initial), (0)::numeric) AS total_initial,
    COALESCE(sum(v.delta_total), (0)::numeric) AS delta_total,
    sum(v.nb_bilans_valides) AS nb_bilans_valides,
    sum(v.nb_bilans_manquants) AS nb_bilans_manquants,
        CASE
            WHEN ((sum(v.nb_bilans_valides) + sum(v.nb_bilans_manquants)) > (0)::numeric) THEN round((sum(v.nb_bilans_valides) / (sum(v.nb_bilans_valides) + sum(v.nb_bilans_manquants))), 4)
            ELSE NULL::numeric
        END AS taux_remise_bilans,
        CASE
            WHEN (sum(v.total_prevu) > (0)::numeric) THEN round((sum(v.total_realise) / sum(v.total_prevu)), 4)
            ELSE NULL::numeric
        END AS taux_execution,
        CASE
            WHEN (sum(v.nb_occurrences) > (0)::numeric) THEN round((sum(v.nb_occurrences_realisees) / sum(v.nb_occurrences)), 4)
            ELSE NULL::numeric
        END AS taux_actions_realisees
   FROM (bancarisation.organisations o
     LEFT JOIN bancarisation.v_parc_projet v ON ((v.organisation_id = o.id)))
  GROUP BY o.id, o.nom;


ALTER VIEW bancarisation.v_parc_organisation OWNER TO postgres;

--
-- Name: v_portefeuille_organisation; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_portefeuille_organisation WITH (security_invoker='true') AS
 SELECT o.id AS organisation_id,
    o.nom AS organisation_nom,
    o.code AS organisation_code,
    o.type AS organisation_type,
    o.parent_id,
    count(p.id) AS nb_dossiers_propres,
    ( SELECT count(*) AS count
           FROM (bancarisation.projets px
             JOIN bancarisation.organisation_closure c ON ((c.descendant_id = px.organisation_id)))
          WHERE (c.ancetre_id = o.id)) AS nb_dossiers_consolide,
    count(p.id) FILTER (WHERE (p.date_fin >= CURRENT_DATE)) AS nb_en_cours,
    count(p.id) FILTER (WHERE (p.date_fin < CURRENT_DATE)) AS nb_echus,
    count(DISTINCT p.departement) AS nb_departements,
    min(p.date_decision) AS premiere_decision,
    max(p.date_fin) AS horizon_max,
    round(avg(p.duree_annees), 1) AS duree_moyenne_annees
   FROM (bancarisation.organisations o
     LEFT JOIN bancarisation.projets p ON ((p.organisation_id = o.id)))
  WHERE (NOT o.archivee)
  GROUP BY o.id, o.nom, o.code, o.type, o.parent_id;


ALTER VIEW bancarisation.v_portefeuille_organisation OWNER TO postgres;

--
-- Name: v_prescription_couverture; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_prescription_couverture WITH (security_invoker='true') AS
 WITH liens AS (
         SELECT prescription_couverture.prescription_id,
            count(DISTINCT prescription_couverture.echeance_id) AS nb_echeances
           FROM bancarisation.prescription_couverture
          WHERE (prescription_couverture.mode = 'user'::text)
          GROUP BY prescription_couverture.prescription_id
        ), occ AS (
         SELECT c.prescription_id,
            count(DISTINCT o_1.id) AS nb_occurrences,
            count(DISTINCT o_1.id) FILTER (WHERE (o_1.statut = 'realise'::text)) AS nb_realisees,
            count(DISTINCT o_1.id) FILTER (WHERE ((o_1.statut = ANY (ARRAY['a_confirmer'::text, 'planifie'::text, 'en_cours'::text, 'repousse'::text])) AND ((((make_date(o_1.annee, COALESCE(o_1.mois_fin, 12), 1) + '1 mon'::interval) - '1 day'::interval))::date < CURRENT_DATE))) AS nb_en_retard,
            min(make_date(o_1.annee, COALESCE(o_1.mois_debut, 1), 1)) FILTER (WHERE ((o_1.statut = ANY (ARRAY['a_confirmer'::text, 'planifie'::text, 'en_cours'::text, 'repousse'::text])) AND (make_date(o_1.annee, COALESCE(o_1.mois_debut, 1), 1) >= CURRENT_DATE))) AS prochaine_echeance
           FROM (bancarisation.prescription_couverture c
             JOIN bancarisation.occurrence o_1 ON (((o_1.echeance_id = c.echeance_id) AND (o_1.statut <> 'supprime'::text))))
          WHERE (c.mode = 'user'::text)
          GROUP BY c.prescription_id
        )
 SELECT p.id AS prescription_id,
    a.projet_id,
    COALESCE(l.nb_echeances, (0)::bigint) AS nb_echeances,
    COALESCE(o.nb_occurrences, (0)::bigint) AS nb_occurrences,
    COALESCE(o.nb_realisees, (0)::bigint) AS nb_realisees,
    COALESCE(o.nb_en_retard, (0)::bigint) AS nb_en_retard,
    o.prochaine_echeance,
        CASE
            WHEN (COALESCE(l.nb_echeances, (0)::bigint) = 0) THEN 'non_couverte'::text
            ELSE 'couverte'::text
        END AS couverture
   FROM (((bancarisation.arrete_prescription p
     JOIN bancarisation.arrete a ON ((a.id = p.arrete_id)))
     LEFT JOIN liens l ON ((l.prescription_id = p.id)))
     LEFT JOIN occ o ON ((o.prescription_id = p.id)));


ALTER VIEW bancarisation.v_prescription_couverture OWNER TO postgres;

--
-- Name: v_prochaine_mesure_par_ug; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_prochaine_mesure_par_ug WITH (security_invoker='true') AS
 WITH occ_expanded AS (
         SELECT o.projet_id,
            o.id AS occurrence_id,
            o.code,
            o.titre,
            o.statut,
            o.categorie,
            unnest(o.ug_ids) AS ug_id,
            COALESCE(o.mois_fin, o.mois_debut, 12) AS mois_fin_eff,
                CASE
                    WHEN ((o.mois_debut IS NOT NULL) AND (o.mois_fin IS NOT NULL) AND (o.mois_debut > o.mois_fin)) THEN (o.annee + 1)
                    ELSE o.annee
                END AS annee_fin,
            (((make_date(
                CASE
                    WHEN ((o.mois_debut IS NOT NULL) AND (o.mois_fin IS NOT NULL) AND (o.mois_debut > o.mois_fin)) THEN (o.annee + 1)
                    ELSE o.annee
                END, COALESCE(o.mois_fin, o.mois_debut, 12), 1) + '1 mon'::interval) - '1 day'::interval))::date AS deadline
           FROM bancarisation.occurrence o
          WHERE ((o.statut <> ALL (ARRAY['realise'::text, 'supprime'::text])) AND (o.ug_ids IS NOT NULL) AND (cardinality(o.ug_ids) > 0))
        ), ranked AS (
         SELECT occ_expanded.projet_id,
            occ_expanded.ug_id,
            occ_expanded.occurrence_id,
            occ_expanded.code,
            occ_expanded.titre,
            occ_expanded.categorie,
            occ_expanded.deadline,
            occ_expanded.mois_fin_eff,
            occ_expanded.annee_fin,
            (occ_expanded.deadline - CURRENT_DATE) AS jours_restants,
            row_number() OVER (PARTITION BY occ_expanded.projet_id, occ_expanded.ug_id ORDER BY occ_expanded.deadline, occ_expanded.code) AS rn
           FROM occ_expanded
          WHERE ((occ_expanded.deadline >= CURRENT_DATE) AND (occ_expanded.ug_id IS NOT NULL) AND (occ_expanded.ug_id <> ''::text))
        )
 SELECT projet_id,
    ug_id,
    occurrence_id,
    code AS prochaine_code,
    titre AS prochaine_titre,
    categorie AS prochaine_categorie,
    deadline AS prochaine_deadline,
    mois_fin_eff AS prochaine_mois_fin,
    annee_fin AS prochaine_annee_fin,
    jours_restants,
        CASE
            WHEN (jours_restants <= 14) THEN 'urgent'::text
            WHEN (jours_restants <= 45) THEN 'soon'::text
            ELSE 'ok'::text
        END AS urgence
   FROM ranked
  WHERE (rn = 1);


ALTER VIEW bancarisation.v_prochaine_mesure_par_ug OWNER TO postgres;

--
-- Name: VIEW v_prochaine_mesure_par_ug; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON VIEW bancarisation.v_prochaine_mesure_par_ug IS 'Prochaine mesure à échéance par UG (deadline = fin de mois de fenêtre).';


--
-- Name: v_projet_activite; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_projet_activite WITH (security_invoker='true') AS
 WITH demandes AS (
         SELECT d.projet_id,
            d.cree_le AS survenu_le,
            'demande_ouverte'::text AS type,
            d.auteur AS acteur,
            format('%s : %s'::text,
                CASE d.auteur
                    WHEN 'dreal'::text THEN 'Demande du service instructeur'::text
                    ELSE 'Signalement du bureau d''études'::text
                END, d.objet) AS libelle,
            d.id AS demande_id
           FROM bancarisation.demande d
        ), messages AS (
         SELECT d.projet_id,
            m.cree_le,
            'message'::text AS "?column?",
            m.acteur,
            format('%s : %s'::text,
                CASE m.acteur
                    WHEN 'dreal'::text THEN 'Message du service instructeur'::text
                    ELSE 'Réponse du bureau d''études'::text
                END, ("left"(m.corps, 120) ||
                CASE
                    WHEN (length(m.corps) > 120) THEN '…'::text
                    ELSE ''::text
                END)) AS format,
            d.id
           FROM (bancarisation.demande_message m
             JOIN bancarisation.demande d ON ((d.id = m.demande_id)))
        ), clotures AS (
         SELECT d.projet_id,
            d.clos_le,
            'demande_close'::text AS "?column?",
            'dreal'::text AS "?column?",
            format('Demande close : %s'::text, d.objet) AS format,
            d.id
           FROM bancarisation.demande d
          WHERE (d.clos_le IS NOT NULL)
        ), bilans AS (
         SELECT rb.projet_id,
            rb.valide_le,
            'bilan_valide'::text AS "?column?",
            'be'::text AS "?column?",
            format('Bilan financier %s validé (version %s)'::text, rb.annee, rb.version) AS format,
            NULL::uuid AS uuid
           FROM bancarisation.rapport_bilan rb
          WHERE ((rb.statut = 'valide'::text) AND (rb.valide_le IS NOT NULL))
        ), baselines AS (
         SELECT bb.projet_id,
            bb.figee_le,
            'baseline_figee'::text AS "?column?",
            'be'::text AS "?column?",
            format('Budget de référence figé : %s'::text, bb.libelle) AS format,
            NULL::uuid AS uuid
           FROM bancarisation.budget_baseline bb
        )
 SELECT demandes.projet_id,
    demandes.survenu_le,
    demandes.type,
    demandes.acteur,
    demandes.libelle,
    demandes.demande_id
   FROM demandes
UNION ALL
 SELECT messages.projet_id,
    messages.cree_le AS survenu_le,
    messages."?column?" AS type,
    messages.acteur,
    messages.format AS libelle,
    messages.id AS demande_id
   FROM messages
UNION ALL
 SELECT clotures.projet_id,
    clotures.clos_le AS survenu_le,
    clotures."?column?" AS type,
    clotures."?column?_1" AS acteur,
    clotures.format AS libelle,
    clotures.id AS demande_id
   FROM clotures clotures(projet_id, clos_le, "?column?", "?column?_1", format, id)
UNION ALL
 SELECT bilans.projet_id,
    bilans.valide_le AS survenu_le,
    bilans."?column?" AS type,
    bilans."?column?_1" AS acteur,
    bilans.format AS libelle,
    bilans.uuid AS demande_id
   FROM bilans bilans(projet_id, valide_le, "?column?", "?column?_1", format, uuid)
UNION ALL
 SELECT baselines.projet_id,
    baselines.figee_le AS survenu_le,
    baselines."?column?" AS type,
    baselines."?column?_1" AS acteur,
    baselines.format AS libelle,
    baselines.uuid AS demande_id
   FROM baselines baselines(projet_id, figee_le, "?column?", "?column?_1", format, uuid);


ALTER VIEW bancarisation.v_projet_activite OWNER TO postgres;

--
-- Name: v_projet_liste_organisation; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_projet_liste_organisation WITH (security_invoker='true') AS
 SELECT p.id AS projet_id,
    o.id AS agence_id,
    o.nom AS agence_nom,
    o.code AS agence_code,
    racine.id AS structure_id,
    racine.nom AS structure_nom,
    mo.nom AS maitre_ouvrage_nom
   FROM (((bancarisation.projets p
     LEFT JOIN bancarisation.organisations o ON ((o.id = p.organisation_id)))
     LEFT JOIN bancarisation.organisations racine ON ((racine.id = COALESCE(o.parent_id, o.id))))
     LEFT JOIN LATERAL ( SELECT org.nom
           FROM (bancarisation.projet_acces a
             JOIN bancarisation.organisations org ON ((org.id = a.organisation_id)))
          WHERE ((a.projet_id = p.id) AND (a.qualite = 'maitre_ouvrage'::text))
         LIMIT 1) mo ON (true));


ALTER VIEW bancarisation.v_projet_liste_organisation OWNER TO postgres;

--
-- Name: v_projet_liste_resume; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_projet_liste_resume WITH (security_invoker='true') AS
 WITH occ_deadline AS (
         SELECT o.projet_id,
            o.id,
            o.code,
            o.titre,
            o.statut,
            COALESCE(o.mois_fin, o.mois_debut, 12) AS mois_fin_eff,
                CASE
                    WHEN ((o.mois_debut IS NOT NULL) AND (o.mois_fin IS NOT NULL) AND (o.mois_debut > o.mois_fin)) THEN (o.annee + 1)
                    ELSE o.annee
                END AS annee_fin,
            (((make_date(
                CASE
                    WHEN ((o.mois_debut IS NOT NULL) AND (o.mois_fin IS NOT NULL) AND (o.mois_debut > o.mois_fin)) THEN (o.annee + 1)
                    ELSE o.annee
                END, COALESCE(o.mois_fin, o.mois_debut, 12), 1) + '1 mon'::interval) - '1 day'::interval))::date AS deadline
           FROM bancarisation.occurrence o
          WHERE (o.statut <> ALL (ARRAY['realise'::text, 'supprime'::text]))
        ), next_occ AS (
         SELECT DISTINCT ON (occ_deadline.projet_id) occ_deadline.projet_id,
            occ_deadline.code,
            occ_deadline.titre,
            occ_deadline.deadline,
            occ_deadline.mois_fin_eff,
            occ_deadline.annee_fin
           FROM occ_deadline
          WHERE (occ_deadline.deadline >= CURRENT_DATE)
          ORDER BY occ_deadline.projet_id, occ_deadline.deadline, occ_deadline.code
        ), counts AS (
         SELECT occurrence.projet_id,
            count(*) FILTER (WHERE (occurrence.statut <> 'supprime'::text)) AS nb_occurrences,
            count(*) FILTER (WHERE (occurrence.statut = 'realise'::text)) AS nb_realisees
           FROM bancarisation.occurrence
          GROUP BY occurrence.projet_id
        )
 SELECT p.id,
    p.organisation_id,
    p.nom,
    p.reference_interne,
    p.commune,
    p.departement,
    p.description,
    p.type_procedure,
    p.date_decision,
    p.duree_annees,
    p.date_fin,
    p.statut,
    p.created_at,
    p.updated_at,
    (COALESCE(c.nb_occurrences, (0)::bigint))::integer AS nb_occurrences,
    (COALESCE(c.nb_realisees, (0)::bigint))::integer AS nb_realisees,
    (0)::numeric AS dette_m2,
    n.code AS prochaine_code,
    n.titre AS prochaine_titre,
    n.deadline AS prochaine_deadline,
        CASE
            WHEN (n.deadline IS NULL) THEN NULL::integer
            ELSE (n.deadline - CURRENT_DATE)
        END AS prochaine_jours_restants,
    n.mois_fin_eff AS prochaine_mois_fin,
    n.annee_fin AS prochaine_annee_fin
   FROM ((bancarisation.projets p
     LEFT JOIN counts c ON ((c.projet_id = p.id)))
     LEFT JOIN next_occ n ON ((n.projet_id = p.id)));


ALTER VIEW bancarisation.v_projet_liste_resume OWNER TO postgres;

--
-- Name: VIEW v_projet_liste_resume; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON VIEW bancarisation.v_projet_liste_resume IS 'Liste projets enrichie : réalisées / total + prochaine occurrence (J-x).';


--
-- Name: v_projet_prestataires; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_projet_prestataires WITH (security_invoker='true') AS
 WITH depuis_occurrences AS (
         SELECT o.projet_id,
            o.prestataire_id,
            count(*) FILTER (WHERE (o.statut <> 'supprime'::text)) AS nb_occurrences,
            count(*) FILTER (WHERE (o.statut = 'realise'::text)) AS nb_realisees,
            sum(f.montant_ht) FILTER (WHERE (o.statut <> 'supprime'::text)) AS total_prevu_ht,
            sum(f.montant_engage) FILTER (WHERE (o.statut <> 'supprime'::text)) AS total_engage_ht,
            sum(f.montant_realise) FILTER (WHERE (o.statut <> 'supprime'::text)) AS total_realise_ht,
            min(o.annee) FILTER (WHERE (o.statut <> 'supprime'::text)) AS annee_min,
            max(o.annee) FILTER (WHERE (o.statut <> 'supprime'::text)) AS annee_max
           FROM (bancarisation.occurrence o
             LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
          WHERE (o.prestataire_id IS NOT NULL)
          GROUP BY o.projet_id, o.prestataire_id
        ), depuis_projet AS (
         SELECT pp.projet_id,
            pp.prestataire_id,
            pp.role,
            pp.created_at AS rattache_le
           FROM bancarisation.projet_prestataire pp
        )
 SELECT COALESCE(occ.projet_id, prj.projet_id) AS projet_id,
    COALESCE(occ.prestataire_id, prj.prestataire_id) AS prestataire_id,
    p.nom AS prestataire_nom,
    p.siret,
    p.siret_norm,
    p.forme_juridique,
    p.adresse,
    p.code_postal,
    p.commune,
    p.departement,
    p.email,
    p.telephone,
    p.interlocuteur,
    p.specialites,
    p.categories_mesure,
    p.actif,
    prj.role,
    prj.rattache_le,
        CASE
            WHEN ((occ.prestataire_id IS NOT NULL) AND (prj.prestataire_id IS NOT NULL)) THEN 'les_deux'::text
            WHEN (occ.prestataire_id IS NOT NULL) THEN 'occurrence'::text
            ELSE 'projet'::text
        END AS source,
    COALESCE(occ.nb_occurrences, (0)::bigint) AS nb_occurrences,
    COALESCE(occ.nb_realisees, (0)::bigint) AS nb_realisees,
    occ.total_prevu_ht,
    occ.total_engage_ht,
    occ.total_realise_ht,
    occ.annee_min,
    occ.annee_max,
    p.code_naf,
    p.tva
   FROM ((depuis_occurrences occ
     FULL JOIN depuis_projet prj ON (((prj.projet_id = occ.projet_id) AND (prj.prestataire_id = occ.prestataire_id))))
     JOIN bancarisation.prestataires p ON ((p.id = COALESCE(occ.prestataire_id, prj.prestataire_id))));


ALTER VIEW bancarisation.v_projet_prestataires OWNER TO postgres;

--
-- Name: v_registre_ventes; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_registre_ventes WITH (security_invoker='true') AS
 SELECT sa.projet_id,
    sa.numero_agrement,
    l.id AS lot_id,
    l.libelle AS lot_libelle,
    l.composante_milieu,
    v.id AS vente_id,
    v.quantite,
    v.usage_credit,
    v.acheteur_nom,
    v.acheteur_siren,
    v.arrete_acheteur,
    v.prix_unitaire_ht,
    v.prix_total_ht,
    v.date_vente,
    v.date_affectation,
    v.date_reservation,
    v.statut,
    v.annee_encaissement,
    v.certificat_doc_id
   FROM ((bancarisation.credit_vente v
     JOIN bancarisation.credit_lot l ON ((l.id = v.lot_id)))
     JOIN bancarisation.site_agrement sa ON ((sa.id = l.site_agrement_id)))
  WHERE (v.statut <> 'annule'::bancarisation.statut_vente);


ALTER VIEW bancarisation.v_registre_ventes OWNER TO postgres;

--
-- Name: v_tresorerie_site; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_tresorerie_site WITH (security_invoker='true') AS
 WITH dep AS (
         SELECT o.projet_id,
            o.annee,
            sum(f.montant_ht) AS depenses
           FROM (bancarisation.occurrence o
             LEFT JOIN bancarisation.occurrence_finance f ON ((f.occurrence_id = o.id)))
          WHERE (o.statut <> 'supprime'::text)
          GROUP BY o.projet_id, o.annee
        ), rec AS (
         SELECT sa.projet_id,
            v.annee_encaissement AS annee,
            sum(v.prix_total_ht) AS recettes
           FROM ((bancarisation.credit_vente v
             JOIN bancarisation.credit_lot l ON ((l.id = v.lot_id)))
             JOIN bancarisation.site_agrement sa ON ((sa.id = l.site_agrement_id)))
          WHERE ((v.statut <> 'annule'::bancarisation.statut_vente) AND (v.annee_encaissement IS NOT NULL))
          GROUP BY sa.projet_id, v.annee_encaissement
        )
 SELECT COALESCE(d.projet_id, r.projet_id) AS projet_id,
    COALESCE(d.annee, r.annee) AS annee,
    d.depenses,
    r.recettes,
    (r.recettes - d.depenses) AS solde
   FROM (dep d
     FULL JOIN rec r ON (((d.projet_id = r.projet_id) AND (d.annee = r.annee))));


ALTER VIEW bancarisation.v_tresorerie_site OWNER TO postgres;

--
-- Name: v_ug_lin; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_ug_lin WITH (security_invoker='true') AS
 SELECT projet_id,
    ug_id,
    max(libelle) AS libelle,
    public.st_union(geom) AS geom,
    public.st_union(geom_3857) AS geom_3857,
    count(*) AS nb_entites,
    max(description) AS description
   FROM bancarisation.unites_de_gestion_lin
  WHERE (statut = 'ug'::text)
  GROUP BY projet_id, ug_id;


ALTER VIEW bancarisation.v_ug_lin OWNER TO postgres;

--
-- Name: VIEW v_ug_lin; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON VIEW bancarisation.v_ug_lin IS 'Une ligne par UG linéaire : union des entités statut=ug.';


--
-- Name: v_ug_pct; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_ug_pct WITH (security_invoker='true') AS
 SELECT projet_id,
    ug_id,
    max(libelle) AS libelle,
    public.st_union(geom) AS geom,
    public.st_union(geom_3857) AS geom_3857,
    count(*) AS nb_entites,
    max(description) AS description
   FROM bancarisation.unites_de_gestion_pct
  WHERE (statut = 'ug'::text)
  GROUP BY projet_id, ug_id;


ALTER VIEW bancarisation.v_ug_pct OWNER TO postgres;

--
-- Name: VIEW v_ug_pct; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON VIEW bancarisation.v_ug_pct IS 'Une ligne par UG ponctuelle : union des entités statut=ug.';


--
-- Name: v_ug_surf; Type: VIEW; Schema: bancarisation; Owner: postgres
--

CREATE VIEW bancarisation.v_ug_surf WITH (security_invoker='true') AS
 SELECT projet_id,
    ug_id,
    max(libelle) AS libelle,
    public.st_union(geom) AS geom,
    public.st_union(geom_3857) AS geom_3857,
    count(*) AS nb_entites,
    max(description) AS description
   FROM bancarisation.unites_de_gestion_surf
  WHERE (statut = 'ug'::text)
  GROUP BY projet_id, ug_id;


ALTER VIEW bancarisation.v_ug_surf OWNER TO postgres;

--
-- Name: VIEW v_ug_surf; Type: COMMENT; Schema: bancarisation; Owner: postgres
--

COMMENT ON VIEW bancarisation.v_ug_surf IS 'Une ligne par UG surfacique : union des entités statut=ug.';


--
-- Name: plan_cao_entite id; Type: DEFAULT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_entite ALTER COLUMN id SET DEFAULT nextval('bancarisation.plan_cao_entite_id_seq'::regclass);


--
-- Name: acte_dreal acte_dreal_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.acte_dreal
    ADD CONSTRAINT acte_dreal_pkey PRIMARY KEY (id);


--
-- Name: action_etape action_etape_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_etape
    ADD CONSTRAINT action_etape_pkey PRIMARY KEY (id);


--
-- Name: action_fiche_arrete action_fiche_arrete_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_fiche_arrete
    ADD CONSTRAINT action_fiche_arrete_pkey PRIMARY KEY (action_fiche_id, arrete_id);


--
-- Name: action_fiche action_fiche_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_fiche
    ADD CONSTRAINT action_fiche_pkey PRIMARY KEY (id);


--
-- Name: action_fiche action_fiche_projet_id_cle_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_fiche
    ADD CONSTRAINT action_fiche_projet_id_cle_key UNIQUE (projet_id, cle);


--
-- Name: annotation_terrain annotation_terrain_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.annotation_terrain
    ADD CONSTRAINT annotation_terrain_pkey PRIMARY KEY (id);


--
-- Name: arrete arrete_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.arrete
    ADD CONSTRAINT arrete_pkey PRIMARY KEY (id);


--
-- Name: arrete_prescription arrete_prescription_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.arrete_prescription
    ADD CONSTRAINT arrete_prescription_pkey PRIMARY KEY (id);


--
-- Name: bilan_suivi bilan_suivi_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.bilan_suivi
    ADD CONSTRAINT bilan_suivi_pkey PRIMARY KEY (id);


--
-- Name: bilan_suivi bilan_suivi_projet_id_annee_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.bilan_suivi
    ADD CONSTRAINT bilan_suivi_projet_id_annee_key UNIQUE (projet_id, annee);


--
-- Name: budget_baseline budget_baseline_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.budget_baseline
    ADD CONSTRAINT budget_baseline_pkey PRIMARY KEY (id);


--
-- Name: budget_import budget_import_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.budget_import
    ADD CONSTRAINT budget_import_pkey PRIMARY KEY (id);


--
-- Name: budget_mouvement budget_mouvement_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.budget_mouvement
    ADD CONSTRAINT budget_mouvement_pkey PRIMARY KEY (id);


--
-- Name: cadastre_parcelle cadastre_parcelle_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.cadastre_parcelle
    ADD CONSTRAINT cadastre_parcelle_pkey PRIMARY KEY (id);


--
-- Name: cadastre_parcelle cadastre_parcelle_projet_id_ug_id_idu_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.cadastre_parcelle
    ADD CONSTRAINT cadastre_parcelle_projet_id_ug_id_idu_key UNIQUE (projet_id, ug_id, idu);


--
-- Name: cadastre_parcelle_ug cadastre_parcelle_ug_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.cadastre_parcelle_ug
    ADD CONSTRAINT cadastre_parcelle_ug_pkey PRIMARY KEY (projet_id, ug_id, idu);


--
-- Name: checklist_modele_etape checklist_modele_etape_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.checklist_modele_etape
    ADD CONSTRAINT checklist_modele_etape_pkey PRIMARY KEY (id);


--
-- Name: checklist_modele checklist_modele_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.checklist_modele
    ADD CONSTRAINT checklist_modele_pkey PRIMARY KEY (id);


--
-- Name: constat_controle constat_controle_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.constat_controle
    ADD CONSTRAINT constat_controle_pkey PRIMARY KEY (id);


--
-- Name: constat_gain constat_gain_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.constat_gain
    ADD CONSTRAINT constat_gain_pkey PRIMARY KEY (id);


--
-- Name: couche_non_georeferencee couche_non_georeferencee_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.couche_non_georeferencee
    ADD CONSTRAINT couche_non_georeferencee_pkey PRIMARY KEY (id);


--
-- Name: couche_non_georeferencee couche_non_georeferencee_projet_id_depot_id_couche_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.couche_non_georeferencee
    ADD CONSTRAINT couche_non_georeferencee_projet_id_depot_id_couche_key UNIQUE (projet_id, depot_id, couche);


--
-- Name: credit_lot credit_lot_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.credit_lot
    ADD CONSTRAINT credit_lot_pkey PRIMARY KEY (id);


--
-- Name: credit_palier credit_palier_lot_id_annee_palier_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.credit_palier
    ADD CONSTRAINT credit_palier_lot_id_annee_palier_key UNIQUE (lot_id, annee_palier);


--
-- Name: credit_palier credit_palier_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.credit_palier
    ADD CONSTRAINT credit_palier_pkey PRIMARY KEY (id);


--
-- Name: credit_vente credit_vente_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.credit_vente
    ADD CONSTRAINT credit_vente_pkey PRIMARY KEY (id);


--
-- Name: demande_message demande_message_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.demande_message
    ADD CONSTRAINT demande_message_pkey PRIMARY KEY (id);


--
-- Name: demande demande_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.demande
    ADD CONSTRAINT demande_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: droit_documents droit_documents_droit_id_document_id_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.droit_documents
    ADD CONSTRAINT droit_documents_droit_id_document_id_key UNIQUE (droit_id, document_id);


--
-- Name: droit_documents droit_documents_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.droit_documents
    ADD CONSTRAINT droit_documents_pkey PRIMARY KEY (id);


--
-- Name: droits_fonciers droits_fonciers_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.droits_fonciers
    ADD CONSTRAINT droits_fonciers_pkey PRIMARY KEY (id);


--
-- Name: echeance echeance_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.echeance
    ADD CONSTRAINT echeance_pkey PRIMARY KEY (id);


--
-- Name: echeance echeance_projet_id_cle_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.echeance
    ADD CONSTRAINT echeance_projet_id_cle_key UNIQUE (projet_id, cle);


--
-- Name: emprise_projet emprise_projet_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.emprise_projet
    ADD CONSTRAINT emprise_projet_pkey PRIMARY KEY (id);


--
-- Name: export_geomce export_geomce_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.export_geomce
    ADD CONSTRAINT export_geomce_pkey PRIMARY KEY (id);


--
-- Name: export_projet export_projet_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.export_projet
    ADD CONSTRAINT export_projet_pkey PRIMARY KEY (id);


--
-- Name: extraction_import extraction_import_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.extraction_import
    ADD CONSTRAINT extraction_import_pkey PRIMARY KEY (id);


--
-- Name: geometrie_mouvement geometrie_mouvement_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.geometrie_mouvement
    ADD CONSTRAINT geometrie_mouvement_pkey PRIMARY KEY (id);


--
-- Name: invitation invitation_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.invitation
    ADD CONSTRAINT invitation_pkey PRIMARY KEY (id);


--
-- Name: journal_actions journal_actions_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.journal_actions
    ADD CONSTRAINT journal_actions_pkey PRIMARY KEY (id);


--
-- Name: journal_audit journal_audit_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.journal_audit
    ADD CONSTRAINT journal_audit_pkey PRIMARY KEY (id);


--
-- Name: ligne_budget ligne_budget_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ligne_budget
    ADD CONSTRAINT ligne_budget_pkey PRIMARY KEY (id);


--
-- Name: membre_organisation membre_organisation_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.membre_organisation
    ADD CONSTRAINT membre_organisation_pkey PRIMARY KEY (id);


--
-- Name: membre_organisation membre_organisation_utilisateur_id_organisation_id_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.membre_organisation
    ADD CONSTRAINT membre_organisation_utilisateur_id_organisation_id_key UNIQUE (utilisateur_id, organisation_id);


--
-- Name: membre membre_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.membre
    ADD CONSTRAINT membre_pkey PRIMARY KEY (user_id);


--
-- Name: note_interne note_interne_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.note_interne
    ADD CONSTRAINT note_interne_pkey PRIMARY KEY (id);


--
-- Name: occurrence_finance occurrence_finance_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence_finance
    ADD CONSTRAINT occurrence_finance_pkey PRIMARY KEY (occurrence_id);


--
-- Name: occurrence occurrence_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence
    ADD CONSTRAINT occurrence_pkey PRIMARY KEY (id);


--
-- Name: occurrence_responsable occurrence_responsable_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence_responsable
    ADD CONSTRAINT occurrence_responsable_pkey PRIMARY KEY (id);


--
-- Name: occurrence_responsable occurrence_responsable_unique; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence_responsable
    ADD CONSTRAINT occurrence_responsable_unique UNIQUE (occurrence_id, personne_id);


--
-- Name: organisation_closure organisation_closure_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.organisation_closure
    ADD CONSTRAINT organisation_closure_pkey PRIMARY KEY (ancetre_id, descendant_id);


--
-- Name: organisations organisations_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.organisations
    ADD CONSTRAINT organisations_pkey PRIMARY KEY (id);


--
-- Name: parametre_signal parametre_signal_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.parametre_signal
    ADD CONSTRAINT parametre_signal_pkey PRIMARY KEY (code);


--
-- Name: parcelles parcelles_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.parcelles
    ADD CONSTRAINT parcelles_pkey PRIMARY KEY (id);


--
-- Name: personnes personnes_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.personnes
    ADD CONSTRAINT personnes_pkey PRIMARY KEY (id);


--
-- Name: plan_cao_calque plan_cao_calque_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_calque
    ADD CONSTRAINT plan_cao_calque_pkey PRIMARY KEY (id);


--
-- Name: plan_cao_calque plan_cao_calque_plan_id_nom_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_calque
    ADD CONSTRAINT plan_cao_calque_plan_id_nom_key UNIQUE (plan_id, nom);


--
-- Name: plan_cao_entite plan_cao_entite_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_entite
    ADD CONSTRAINT plan_cao_entite_pkey PRIMARY KEY (id);


--
-- Name: plan_cao_groupe plan_cao_groupe_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_groupe
    ADD CONSTRAINT plan_cao_groupe_pkey PRIMARY KEY (plan_id, id);


--
-- Name: plan_cao plan_cao_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao
    ADD CONSTRAINT plan_cao_pkey PRIMARY KEY (id);


--
-- Name: prescription_couverture prescription_couverture_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.prescription_couverture
    ADD CONSTRAINT prescription_couverture_pkey PRIMARY KEY (prescription_id, echeance_id);


--
-- Name: prestataires prestataires_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.prestataires
    ADD CONSTRAINT prestataires_pkey PRIMARY KEY (id);


--
-- Name: profils profils_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.profils
    ADD CONSTRAINT profils_pkey PRIMARY KEY (utilisateur_id);


--
-- Name: projet_acces projet_acces_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_acces
    ADD CONSTRAINT projet_acces_pkey PRIMARY KEY (id);


--
-- Name: projet_contact projet_contact_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_contact
    ADD CONSTRAINT projet_contact_pkey PRIMARY KEY (id);


--
-- Name: projet_contact projet_contact_unique; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_contact
    ADD CONSTRAINT projet_contact_unique UNIQUE (projet_id, personne_id, role_code);


--
-- Name: projet_geometries projet_geometries_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_geometries
    ADD CONSTRAINT projet_geometries_pkey PRIMARY KEY (id);


--
-- Name: projet_metadata projet_metadata_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_metadata
    ADD CONSTRAINT projet_metadata_pkey PRIMARY KEY (id);


--
-- Name: projet_metadata projet_metadata_projet_id_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_metadata
    ADD CONSTRAINT projet_metadata_projet_id_key UNIQUE (projet_id);


--
-- Name: projet_prestataire projet_prestataire_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_prestataire
    ADD CONSTRAINT projet_prestataire_pkey PRIMARY KEY (projet_id, prestataire_id);


--
-- Name: projets projets_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projets
    ADD CONSTRAINT projets_pkey PRIMARY KEY (id);


--
-- Name: rapport_bilan rapport_bilan_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_bilan
    ADD CONSTRAINT rapport_bilan_pkey PRIMARY KEY (id);


--
-- Name: rapport_bilan rapport_bilan_unique; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_bilan
    ADD CONSTRAINT rapport_bilan_unique UNIQUE (projet_id, annee, version);


--
-- Name: rapport_suivi rapport_suivi_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_suivi
    ADD CONSTRAINT rapport_suivi_pkey PRIMARY KEY (id);


--
-- Name: rapport_suivi rapport_suivi_unique; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_suivi
    ADD CONSTRAINT rapport_suivi_unique UNIQUE (projet_id, annee, version);


--
-- Name: ref_geomce_categorie ref_geomce_categorie_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ref_geomce_categorie
    ADD CONSTRAINT ref_geomce_categorie_pkey PRIMARY KEY (code);


--
-- Name: role_contact_ref role_contact_ref_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.role_contact_ref
    ADD CONSTRAINT role_contact_ref_pkey PRIMARY KEY (code);


--
-- Name: satellite_captures satellite_captures_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.satellite_captures
    ADD CONSTRAINT satellite_captures_pkey PRIMARY KEY (id);


--
-- Name: satellite_captures satellite_captures_projet_ug_date_uidx; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.satellite_captures
    ADD CONSTRAINT satellite_captures_projet_ug_date_uidx UNIQUE (projet_id, ug_id, acquisition_date);


--
-- Name: site_agrement site_agrement_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.site_agrement
    ADD CONSTRAINT site_agrement_pkey PRIMARY KEY (id);


--
-- Name: site_agrement site_agrement_projet_id_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.site_agrement
    ADD CONSTRAINT site_agrement_projet_id_key UNIQUE (projet_id);


--
-- Name: transfert_projet transfert_projet_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.transfert_projet
    ADD CONSTRAINT transfert_projet_pkey PRIMARY KEY (id);


--
-- Name: ug_lin_parcelles ug_lin_parcelles_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_lin_parcelles
    ADD CONSTRAINT ug_lin_parcelles_pkey PRIMARY KEY (id);


--
-- Name: ug_lin_parcelles ug_lin_parcelles_ug_id_parcelle_id_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_lin_parcelles
    ADD CONSTRAINT ug_lin_parcelles_ug_id_parcelle_id_key UNIQUE (ug_id, parcelle_id);


--
-- Name: ug_pct_parcelles ug_pct_parcelles_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_pct_parcelles
    ADD CONSTRAINT ug_pct_parcelles_pkey PRIMARY KEY (id);


--
-- Name: ug_pct_parcelles ug_pct_parcelles_ug_id_parcelle_id_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_pct_parcelles
    ADD CONSTRAINT ug_pct_parcelles_ug_id_parcelle_id_key UNIQUE (ug_id, parcelle_id);


--
-- Name: ug_surf_parcelles ug_surf_parcelles_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_surf_parcelles
    ADD CONSTRAINT ug_surf_parcelles_pkey PRIMARY KEY (id);


--
-- Name: ug_surf_parcelles ug_surf_parcelles_ug_id_parcelle_id_key; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_surf_parcelles
    ADD CONSTRAINT ug_surf_parcelles_ug_id_parcelle_id_key UNIQUE (ug_id, parcelle_id);


--
-- Name: unites_de_gestion_lin unites_de_gestion_lin_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.unites_de_gestion_lin
    ADD CONSTRAINT unites_de_gestion_lin_pkey PRIMARY KEY (id);


--
-- Name: unites_de_gestion_pct unites_de_gestion_pct_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.unites_de_gestion_pct
    ADD CONSTRAINT unites_de_gestion_pct_pkey PRIMARY KEY (id);


--
-- Name: unites_de_gestion_surf unites_de_gestion_surf_pkey; Type: CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.unites_de_gestion_surf
    ADD CONSTRAINT unites_de_gestion_surf_pkey PRIMARY KEY (id);


--
-- Name: action_etape_fait_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX action_etape_fait_idx ON bancarisation.action_etape USING btree (projet_id, fait_le DESC) WHERE fait;


--
-- Name: action_etape_occ_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX action_etape_occ_idx ON bancarisation.action_etape USING btree (occurrence_id, ordre);


--
-- Name: action_etape_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX action_etape_projet_idx ON bancarisation.action_etape USING btree (projet_id);


--
-- Name: action_fiche_arrete_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX action_fiche_arrete_projet_id_idx ON bancarisation.action_fiche_arrete USING btree (projet_id);


--
-- Name: action_fiche_ug_ids_gin; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX action_fiche_ug_ids_gin ON bancarisation.action_fiche USING gin (ug_ids);


--
-- Name: annotation_terrain_geom_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX annotation_terrain_geom_idx ON bancarisation.annotation_terrain USING gist (geom);


--
-- Name: annotation_terrain_observed_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX annotation_terrain_observed_idx ON bancarisation.annotation_terrain USING btree (projet_id, observed_at DESC);


--
-- Name: annotation_terrain_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX annotation_terrain_projet_idx ON bancarisation.annotation_terrain USING btree (projet_id);


--
-- Name: annotation_terrain_source_key_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX annotation_terrain_source_key_idx ON bancarisation.annotation_terrain USING btree (projet_id, source_geom_key);


--
-- Name: annotation_terrain_ug_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX annotation_terrain_ug_idx ON bancarisation.annotation_terrain USING btree (projet_id, ug_id);


--
-- Name: arrete_prescription_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX arrete_prescription_projet_id_idx ON bancarisation.arrete_prescription USING btree (projet_id);


--
-- Name: budget_baseline_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX budget_baseline_projet_idx ON bancarisation.budget_baseline USING btree (projet_id, figee_le DESC);


--
-- Name: budget_import_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX budget_import_projet_idx ON bancarisation.budget_import USING btree (projet_id, created_at DESC);


--
-- Name: budget_mouvement_occurrence_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX budget_mouvement_occurrence_idx ON bancarisation.budget_mouvement USING btree (occurrence_id, modifie_le DESC);


--
-- Name: budget_mouvement_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX budget_mouvement_projet_idx ON bancarisation.budget_mouvement USING btree (projet_id, modifie_le DESC);


--
-- Name: cadastre_parcelle_geom_3857_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX cadastre_parcelle_geom_3857_idx ON bancarisation.cadastre_parcelle USING gist (geom_3857);


--
-- Name: cadastre_parcelle_idu_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX cadastre_parcelle_idu_idx ON bancarisation.cadastre_parcelle USING btree (projet_id, idu);


--
-- Name: cadastre_parcelle_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX cadastre_parcelle_projet_idx ON bancarisation.cadastre_parcelle USING btree (projet_id);


--
-- Name: cadastre_parcelle_ug_idu_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX cadastre_parcelle_ug_idu_idx ON bancarisation.cadastre_parcelle_ug USING btree (projet_id, idu);


--
-- Name: cadastre_parcelle_ug_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX cadastre_parcelle_ug_idx ON bancarisation.cadastre_parcelle USING btree (projet_id, ug_id);


--
-- Name: cadastre_parcelle_ug_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX cadastre_parcelle_ug_projet_idx ON bancarisation.cadastre_parcelle_ug USING btree (projet_id);


--
-- Name: cadastre_parcelle_ug_ug_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX cadastre_parcelle_ug_ug_idx ON bancarisation.cadastre_parcelle_ug USING btree (projet_id, ug_id);


--
-- Name: checklist_modele_etape_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX checklist_modele_etape_idx ON bancarisation.checklist_modele_etape USING btree (modele_id, ordre);


--
-- Name: checklist_modele_etape_organisation_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX checklist_modele_etape_organisation_id_idx ON bancarisation.checklist_modele_etape USING btree (organisation_id);


--
-- Name: constat_controle_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX constat_controle_projet_id_idx ON bancarisation.constat_controle USING btree (projet_id);


--
-- Name: constat_gain_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX constat_gain_projet_id_idx ON bancarisation.constat_gain USING btree (projet_id);


--
-- Name: couche_non_georeferencee_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX couche_non_georeferencee_projet_idx ON bancarisation.couche_non_georeferencee USING btree (projet_id, depot_id);


--
-- Name: credit_lot_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX credit_lot_projet_id_idx ON bancarisation.credit_lot USING btree (projet_id);


--
-- Name: credit_palier_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX credit_palier_projet_id_idx ON bancarisation.credit_palier USING btree (projet_id);


--
-- Name: credit_vente_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX credit_vente_projet_id_idx ON bancarisation.credit_vente USING btree (projet_id);


--
-- Name: demande_message_demande_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX demande_message_demande_idx ON bancarisation.demande_message USING btree (demande_id, cree_le);


--
-- Name: demande_message_email_uniq; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX demande_message_email_uniq ON bancarisation.demande_message USING btree (email_message_id) WHERE (email_message_id IS NOT NULL);


--
-- Name: demande_message_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX demande_message_projet_id_idx ON bancarisation.demande_message USING btree (projet_id);


--
-- Name: demande_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX demande_projet_idx ON bancarisation.demande USING btree (projet_id, cree_le DESC);


--
-- Name: demande_statut_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX demande_statut_idx ON bancarisation.demande USING btree (statut) WHERE (statut <> 'close'::text);


--
-- Name: documents_categorie_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX documents_categorie_idx ON bancarisation.documents USING btree (projet_id, categorie);


--
-- Name: documents_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX documents_projet_idx ON bancarisation.documents USING btree (projet_id);


--
-- Name: documents_projet_occurrence_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX documents_projet_occurrence_idx ON bancarisation.documents USING btree (projet_id, occurrence_id);


--
-- Name: droit_documents_document_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX droit_documents_document_id_idx ON bancarisation.droit_documents USING btree (document_id);


--
-- Name: droit_documents_droit_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX droit_documents_droit_id_idx ON bancarisation.droit_documents USING btree (droit_id);


--
-- Name: droit_documents_organisation_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX droit_documents_organisation_id_idx ON bancarisation.droit_documents USING btree (organisation_id);


--
-- Name: droits_fonciers_date_fin_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX droits_fonciers_date_fin_idx ON bancarisation.droits_fonciers USING btree (date_fin);


--
-- Name: droits_fonciers_organisation_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX droits_fonciers_organisation_id_idx ON bancarisation.droits_fonciers USING btree (organisation_id);


--
-- Name: droits_fonciers_parcelle_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX droits_fonciers_parcelle_id_idx ON bancarisation.droits_fonciers USING btree (parcelle_id);


--
-- Name: droits_fonciers_titulaire_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX droits_fonciers_titulaire_id_idx ON bancarisation.droits_fonciers USING btree (titulaire_id);


--
-- Name: echeance_ug_ids_gin; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX echeance_ug_ids_gin ON bancarisation.echeance USING gin (ug_ids);


--
-- Name: emprise_projet_geom_3857_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX emprise_projet_geom_3857_idx ON bancarisation.emprise_projet USING gist (geom_3857);


--
-- Name: emprise_projet_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX emprise_projet_projet_idx ON bancarisation.emprise_projet USING btree (projet_id);


--
-- Name: export_projet_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX export_projet_projet_idx ON bancarisation.export_projet USING btree (projet_id, created_at DESC);


--
-- Name: export_projet_un_seul_actif; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX export_projet_un_seul_actif ON bancarisation.export_projet USING btree (projet_id) WHERE (statut = ANY (ARRAY['en_attente'::text, 'en_cours'::text]));


--
-- Name: geometrie_mouvement_entite_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX geometrie_mouvement_entite_idx ON bancarisation.geometrie_mouvement USING btree (table_source, entite_id, created_at DESC);


--
-- Name: geometrie_mouvement_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX geometrie_mouvement_projet_idx ON bancarisation.geometrie_mouvement USING btree (projet_id, created_at DESC);


--
-- Name: idx_acte_projet; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_acte_projet ON bancarisation.acte_dreal USING btree (projet_id);


--
-- Name: idx_acte_statut; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_acte_statut ON bancarisation.acte_dreal USING btree (statut);


--
-- Name: idx_arrete_projet; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_arrete_projet ON bancarisation.arrete USING btree (projet_id);


--
-- Name: idx_bilan_projet; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_bilan_projet ON bancarisation.bilan_suivi USING btree (projet_id);


--
-- Name: idx_closure_descendant; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_closure_descendant ON bancarisation.organisation_closure USING btree (descendant_id);


--
-- Name: idx_constat_action; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_constat_action ON bancarisation.constat_controle USING btree (action_fiche_id);


--
-- Name: idx_constat_gain_lot; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_constat_gain_lot ON bancarisation.constat_gain USING btree (lot_id);


--
-- Name: idx_constat_occ; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_constat_occ ON bancarisation.constat_controle USING btree (occurrence_id);


--
-- Name: idx_couv_echeance; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_couv_echeance ON bancarisation.prescription_couverture USING btree (echeance_id);


--
-- Name: idx_credit_lot_agrement; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_credit_lot_agrement ON bancarisation.credit_lot USING btree (site_agrement_id);


--
-- Name: idx_credit_palier_lot; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_credit_palier_lot ON bancarisation.credit_palier USING btree (lot_id);


--
-- Name: idx_credit_vente_lot; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_credit_vente_lot ON bancarisation.credit_vente USING btree (lot_id);


--
-- Name: idx_export_geomce_projet; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_export_geomce_projet ON bancarisation.export_geomce USING btree (projet_id, cree_le DESC);


--
-- Name: idx_geomce_communes_geom_2154; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_geomce_communes_geom_2154 ON bancarisation.geomce_communes USING gist (geom_2154);


--
-- Name: idx_geomce_communes_geom_3857; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_geomce_communes_geom_3857 ON bancarisation.geomce_communes USING gist (geom_3857);


--
-- Name: idx_geomce_lin_geom_2154; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_geomce_lin_geom_2154 ON bancarisation.geomce_lin USING gist (geom_2154);


--
-- Name: idx_geomce_lin_geom_3857; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_geomce_lin_geom_3857 ON bancarisation.geomce_lin USING gist (geom_3857);


--
-- Name: idx_geomce_pct_geom_2154; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_geomce_pct_geom_2154 ON bancarisation.geomce_pct USING gist (geom_2154);


--
-- Name: idx_geomce_pct_geom_3857; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_geomce_pct_geom_3857 ON bancarisation.geomce_pct USING gist (geom_3857);


--
-- Name: idx_geomce_surf_geom_2154; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_geomce_surf_geom_2154 ON bancarisation.geomce_surf USING gist (geom_2154);


--
-- Name: idx_geomce_surf_geom_3857; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_geomce_surf_geom_3857 ON bancarisation.geomce_surf USING gist (geom_3857);


--
-- Name: idx_membre_utilisateur; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_membre_utilisateur ON bancarisation.membre_organisation USING btree (utilisateur_id);


--
-- Name: idx_organisations_parent; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_organisations_parent ON bancarisation.organisations USING btree (parent_id);


--
-- Name: idx_pce_attributs; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_pce_attributs ON bancarisation.plan_cao_entite USING gin (attributs);


--
-- Name: idx_pce_bloc; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_pce_bloc ON bancarisation.plan_cao_entite USING btree (plan_id, bloc) WHERE (bloc IS NOT NULL);


--
-- Name: idx_pce_geom; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_pce_geom ON bancarisation.plan_cao_entite USING gist (geom) WHERE (geom IS NOT NULL);


--
-- Name: idx_pce_geom_3857; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_pce_geom_3857 ON bancarisation.plan_cao_entite USING gist (geom_3857) WHERE (geom_3857 IS NOT NULL);


--
-- Name: idx_pce_geom_local; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_pce_geom_local ON bancarisation.plan_cao_entite USING gist (geom_local);


--
-- Name: idx_pce_plan_calque; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_pce_plan_calque ON bancarisation.plan_cao_entite USING btree (plan_id, calque);


--
-- Name: idx_plan_cao_projet; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_plan_cao_projet ON bancarisation.plan_cao USING btree (projet_id, cree_le DESC);


--
-- Name: idx_plan_cao_projet_fichier; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX idx_plan_cao_projet_fichier ON bancarisation.plan_cao USING btree (projet_id, nom_fichier);


--
-- Name: idx_presc_action; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_presc_action ON bancarisation.arrete_prescription USING btree (action_fiche_id);


--
-- Name: idx_presc_arrete; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_presc_arrete ON bancarisation.arrete_prescription USING btree (arrete_id);


--
-- Name: idx_presc_arrete_code; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_presc_arrete_code ON bancarisation.arrete_prescription USING btree (arrete_id, code);


--
-- Name: idx_projet_acces_org; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX idx_projet_acces_org ON bancarisation.projet_acces USING btree (organisation_id);


--
-- Name: invitation_email_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX invitation_email_idx ON bancarisation.invitation USING btree (lower(email));


--
-- Name: invitation_organisation_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX invitation_organisation_idx ON bancarisation.invitation USING btree (organisation_id);


--
-- Name: journal_actions_action_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX journal_actions_action_idx ON bancarisation.journal_actions USING btree (action, cree_le DESC);


--
-- Name: journal_actions_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX journal_actions_projet_idx ON bancarisation.journal_actions USING btree (projet_id, cree_le DESC);


--
-- Name: journal_audit_org_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX journal_audit_org_idx ON bancarisation.journal_audit USING btree (organisation_id, horodatage DESC);


--
-- Name: journal_audit_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX journal_audit_projet_idx ON bancarisation.journal_audit USING btree (projet_id, horodatage DESC);


--
-- Name: ligne_budget_est_total_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ligne_budget_est_total_idx ON bancarisation.ligne_budget USING btree (projet_id, est_total);


--
-- Name: ligne_budget_import_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ligne_budget_import_idx ON bancarisation.ligne_budget USING btree (import_id);


--
-- Name: ligne_budget_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ligne_budget_projet_idx ON bancarisation.ligne_budget USING btree (projet_id);


--
-- Name: ligne_budget_ug_ids_gin; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ligne_budget_ug_ids_gin ON bancarisation.ligne_budget USING gin (ug_ids);


--
-- Name: membre_organisation_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX membre_organisation_idx ON bancarisation.membre USING btree (organisation_id);


--
-- Name: membre_organisation_utilisateur_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX membre_organisation_utilisateur_idx ON bancarisation.membre_organisation USING btree (utilisateur_id) WHERE (statut = 'actif'::text);


--
-- Name: note_interne_mentions_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX note_interne_mentions_idx ON bancarisation.note_interne USING gin (mentions);


--
-- Name: note_interne_occ_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX note_interne_occ_idx ON bancarisation.note_interne USING btree (occurrence_id, created_at) WHERE (occurrence_id IS NOT NULL);


--
-- Name: note_interne_organisation_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX note_interne_organisation_idx ON bancarisation.note_interne USING btree (organisation_id, projet_id);


--
-- Name: note_interne_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX note_interne_projet_idx ON bancarisation.note_interne USING btree (projet_id, created_at DESC);


--
-- Name: occurrence_finance_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_finance_projet_idx ON bancarisation.occurrence_finance USING btree (projet_id);


--
-- Name: occurrence_ia_echeance_annee_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX occurrence_ia_echeance_annee_idx ON bancarisation.occurrence USING btree (echeance_id, annee) WHERE ((origine = 'ia'::text) AND (echeance_id IS NOT NULL));


--
-- Name: occurrence_ligne_budget_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_ligne_budget_idx ON bancarisation.occurrence USING btree (ligne_budget_id);


--
-- Name: occurrence_prestataire_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_prestataire_idx ON bancarisation.occurrence USING btree (prestataire_id) WHERE (prestataire_id IS NOT NULL);


--
-- Name: occurrence_responsable_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_responsable_idx ON bancarisation.occurrence USING btree (responsable_id);


--
-- Name: occurrence_responsable_occ_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_responsable_occ_idx ON bancarisation.occurrence_responsable USING btree (occurrence_id, ordre);


--
-- Name: occurrence_responsable_personne_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_responsable_personne_idx ON bancarisation.occurrence_responsable USING btree (personne_id);


--
-- Name: occurrence_responsable_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_responsable_projet_idx ON bancarisation.occurrence_responsable USING btree (projet_id);


--
-- Name: occurrence_ug_ids_gin; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_ug_ids_gin ON bancarisation.occurrence USING gin (ug_ids);


--
-- Name: occurrence_ug_ids_gin_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX occurrence_ug_ids_gin_idx ON bancarisation.occurrence USING gin (ug_ids);


--
-- Name: parcelles_geom_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX parcelles_geom_idx ON bancarisation.parcelles USING gist (geom);


--
-- Name: parcelles_idu_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX parcelles_idu_idx ON bancarisation.parcelles USING btree (idu);


--
-- Name: parcelles_organisation_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX parcelles_organisation_id_idx ON bancarisation.parcelles USING btree (organisation_id);


--
-- Name: parcelles_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX parcelles_projet_id_idx ON bancarisation.parcelles USING btree (projet_id);


--
-- Name: personnes_archive_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX personnes_archive_idx ON bancarisation.personnes USING btree (archive);


--
-- Name: personnes_lower_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX personnes_lower_idx ON bancarisation.personnes USING btree (lower(nom));


--
-- Name: personnes_org_utilisateur_uidx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX personnes_org_utilisateur_uidx ON bancarisation.personnes USING btree (organisation_id, utilisateur_id) WHERE (utilisateur_id IS NOT NULL);


--
-- Name: personnes_organisation_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX personnes_organisation_id_idx ON bancarisation.personnes USING btree (organisation_id);


--
-- Name: personnes_prestataire_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX personnes_prestataire_id_idx ON bancarisation.personnes USING btree (prestataire_id);


--
-- Name: personnes_structure_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX personnes_structure_id_idx ON bancarisation.personnes USING btree (structure_id);


--
-- Name: plan_cao_calque_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX plan_cao_calque_projet_id_idx ON bancarisation.plan_cao_calque USING btree (projet_id);


--
-- Name: plan_cao_entite_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX plan_cao_entite_projet_id_idx ON bancarisation.plan_cao_entite USING btree (projet_id);


--
-- Name: plan_cao_groupe_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX plan_cao_groupe_projet_id_idx ON bancarisation.plan_cao_groupe USING btree (projet_id);


--
-- Name: prescription_couverture_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX prescription_couverture_projet_id_idx ON bancarisation.prescription_couverture USING btree (projet_id);


--
-- Name: prestataires_categories_gin; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX prestataires_categories_gin ON bancarisation.prestataires USING gin (categories_mesure);


--
-- Name: prestataires_departement_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX prestataires_departement_idx ON bancarisation.prestataires USING btree (departement) WHERE actif;


--
-- Name: prestataires_nom_lower_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX prestataires_nom_lower_idx ON bancarisation.prestataires USING btree (lower(nom) text_pattern_ops);


--
-- Name: prestataires_nom_unique_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX prestataires_nom_unique_idx ON bancarisation.prestataires USING btree (lower(TRIM(BOTH FROM nom)));


--
-- Name: prestataires_siret_norm_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX prestataires_siret_norm_idx ON bancarisation.prestataires USING btree (siret_norm) WHERE (siret_norm IS NOT NULL);


--
-- Name: prestataires_siret_norm_uidx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX prestataires_siret_norm_uidx ON bancarisation.prestataires USING btree (siret_norm) WHERE ((siret_norm IS NOT NULL) AND (length(siret_norm) >= 9));


--
-- Name: prestataires_specialites_gin; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX prestataires_specialites_gin ON bancarisation.prestataires USING gin (specialites);


--
-- Name: projet_acces_org_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX projet_acces_org_idx ON bancarisation.projet_acces USING btree (organisation_id);


--
-- Name: projet_acces_org_uniq; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX projet_acces_org_uniq ON bancarisation.projet_acces USING btree (projet_id, organisation_id, COALESCE(qualite, ''::text)) WHERE (organisation_id IS NOT NULL);


--
-- Name: projet_acces_user_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX projet_acces_user_idx ON bancarisation.projet_acces USING btree (utilisateur_id);


--
-- Name: projet_acces_user_uniq; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX projet_acces_user_uniq ON bancarisation.projet_acces USING btree (projet_id, utilisateur_id) WHERE (utilisateur_id IS NOT NULL);


--
-- Name: projet_contact_personne_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX projet_contact_personne_idx ON bancarisation.projet_contact USING btree (personne_id);


--
-- Name: projet_contact_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX projet_contact_projet_idx ON bancarisation.projet_contact USING btree (projet_id);


--
-- Name: projet_geometries_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX projet_geometries_projet_idx ON bancarisation.projet_geometries USING btree (projet_id);


--
-- Name: projet_prestataire_presta_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX projet_prestataire_presta_idx ON bancarisation.projet_prestataire USING btree (prestataire_id);


--
-- Name: projets_credits_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX projets_credits_idx ON bancarisation.projets USING btree (organisation_id) WHERE (type_dispositif = 'site_credits'::bancarisation.type_dispositif);


--
-- Name: projets_organisation_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX projets_organisation_idx ON bancarisation.projets USING btree (organisation_id);


--
-- Name: rapport_bilan_projet_annee_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX rapport_bilan_projet_annee_idx ON bancarisation.rapport_bilan USING btree (projet_id, annee, version DESC);


--
-- Name: rapport_suivi_bilan_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX rapport_suivi_bilan_idx ON bancarisation.rapport_suivi USING btree (bilan_suivi_id) WHERE (bilan_suivi_id IS NOT NULL);


--
-- Name: rapport_suivi_projet_annee_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX rapport_suivi_projet_annee_idx ON bancarisation.rapport_suivi USING btree (projet_id, annee, version DESC);


--
-- Name: satellite_captures_date_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX satellite_captures_date_idx ON bancarisation.satellite_captures USING btree (acquisition_date);


--
-- Name: satellite_captures_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX satellite_captures_projet_idx ON bancarisation.satellite_captures USING btree (projet_id);


--
-- Name: satellite_captures_projet_ug_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX satellite_captures_projet_ug_idx ON bancarisation.satellite_captures USING btree (projet_id, ug_id);


--
-- Name: transfert_projet_cible_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX transfert_projet_cible_idx ON bancarisation.transfert_projet USING btree (org_cible, statut);


--
-- Name: transfert_projet_source_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX transfert_projet_source_idx ON bancarisation.transfert_projet USING btree (org_source, statut);


--
-- Name: transfert_projet_un_en_attente; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX transfert_projet_un_en_attente ON bancarisation.transfert_projet USING btree (projet_id) WHERE (statut = 'en_attente'::text);


--
-- Name: ug_lin_parcelles_parcelle_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_lin_parcelles_parcelle_id_idx ON bancarisation.ug_lin_parcelles USING btree (parcelle_id);


--
-- Name: ug_lin_parcelles_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_lin_parcelles_projet_id_idx ON bancarisation.ug_lin_parcelles USING btree (projet_id);


--
-- Name: ug_lin_parcelles_ug_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_lin_parcelles_ug_id_idx ON bancarisation.ug_lin_parcelles USING btree (ug_id);


--
-- Name: ug_pct_parcelles_parcelle_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_pct_parcelles_parcelle_id_idx ON bancarisation.ug_pct_parcelles USING btree (parcelle_id);


--
-- Name: ug_pct_parcelles_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_pct_parcelles_projet_id_idx ON bancarisation.ug_pct_parcelles USING btree (projet_id);


--
-- Name: ug_pct_parcelles_ug_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_pct_parcelles_ug_id_idx ON bancarisation.ug_pct_parcelles USING btree (ug_id);


--
-- Name: ug_surf_parcelles_parcelle_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_surf_parcelles_parcelle_id_idx ON bancarisation.ug_surf_parcelles USING btree (parcelle_id);


--
-- Name: ug_surf_parcelles_projet_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_surf_parcelles_projet_id_idx ON bancarisation.ug_surf_parcelles USING btree (projet_id);


--
-- Name: ug_surf_parcelles_ug_id_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX ug_surf_parcelles_ug_id_idx ON bancarisation.ug_surf_parcelles USING btree (ug_id);


--
-- Name: unites_de_gestion_lin_depot_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_lin_depot_idx ON bancarisation.unites_de_gestion_lin USING btree (projet_id, depot_id);


--
-- Name: unites_de_gestion_lin_geom_3857_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_lin_geom_3857_idx ON bancarisation.unites_de_gestion_lin USING gist (geom_3857);


--
-- Name: unites_de_gestion_lin_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_lin_projet_idx ON bancarisation.unites_de_gestion_lin USING btree (projet_id);


--
-- Name: unites_de_gestion_lin_statut_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_lin_statut_idx ON bancarisation.unites_de_gestion_lin USING btree (projet_id, statut);


--
-- Name: unites_de_gestion_lin_ug_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_lin_ug_idx ON bancarisation.unites_de_gestion_lin USING btree (projet_id, ug_id);


--
-- Name: unites_de_gestion_pct_depot_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_pct_depot_idx ON bancarisation.unites_de_gestion_pct USING btree (projet_id, depot_id);


--
-- Name: unites_de_gestion_pct_geom_3857_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_pct_geom_3857_idx ON bancarisation.unites_de_gestion_pct USING gist (geom_3857);


--
-- Name: unites_de_gestion_pct_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_pct_projet_idx ON bancarisation.unites_de_gestion_pct USING btree (projet_id);


--
-- Name: unites_de_gestion_pct_statut_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_pct_statut_idx ON bancarisation.unites_de_gestion_pct USING btree (projet_id, statut);


--
-- Name: unites_de_gestion_pct_ug_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_pct_ug_idx ON bancarisation.unites_de_gestion_pct USING btree (projet_id, ug_id);


--
-- Name: unites_de_gestion_surf_depot_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_surf_depot_idx ON bancarisation.unites_de_gestion_surf USING btree (projet_id, depot_id);


--
-- Name: unites_de_gestion_surf_geom_3857_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_surf_geom_3857_idx ON bancarisation.unites_de_gestion_surf USING gist (geom_3857);


--
-- Name: unites_de_gestion_surf_projet_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_surf_projet_idx ON bancarisation.unites_de_gestion_surf USING btree (projet_id);


--
-- Name: unites_de_gestion_surf_statut_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_surf_statut_idx ON bancarisation.unites_de_gestion_surf USING btree (projet_id, statut);


--
-- Name: unites_de_gestion_surf_ug_idx; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE INDEX unites_de_gestion_surf_ug_idx ON bancarisation.unites_de_gestion_surf USING btree (projet_id, ug_id);


--
-- Name: uq_organisations_code; Type: INDEX; Schema: bancarisation; Owner: postgres
--

CREATE UNIQUE INDEX uq_organisations_code ON bancarisation.organisations USING btree (code) WHERE (code IS NOT NULL);


--
-- Name: v_credit_stock _RETURN; Type: RULE; Schema: bancarisation; Owner: postgres
--

CREATE OR REPLACE VIEW bancarisation.v_credit_stock WITH (security_invoker='true') AS
 SELECT l.id AS lot_id,
    l.site_agrement_id,
    sa.projet_id,
    l.libelle,
    l.composante_milieu,
    l.unite,
    l.date_emission,
    l.quantite_emise,
    COALESCE(sum(v.quantite) FILTER (WHERE (v.statut = 'reserve'::bancarisation.statut_vente)), (0)::numeric) AS qte_reservee,
    COALESCE(sum(v.quantite) FILTER (WHERE (v.statut = 'vendu'::bancarisation.statut_vente)), (0)::numeric) AS qte_vendue,
    COALESCE(sum(v.quantite) FILTER (WHERE (v.statut = 'affecte'::bancarisation.statut_vente)), (0)::numeric) AS qte_affectee,
    (l.quantite_emise - COALESCE(sum(v.quantite) FILTER (WHERE (v.statut <> 'annule'::bancarisation.statut_vente)), (0)::numeric)) AS qte_disponible
   FROM ((bancarisation.credit_lot l
     JOIN bancarisation.site_agrement sa ON ((sa.id = l.site_agrement_id)))
     LEFT JOIN bancarisation.credit_vente v ON ((v.lot_id = l.id)))
  GROUP BY l.id, sa.projet_id;


--
-- Name: action_etape action_etape_before; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER action_etape_before BEFORE INSERT OR UPDATE ON bancarisation.action_etape FOR EACH ROW EXECUTE FUNCTION bancarisation.tg_action_etape_before();


--
-- Name: credit_vente credit_vente_affecte_terminal; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER credit_vente_affecte_terminal BEFORE UPDATE OF statut ON bancarisation.credit_vente FOR EACH ROW EXECUTE FUNCTION bancarisation.trg_credit_vente_affecte_terminal();


--
-- Name: credit_vente credit_vente_stock; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER credit_vente_stock BEFORE INSERT OR UPDATE OF quantite, statut, lot_id ON bancarisation.credit_vente FOR EACH ROW EXECUTE FUNCTION bancarisation.trg_credit_vente_stock();


--
-- Name: action_fiche_arrete deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.action_fiche_arrete FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'action_fiche', 'action_fiche_id', 'arrete', 'arrete_id');


--
-- Name: arrete_prescription deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.arrete_prescription FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'arrete', 'arrete_id', 'action_fiche', 'action_fiche_id');


--
-- Name: checklist_modele_etape deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.checklist_modele_etape FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('organisation_id', 'checklist_modele', 'modele_id');


--
-- Name: constat_controle deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.constat_controle FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'action_fiche', 'action_fiche_id', 'occurrence', 'occurrence_id');


--
-- Name: constat_gain deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.constat_gain FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'credit_lot', 'lot_id', 'constat_controle', 'constat_controle_id');


--
-- Name: credit_lot deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.credit_lot FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'site_agrement', 'site_agrement_id');


--
-- Name: credit_palier deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.credit_palier FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'credit_lot', 'lot_id');


--
-- Name: credit_vente deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.credit_vente FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'credit_lot', 'lot_id');


--
-- Name: demande_message deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.demande_message FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'demande', 'demande_id');


--
-- Name: droit_documents deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.droit_documents FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('organisation_id', 'droits_fonciers', 'droit_id');


--
-- Name: plan_cao_calque deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.plan_cao_calque FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'plan_cao', 'plan_id');


--
-- Name: plan_cao_entite deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.plan_cao_entite FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'plan_cao', 'plan_id');


--
-- Name: plan_cao_groupe deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.plan_cao_groupe FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'plan_cao', 'plan_id');


--
-- Name: prescription_couverture deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.prescription_couverture FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'arrete_prescription', 'prescription_id', 'echeance', 'echeance_id');


--
-- Name: ug_lin_parcelles deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.ug_lin_parcelles FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'unites_de_gestion_lin', 'ug_id');


--
-- Name: ug_pct_parcelles deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.ug_pct_parcelles FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'unites_de_gestion_pct', 'ug_id');


--
-- Name: ug_surf_parcelles deriver_rattachement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER deriver_rattachement BEFORE INSERT OR UPDATE ON bancarisation.ug_surf_parcelles FOR EACH ROW EXECUTE FUNCTION prive.deriver_rattachement('projet_id', 'unites_de_gestion_surf', 'ug_id');


--
-- Name: journal_audit journal_audit_immuable; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER journal_audit_immuable BEFORE DELETE OR UPDATE ON bancarisation.journal_audit FOR EACH ROW EXECUTE FUNCTION prive.interdire_modification();


--
-- Name: journal_audit journal_audit_pas_de_truncate; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER journal_audit_pas_de_truncate BEFORE TRUNCATE ON bancarisation.journal_audit FOR EACH STATEMENT EXECUTE FUNCTION prive.interdire_modification();


--
-- Name: membre_organisation journaliser; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER journaliser AFTER INSERT OR DELETE OR UPDATE ON bancarisation.membre_organisation FOR EACH ROW EXECUTE FUNCTION prive.journaliser();


--
-- Name: projet_acces journaliser; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER journaliser AFTER INSERT OR DELETE OR UPDATE ON bancarisation.projet_acces FOR EACH ROW EXECUTE FUNCTION prive.journaliser();


--
-- Name: note_interne note_interne_before; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER note_interne_before BEFORE INSERT OR UPDATE ON bancarisation.note_interne FOR EACH ROW EXECUTE FUNCTION bancarisation.tg_note_interne_before();


--
-- Name: occurrence_finance occurrence_finance_projet; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER occurrence_finance_projet BEFORE INSERT OR UPDATE ON bancarisation.occurrence_finance FOR EACH ROW EXECUTE FUNCTION prive.occurrence_finance_projet();


--
-- Name: occurrence_responsable occurrence_responsable_before; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER occurrence_responsable_before BEFORE INSERT OR UPDATE ON bancarisation.occurrence_responsable FOR EACH ROW EXECUTE FUNCTION bancarisation.tg_occurrence_responsable_before();


--
-- Name: organisations organisations_closure_sync; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER organisations_closure_sync AFTER INSERT OR DELETE OR UPDATE OF parent_id ON bancarisation.organisations FOR EACH STATEMENT EXECUTE FUNCTION bancarisation.trg_organisation_closure();


--
-- Name: projets projets_createur_gestionnaire; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER projets_createur_gestionnaire AFTER INSERT ON bancarisation.projets FOR EACH ROW EXECUTE FUNCTION prive.createur_gestionnaire();


--
-- Name: projets projets_figer_organisation; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER projets_figer_organisation BEFORE UPDATE ON bancarisation.projets FOR EACH ROW EXECUTE FUNCTION prive.figer_organisation_projet();


--
-- Name: projets projets_renseigner_createur; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER projets_renseigner_createur BEFORE INSERT ON bancarisation.projets FOR EACH ROW EXECUTE FUNCTION prive.renseigner_createur();


--
-- Name: occurrence trg_budget_mouvement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER trg_budget_mouvement AFTER UPDATE ON bancarisation.occurrence FOR EACH ROW EXECUTE FUNCTION bancarisation.log_budget_mouvement();


--
-- Name: demande_message trg_maj_statut_demande; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER trg_maj_statut_demande AFTER INSERT ON bancarisation.demande_message FOR EACH ROW EXECUTE FUNCTION bancarisation.maj_statut_demande();


--
-- Name: occurrence_finance trg_occurrence_finance_mouvement; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER trg_occurrence_finance_mouvement AFTER UPDATE ON bancarisation.occurrence_finance FOR EACH ROW EXECUTE FUNCTION bancarisation.log_occurrence_finance();


--
-- Name: rapport_bilan trg_proteger_rapport_valide; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER trg_proteger_rapport_valide BEFORE UPDATE ON bancarisation.rapport_bilan FOR EACH ROW EXECUTE FUNCTION bancarisation.proteger_rapport_valide();


--
-- Name: note_interne zz_note_interne_organisation; Type: TRIGGER; Schema: bancarisation; Owner: postgres
--

CREATE TRIGGER zz_note_interne_organisation BEFORE INSERT OR UPDATE ON bancarisation.note_interne FOR EACH ROW EXECUTE FUNCTION prive.note_interne_organisation();


--
-- Name: acte_dreal acte_dreal_demande_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.acte_dreal
    ADD CONSTRAINT acte_dreal_demande_id_fkey FOREIGN KEY (demande_id) REFERENCES bancarisation.demande(id);


--
-- Name: acte_dreal acte_dreal_document_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.acte_dreal
    ADD CONSTRAINT acte_dreal_document_id_fkey FOREIGN KEY (document_id) REFERENCES bancarisation.documents(id);


--
-- Name: acte_dreal acte_dreal_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.acte_dreal
    ADD CONSTRAINT acte_dreal_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: action_etape action_etape_assigne_a_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_etape
    ADD CONSTRAINT action_etape_assigne_a_fkey FOREIGN KEY (assigne_a) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;


--
-- Name: action_etape action_etape_fait_par_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_etape
    ADD CONSTRAINT action_etape_fait_par_fkey FOREIGN KEY (fait_par) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;


--
-- Name: action_etape action_etape_occurrence_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_etape
    ADD CONSTRAINT action_etape_occurrence_id_fkey FOREIGN KEY (occurrence_id) REFERENCES bancarisation.occurrence(id);


--
-- Name: action_etape action_etape_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_etape
    ADD CONSTRAINT action_etape_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: action_fiche_arrete action_fiche_arrete_action_fiche_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_fiche_arrete
    ADD CONSTRAINT action_fiche_arrete_action_fiche_id_fkey FOREIGN KEY (action_fiche_id) REFERENCES bancarisation.action_fiche(id) ON DELETE CASCADE;


--
-- Name: action_fiche_arrete action_fiche_arrete_arrete_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_fiche_arrete
    ADD CONSTRAINT action_fiche_arrete_arrete_id_fkey FOREIGN KEY (arrete_id) REFERENCES bancarisation.arrete(id) ON DELETE CASCADE;


--
-- Name: action_fiche action_fiche_import_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_fiche
    ADD CONSTRAINT action_fiche_import_id_fkey FOREIGN KEY (import_id) REFERENCES bancarisation.extraction_import(id) ON DELETE SET NULL;


--
-- Name: action_fiche action_fiche_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.action_fiche
    ADD CONSTRAINT action_fiche_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: annotation_terrain annotation_terrain_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.annotation_terrain
    ADD CONSTRAINT annotation_terrain_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: annotation_terrain annotation_terrain_source_annotation_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.annotation_terrain
    ADD CONSTRAINT annotation_terrain_source_annotation_id_fkey FOREIGN KEY (source_annotation_id) REFERENCES bancarisation.annotation_terrain(id) ON DELETE SET NULL;


--
-- Name: arrete arrete_document_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.arrete
    ADD CONSTRAINT arrete_document_id_fkey FOREIGN KEY (document_id) REFERENCES bancarisation.documents(id);


--
-- Name: arrete_prescription arrete_prescription_action_fiche_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.arrete_prescription
    ADD CONSTRAINT arrete_prescription_action_fiche_id_fkey FOREIGN KEY (action_fiche_id) REFERENCES bancarisation.action_fiche(id) ON DELETE SET NULL;


--
-- Name: arrete_prescription arrete_prescription_arrete_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.arrete_prescription
    ADD CONSTRAINT arrete_prescription_arrete_id_fkey FOREIGN KEY (arrete_id) REFERENCES bancarisation.arrete(id) ON DELETE CASCADE;


--
-- Name: arrete arrete_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.arrete
    ADD CONSTRAINT arrete_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: bilan_suivi bilan_suivi_document_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.bilan_suivi
    ADD CONSTRAINT bilan_suivi_document_id_fkey FOREIGN KEY (document_id) REFERENCES bancarisation.documents(id);


--
-- Name: bilan_suivi bilan_suivi_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.bilan_suivi
    ADD CONSTRAINT bilan_suivi_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: bilan_suivi bilan_suivi_rapport_suivi_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.bilan_suivi
    ADD CONSTRAINT bilan_suivi_rapport_suivi_id_fkey FOREIGN KEY (rapport_suivi_id) REFERENCES bancarisation.rapport_suivi(id) ON DELETE SET NULL;


--
-- Name: budget_baseline budget_baseline_projet_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.budget_baseline
    ADD CONSTRAINT budget_baseline_projet_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: budget_import budget_import_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.budget_import
    ADD CONSTRAINT budget_import_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: budget_mouvement budget_mouvement_occurrence_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.budget_mouvement
    ADD CONSTRAINT budget_mouvement_occurrence_fkey FOREIGN KEY (occurrence_id) REFERENCES bancarisation.occurrence(id) ON DELETE CASCADE;


--
-- Name: budget_mouvement budget_mouvement_projet_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.budget_mouvement
    ADD CONSTRAINT budget_mouvement_projet_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: cadastre_parcelle cadastre_parcelle_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.cadastre_parcelle
    ADD CONSTRAINT cadastre_parcelle_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: cadastre_parcelle_ug cadastre_parcelle_ug_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.cadastre_parcelle_ug
    ADD CONSTRAINT cadastre_parcelle_ug_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: checklist_modele_etape checklist_modele_etape_modele_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.checklist_modele_etape
    ADD CONSTRAINT checklist_modele_etape_modele_id_fkey FOREIGN KEY (modele_id) REFERENCES bancarisation.checklist_modele(id) ON DELETE CASCADE;


--
-- Name: checklist_modele checklist_modele_organisation_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.checklist_modele
    ADD CONSTRAINT checklist_modele_organisation_id_fkey FOREIGN KEY (organisation_id) REFERENCES bancarisation.organisations(id) ON DELETE SET NULL;


--
-- Name: constat_controle constat_controle_action_fiche_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.constat_controle
    ADD CONSTRAINT constat_controle_action_fiche_id_fkey FOREIGN KEY (action_fiche_id) REFERENCES bancarisation.action_fiche(id) ON DELETE CASCADE;


--
-- Name: constat_controle constat_controle_occurrence_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.constat_controle
    ADD CONSTRAINT constat_controle_occurrence_id_fkey FOREIGN KEY (occurrence_id) REFERENCES bancarisation.occurrence(id) ON DELETE CASCADE;


--
-- Name: constat_gain constat_gain_constat_controle_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.constat_gain
    ADD CONSTRAINT constat_gain_constat_controle_id_fkey FOREIGN KEY (constat_controle_id) REFERENCES bancarisation.constat_controle(id) ON DELETE SET NULL;


--
-- Name: constat_gain constat_gain_lot_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.constat_gain
    ADD CONSTRAINT constat_gain_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES bancarisation.credit_lot(id) ON DELETE CASCADE;


--
-- Name: constat_gain constat_gain_rapport_doc_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.constat_gain
    ADD CONSTRAINT constat_gain_rapport_doc_id_fkey FOREIGN KEY (rapport_doc_id) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: couche_non_georeferencee couche_non_georeferencee_document_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.couche_non_georeferencee
    ADD CONSTRAINT couche_non_georeferencee_document_id_fkey FOREIGN KEY (document_id) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: couche_non_georeferencee couche_non_georeferencee_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.couche_non_georeferencee
    ADD CONSTRAINT couche_non_georeferencee_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: credit_lot credit_lot_site_agrement_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.credit_lot
    ADD CONSTRAINT credit_lot_site_agrement_id_fkey FOREIGN KEY (site_agrement_id) REFERENCES bancarisation.site_agrement(id) ON DELETE CASCADE;


--
-- Name: credit_palier credit_palier_lot_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.credit_palier
    ADD CONSTRAINT credit_palier_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES bancarisation.credit_lot(id) ON DELETE CASCADE;


--
-- Name: credit_vente credit_vente_certificat_doc_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.credit_vente
    ADD CONSTRAINT credit_vente_certificat_doc_id_fkey FOREIGN KEY (certificat_doc_id) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: credit_vente credit_vente_lot_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.credit_vente
    ADD CONSTRAINT credit_vente_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES bancarisation.credit_lot(id) ON DELETE RESTRICT;


--
-- Name: demande_message demande_message_demande_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.demande_message
    ADD CONSTRAINT demande_message_demande_fkey FOREIGN KEY (demande_id) REFERENCES bancarisation.demande(id) ON DELETE CASCADE;


--
-- Name: demande demande_occurrence_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.demande
    ADD CONSTRAINT demande_occurrence_fkey FOREIGN KEY (occurrence_id) REFERENCES bancarisation.occurrence(id) ON DELETE SET NULL;


--
-- Name: demande demande_projet_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.demande
    ADD CONSTRAINT demande_projet_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: documents documents_occurrence_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.documents
    ADD CONSTRAINT documents_occurrence_id_fkey FOREIGN KEY (occurrence_id) REFERENCES bancarisation.occurrence(id) ON DELETE SET NULL;


--
-- Name: documents documents_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.documents
    ADD CONSTRAINT documents_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: droit_documents droit_documents_droit_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.droit_documents
    ADD CONSTRAINT droit_documents_droit_id_fkey FOREIGN KEY (droit_id) REFERENCES bancarisation.droits_fonciers(id) ON DELETE CASCADE;


--
-- Name: droits_fonciers droits_fonciers_constituant_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.droits_fonciers
    ADD CONSTRAINT droits_fonciers_constituant_id_fkey FOREIGN KEY (constituant_id) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;


--
-- Name: droits_fonciers droits_fonciers_parcelle_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.droits_fonciers
    ADD CONSTRAINT droits_fonciers_parcelle_id_fkey FOREIGN KEY (parcelle_id) REFERENCES bancarisation.parcelles(id) ON DELETE CASCADE;


--
-- Name: droits_fonciers droits_fonciers_titulaire_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.droits_fonciers
    ADD CONSTRAINT droits_fonciers_titulaire_id_fkey FOREIGN KEY (titulaire_id) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;


--
-- Name: echeance echeance_import_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.echeance
    ADD CONSTRAINT echeance_import_id_fkey FOREIGN KEY (import_id) REFERENCES bancarisation.extraction_import(id) ON DELETE SET NULL;


--
-- Name: echeance echeance_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.echeance
    ADD CONSTRAINT echeance_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: emprise_projet emprise_projet_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.emprise_projet
    ADD CONSTRAINT emprise_projet_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: export_geomce export_geomce_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.export_geomce
    ADD CONSTRAINT export_geomce_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: export_projet export_projet_demande_par_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.export_projet
    ADD CONSTRAINT export_projet_demande_par_fkey FOREIGN KEY (demande_par) REFERENCES auth.users(id);


--
-- Name: export_projet export_projet_organisation_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.export_projet
    ADD CONSTRAINT export_projet_organisation_id_fkey FOREIGN KEY (organisation_id) REFERENCES bancarisation.organisations(id);


--
-- Name: export_projet export_projet_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.export_projet
    ADD CONSTRAINT export_projet_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: extraction_import extraction_import_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.extraction_import
    ADD CONSTRAINT extraction_import_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: geometrie_mouvement geometrie_mouvement_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.geometrie_mouvement
    ADD CONSTRAINT geometrie_mouvement_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: invitation invitation_invite_par_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.invitation
    ADD CONSTRAINT invitation_invite_par_fkey FOREIGN KEY (invite_par) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: invitation invitation_organisation_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.invitation
    ADD CONSTRAINT invitation_organisation_id_fkey FOREIGN KEY (organisation_id) REFERENCES bancarisation.organisations(id) ON DELETE CASCADE;


--
-- Name: invitation invitation_utilisateur_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.invitation
    ADD CONSTRAINT invitation_utilisateur_id_fkey FOREIGN KEY (utilisateur_id) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: journal_actions journal_actions_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.journal_actions
    ADD CONSTRAINT journal_actions_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE SET NULL;


--
-- Name: ligne_budget ligne_budget_import_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ligne_budget
    ADD CONSTRAINT ligne_budget_import_id_fkey FOREIGN KEY (import_id) REFERENCES bancarisation.budget_import(id) ON DELETE SET NULL;


--
-- Name: ligne_budget ligne_budget_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ligne_budget
    ADD CONSTRAINT ligne_budget_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: membre membre_organisation_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.membre
    ADD CONSTRAINT membre_organisation_fkey FOREIGN KEY (organisation_id) REFERENCES bancarisation.organisations(id) ON DELETE CASCADE;


--
-- Name: membre_organisation membre_organisation_invite_par_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.membre_organisation
    ADD CONSTRAINT membre_organisation_invite_par_fkey FOREIGN KEY (invite_par) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: membre_organisation membre_organisation_organisation_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.membre_organisation
    ADD CONSTRAINT membre_organisation_organisation_id_fkey FOREIGN KEY (organisation_id) REFERENCES bancarisation.organisations(id) ON DELETE CASCADE;


--
-- Name: membre_organisation membre_organisation_utilisateur_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.membre_organisation
    ADD CONSTRAINT membre_organisation_utilisateur_id_fkey FOREIGN KEY (utilisateur_id) REFERENCES auth.users(id) ON DELETE CASCADE NOT VALID;


--
-- Name: note_interne note_interne_auteur_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.note_interne
    ADD CONSTRAINT note_interne_auteur_id_fkey FOREIGN KEY (auteur_id) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;


--
-- Name: note_interne note_interne_document_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.note_interne
    ADD CONSTRAINT note_interne_document_id_fkey FOREIGN KEY (document_id) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: note_interne note_interne_etape_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.note_interne
    ADD CONSTRAINT note_interne_etape_id_fkey FOREIGN KEY (etape_id) REFERENCES bancarisation.action_etape(id) ON DELETE SET NULL;


--
-- Name: note_interne note_interne_occurrence_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.note_interne
    ADD CONSTRAINT note_interne_occurrence_id_fkey FOREIGN KEY (occurrence_id) REFERENCES bancarisation.occurrence(id);


--
-- Name: note_interne note_interne_organisation_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.note_interne
    ADD CONSTRAINT note_interne_organisation_id_fkey FOREIGN KEY (organisation_id) REFERENCES bancarisation.organisations(id) ON DELETE CASCADE;


--
-- Name: note_interne note_interne_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.note_interne
    ADD CONSTRAINT note_interne_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: occurrence occurrence_echeance_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence
    ADD CONSTRAINT occurrence_echeance_id_fkey FOREIGN KEY (echeance_id) REFERENCES bancarisation.echeance(id) ON DELETE SET NULL;


--
-- Name: occurrence_finance occurrence_finance_occurrence_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence_finance
    ADD CONSTRAINT occurrence_finance_occurrence_id_fkey FOREIGN KEY (occurrence_id) REFERENCES bancarisation.occurrence(id) ON DELETE CASCADE;


--
-- Name: occurrence_finance occurrence_finance_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence_finance
    ADD CONSTRAINT occurrence_finance_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: occurrence occurrence_ligne_budget_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence
    ADD CONSTRAINT occurrence_ligne_budget_id_fkey FOREIGN KEY (ligne_budget_id) REFERENCES bancarisation.ligne_budget(id) ON DELETE SET NULL;


--
-- Name: occurrence occurrence_prestataire_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence
    ADD CONSTRAINT occurrence_prestataire_id_fkey FOREIGN KEY (prestataire_id) REFERENCES bancarisation.prestataires(id) ON DELETE SET NULL;


--
-- Name: occurrence occurrence_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence
    ADD CONSTRAINT occurrence_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: occurrence occurrence_responsable_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence
    ADD CONSTRAINT occurrence_responsable_fkey FOREIGN KEY (responsable_id) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;


--
-- Name: occurrence_responsable occurrence_responsable_occurrence_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence_responsable
    ADD CONSTRAINT occurrence_responsable_occurrence_id_fkey FOREIGN KEY (occurrence_id) REFERENCES bancarisation.occurrence(id) ON DELETE CASCADE;


--
-- Name: occurrence_responsable occurrence_responsable_personne_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence_responsable
    ADD CONSTRAINT occurrence_responsable_personne_id_fkey FOREIGN KEY (personne_id) REFERENCES bancarisation.personnes(id) ON DELETE CASCADE;


--
-- Name: occurrence_responsable occurrence_responsable_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.occurrence_responsable
    ADD CONSTRAINT occurrence_responsable_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: organisation_closure organisation_closure_ancetre_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.organisation_closure
    ADD CONSTRAINT organisation_closure_ancetre_id_fkey FOREIGN KEY (ancetre_id) REFERENCES bancarisation.organisations(id) ON DELETE CASCADE;


--
-- Name: organisation_closure organisation_closure_descendant_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.organisation_closure
    ADD CONSTRAINT organisation_closure_descendant_id_fkey FOREIGN KEY (descendant_id) REFERENCES bancarisation.organisations(id) ON DELETE CASCADE;


--
-- Name: organisations organisations_parent_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.organisations
    ADD CONSTRAINT organisations_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES bancarisation.organisations(id) ON DELETE RESTRICT;


--
-- Name: personnes personnes_prestataire_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.personnes
    ADD CONSTRAINT personnes_prestataire_fkey FOREIGN KEY (prestataire_id) REFERENCES bancarisation.prestataires(id) ON DELETE SET NULL;


--
-- Name: personnes personnes_structure_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.personnes
    ADD CONSTRAINT personnes_structure_fkey FOREIGN KEY (structure_id) REFERENCES bancarisation.personnes(id) ON DELETE SET NULL;


--
-- Name: personnes personnes_utilisateur_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.personnes
    ADD CONSTRAINT personnes_utilisateur_fkey FOREIGN KEY (utilisateur_id) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: plan_cao_calque plan_cao_calque_groupe_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_calque
    ADD CONSTRAINT plan_cao_calque_groupe_fkey FOREIGN KEY (plan_id, groupe_id) REFERENCES bancarisation.plan_cao_groupe(plan_id, id) ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: plan_cao_calque plan_cao_calque_plan_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_calque
    ADD CONSTRAINT plan_cao_calque_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES bancarisation.plan_cao(id) ON DELETE CASCADE;


--
-- Name: plan_cao plan_cao_document_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao
    ADD CONSTRAINT plan_cao_document_id_fkey FOREIGN KEY (document_id) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: plan_cao_entite plan_cao_entite_plan_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_entite
    ADD CONSTRAINT plan_cao_entite_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES bancarisation.plan_cao(id) ON DELETE CASCADE;


--
-- Name: plan_cao_groupe plan_cao_groupe_plan_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao_groupe
    ADD CONSTRAINT plan_cao_groupe_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES bancarisation.plan_cao(id) ON DELETE CASCADE;


--
-- Name: plan_cao plan_cao_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.plan_cao
    ADD CONSTRAINT plan_cao_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: prescription_couverture prescription_couverture_credit_vente_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.prescription_couverture
    ADD CONSTRAINT prescription_couverture_credit_vente_id_fkey FOREIGN KEY (credit_vente_id) REFERENCES bancarisation.credit_vente(id) ON DELETE SET NULL;


--
-- Name: prescription_couverture prescription_couverture_echeance_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.prescription_couverture
    ADD CONSTRAINT prescription_couverture_echeance_id_fkey FOREIGN KEY (echeance_id) REFERENCES bancarisation.echeance(id) ON DELETE CASCADE;


--
-- Name: prescription_couverture prescription_couverture_prescription_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.prescription_couverture
    ADD CONSTRAINT prescription_couverture_prescription_id_fkey FOREIGN KEY (prescription_id) REFERENCES bancarisation.arrete_prescription(id) ON DELETE CASCADE;


--
-- Name: profils profils_utilisateur_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.profils
    ADD CONSTRAINT profils_utilisateur_id_fkey FOREIGN KEY (utilisateur_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: projet_acces projet_acces_accorde_par_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_acces
    ADD CONSTRAINT projet_acces_accorde_par_fkey FOREIGN KEY (accorde_par) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: projet_acces projet_acces_organisation_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_acces
    ADD CONSTRAINT projet_acces_organisation_id_fkey FOREIGN KEY (organisation_id) REFERENCES bancarisation.organisations(id) ON DELETE CASCADE;


--
-- Name: projet_acces projet_acces_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_acces
    ADD CONSTRAINT projet_acces_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: projet_acces projet_acces_utilisateur_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_acces
    ADD CONSTRAINT projet_acces_utilisateur_id_fkey FOREIGN KEY (utilisateur_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: projet_contact projet_contact_personne_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_contact
    ADD CONSTRAINT projet_contact_personne_id_fkey FOREIGN KEY (personne_id) REFERENCES bancarisation.personnes(id) ON DELETE CASCADE;


--
-- Name: projet_contact projet_contact_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_contact
    ADD CONSTRAINT projet_contact_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: projet_contact projet_contact_role_code_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_contact
    ADD CONSTRAINT projet_contact_role_code_fkey FOREIGN KEY (role_code) REFERENCES bancarisation.role_contact_ref(code);


--
-- Name: projet_geometries projet_geometries_document_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_geometries
    ADD CONSTRAINT projet_geometries_document_id_fkey FOREIGN KEY (document_id) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: projet_geometries projet_geometries_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_geometries
    ADD CONSTRAINT projet_geometries_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: projet_metadata projet_metadata_import_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_metadata
    ADD CONSTRAINT projet_metadata_import_id_fkey FOREIGN KEY (import_id) REFERENCES bancarisation.extraction_import(id) ON DELETE SET NULL;


--
-- Name: projet_metadata projet_metadata_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_metadata
    ADD CONSTRAINT projet_metadata_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: projet_prestataire projet_prestataire_prestataire_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_prestataire
    ADD CONSTRAINT projet_prestataire_prestataire_id_fkey FOREIGN KEY (prestataire_id) REFERENCES bancarisation.prestataires(id) ON DELETE CASCADE;


--
-- Name: projet_prestataire projet_prestataire_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projet_prestataire
    ADD CONSTRAINT projet_prestataire_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: projets projets_cree_par_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projets
    ADD CONSTRAINT projets_cree_par_fkey FOREIGN KEY (cree_par) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: projets projets_organisation_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.projets
    ADD CONSTRAINT projets_organisation_id_fkey FOREIGN KEY (organisation_id) REFERENCES bancarisation.organisations(id);


--
-- Name: rapport_bilan rapport_bilan_baseline_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_bilan
    ADD CONSTRAINT rapport_bilan_baseline_fkey FOREIGN KEY (baseline_id) REFERENCES bancarisation.budget_baseline(id) ON DELETE SET NULL;


--
-- Name: rapport_bilan rapport_bilan_projet_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_bilan
    ADD CONSTRAINT rapport_bilan_projet_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: rapport_suivi rapport_suivi_bilan_suivi_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_suivi
    ADD CONSTRAINT rapport_suivi_bilan_suivi_id_fkey FOREIGN KEY (bilan_suivi_id) REFERENCES bancarisation.bilan_suivi(id) ON DELETE SET NULL;


--
-- Name: rapport_suivi rapport_suivi_document_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_suivi
    ADD CONSTRAINT rapport_suivi_document_id_fkey FOREIGN KEY (document_id) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: rapport_suivi rapport_suivi_projet_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.rapport_suivi
    ADD CONSTRAINT rapport_suivi_projet_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: ref_geomce_categorie ref_geomce_categorie_parent_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ref_geomce_categorie
    ADD CONSTRAINT ref_geomce_categorie_parent_fkey FOREIGN KEY (parent) REFERENCES bancarisation.ref_geomce_categorie(code);


--
-- Name: satellite_captures satellite_captures_document_id_png_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.satellite_captures
    ADD CONSTRAINT satellite_captures_document_id_png_fkey FOREIGN KEY (document_id_png) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: satellite_captures satellite_captures_document_id_tif_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.satellite_captures
    ADD CONSTRAINT satellite_captures_document_id_tif_fkey FOREIGN KEY (document_id_tif) REFERENCES bancarisation.documents(id) ON DELETE SET NULL;


--
-- Name: satellite_captures satellite_captures_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.satellite_captures
    ADD CONSTRAINT satellite_captures_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: site_agrement site_agrement_arrete_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.site_agrement
    ADD CONSTRAINT site_agrement_arrete_id_fkey FOREIGN KEY (arrete_id) REFERENCES bancarisation.arrete(id);


--
-- Name: site_agrement site_agrement_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.site_agrement
    ADD CONSTRAINT site_agrement_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: transfert_projet transfert_projet_demande_par_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.transfert_projet
    ADD CONSTRAINT transfert_projet_demande_par_fkey FOREIGN KEY (demande_par) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: transfert_projet transfert_projet_export_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.transfert_projet
    ADD CONSTRAINT transfert_projet_export_id_fkey FOREIGN KEY (export_id) REFERENCES bancarisation.export_projet(id);


--
-- Name: transfert_projet transfert_projet_org_cible_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.transfert_projet
    ADD CONSTRAINT transfert_projet_org_cible_fkey FOREIGN KEY (org_cible) REFERENCES bancarisation.organisations(id);


--
-- Name: transfert_projet transfert_projet_org_source_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.transfert_projet
    ADD CONSTRAINT transfert_projet_org_source_fkey FOREIGN KEY (org_source) REFERENCES bancarisation.organisations(id);


--
-- Name: transfert_projet transfert_projet_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.transfert_projet
    ADD CONSTRAINT transfert_projet_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: transfert_projet transfert_projet_traite_par_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.transfert_projet
    ADD CONSTRAINT transfert_projet_traite_par_fkey FOREIGN KEY (traite_par) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: ug_lin_parcelles ug_lin_parcelles_parcelle_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_lin_parcelles
    ADD CONSTRAINT ug_lin_parcelles_parcelle_id_fkey FOREIGN KEY (parcelle_id) REFERENCES bancarisation.parcelles(id) ON DELETE CASCADE;


--
-- Name: ug_lin_parcelles ug_lin_parcelles_ug_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_lin_parcelles
    ADD CONSTRAINT ug_lin_parcelles_ug_id_fkey FOREIGN KEY (ug_id) REFERENCES bancarisation.unites_de_gestion_lin(id) ON DELETE CASCADE;


--
-- Name: ug_pct_parcelles ug_pct_parcelles_parcelle_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_pct_parcelles
    ADD CONSTRAINT ug_pct_parcelles_parcelle_id_fkey FOREIGN KEY (parcelle_id) REFERENCES bancarisation.parcelles(id) ON DELETE CASCADE;


--
-- Name: ug_pct_parcelles ug_pct_parcelles_ug_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_pct_parcelles
    ADD CONSTRAINT ug_pct_parcelles_ug_id_fkey FOREIGN KEY (ug_id) REFERENCES bancarisation.unites_de_gestion_pct(id) ON DELETE CASCADE;


--
-- Name: ug_surf_parcelles ug_surf_parcelles_parcelle_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_surf_parcelles
    ADD CONSTRAINT ug_surf_parcelles_parcelle_id_fkey FOREIGN KEY (parcelle_id) REFERENCES bancarisation.parcelles(id) ON DELETE CASCADE;


--
-- Name: ug_surf_parcelles ug_surf_parcelles_ug_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.ug_surf_parcelles
    ADD CONSTRAINT ug_surf_parcelles_ug_id_fkey FOREIGN KEY (ug_id) REFERENCES bancarisation.unites_de_gestion_surf(id) ON DELETE CASCADE;


--
-- Name: unites_de_gestion_lin unites_de_gestion_lin_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.unites_de_gestion_lin
    ADD CONSTRAINT unites_de_gestion_lin_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: unites_de_gestion_pct unites_de_gestion_pct_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.unites_de_gestion_pct
    ADD CONSTRAINT unites_de_gestion_pct_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: unites_de_gestion_surf unites_de_gestion_surf_projet_id_fkey; Type: FK CONSTRAINT; Schema: bancarisation; Owner: postgres
--

ALTER TABLE ONLY bancarisation.unites_de_gestion_surf
    ADD CONSTRAINT unites_de_gestion_surf_projet_id_fkey FOREIGN KEY (projet_id) REFERENCES bancarisation.projets(id) ON DELETE CASCADE;


--
-- Name: acte_dreal; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.acte_dreal ENABLE ROW LEVEL SECURITY;

--
-- Name: acte_dreal acte_dreal_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY acte_dreal_delete ON bancarisation.acte_dreal FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: acte_dreal acte_dreal_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY acte_dreal_insert ON bancarisation.acte_dreal FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: acte_dreal acte_dreal_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY acte_dreal_select ON bancarisation.acte_dreal FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: acte_dreal acte_dreal_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY acte_dreal_update ON bancarisation.acte_dreal FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_etape; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.action_etape ENABLE ROW LEVEL SECURITY;

--
-- Name: action_etape action_etape_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_etape_delete ON bancarisation.action_etape FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_etape action_etape_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_etape_insert ON bancarisation.action_etape FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_etape action_etape_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_etape_select ON bancarisation.action_etape FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: action_etape action_etape_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_etape_update ON bancarisation.action_etape FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_fiche; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.action_fiche ENABLE ROW LEVEL SECURITY;

--
-- Name: action_fiche_arrete; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.action_fiche_arrete ENABLE ROW LEVEL SECURITY;

--
-- Name: action_fiche_arrete action_fiche_arrete_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_fiche_arrete_delete ON bancarisation.action_fiche_arrete FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_fiche_arrete action_fiche_arrete_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_fiche_arrete_insert ON bancarisation.action_fiche_arrete FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_fiche_arrete action_fiche_arrete_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_fiche_arrete_select ON bancarisation.action_fiche_arrete FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: action_fiche_arrete action_fiche_arrete_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_fiche_arrete_update ON bancarisation.action_fiche_arrete FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_fiche action_fiche_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_fiche_delete ON bancarisation.action_fiche FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_fiche action_fiche_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_fiche_insert ON bancarisation.action_fiche FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: action_fiche action_fiche_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_fiche_select ON bancarisation.action_fiche FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: action_fiche action_fiche_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY action_fiche_update ON bancarisation.action_fiche FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: annotation_terrain; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.annotation_terrain ENABLE ROW LEVEL SECURITY;

--
-- Name: annotation_terrain annotation_terrain_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY annotation_terrain_delete ON bancarisation.annotation_terrain FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: annotation_terrain annotation_terrain_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY annotation_terrain_insert ON bancarisation.annotation_terrain FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: annotation_terrain annotation_terrain_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY annotation_terrain_select ON bancarisation.annotation_terrain FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: annotation_terrain annotation_terrain_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY annotation_terrain_update ON bancarisation.annotation_terrain FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: arrete; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.arrete ENABLE ROW LEVEL SECURITY;

--
-- Name: arrete arrete_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY arrete_delete ON bancarisation.arrete FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: arrete arrete_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY arrete_insert ON bancarisation.arrete FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: arrete_prescription; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.arrete_prescription ENABLE ROW LEVEL SECURITY;

--
-- Name: arrete_prescription arrete_prescription_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY arrete_prescription_delete ON bancarisation.arrete_prescription FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: arrete_prescription arrete_prescription_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY arrete_prescription_insert ON bancarisation.arrete_prescription FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: arrete_prescription arrete_prescription_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY arrete_prescription_select ON bancarisation.arrete_prescription FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: arrete_prescription arrete_prescription_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY arrete_prescription_update ON bancarisation.arrete_prescription FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: arrete arrete_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY arrete_select ON bancarisation.arrete FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: arrete arrete_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY arrete_update ON bancarisation.arrete FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: bilan_suivi; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.bilan_suivi ENABLE ROW LEVEL SECURITY;

--
-- Name: bilan_suivi bilan_suivi_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY bilan_suivi_delete ON bancarisation.bilan_suivi FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: bilan_suivi bilan_suivi_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY bilan_suivi_insert ON bancarisation.bilan_suivi FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: bilan_suivi bilan_suivi_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY bilan_suivi_select ON bancarisation.bilan_suivi FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: bilan_suivi bilan_suivi_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY bilan_suivi_update ON bancarisation.bilan_suivi FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: budget_baseline; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.budget_baseline ENABLE ROW LEVEL SECURITY;

--
-- Name: budget_baseline budget_baseline_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_baseline_delete ON bancarisation.budget_baseline FOR DELETE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: budget_baseline budget_baseline_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_baseline_insert ON bancarisation.budget_baseline FOR INSERT TO authenticated WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: budget_baseline budget_baseline_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_baseline_select ON bancarisation.budget_baseline FOR SELECT TO authenticated USING (prive.voit_finances(projet_id));


--
-- Name: budget_baseline budget_baseline_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_baseline_update ON bancarisation.budget_baseline FOR UPDATE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: budget_import; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.budget_import ENABLE ROW LEVEL SECURITY;

--
-- Name: budget_import budget_import_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_import_delete ON bancarisation.budget_import FOR DELETE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: budget_import budget_import_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_import_insert ON bancarisation.budget_import FOR INSERT TO authenticated WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: budget_import budget_import_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_import_select ON bancarisation.budget_import FOR SELECT TO authenticated USING (prive.voit_finances(projet_id));


--
-- Name: budget_import budget_import_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_import_update ON bancarisation.budget_import FOR UPDATE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: budget_mouvement; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.budget_mouvement ENABLE ROW LEVEL SECURITY;

--
-- Name: budget_mouvement budget_mouvement_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_mouvement_delete ON bancarisation.budget_mouvement FOR DELETE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: budget_mouvement budget_mouvement_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_mouvement_insert ON bancarisation.budget_mouvement FOR INSERT TO authenticated WITH CHECK (((prive.niveau_projet(projet_id) >= 3) AND ((champ = ANY (ARRAY['statut'::text, 'annee'::text, 'mois_debut'::text, 'mois_fin'::text])) OR prive.voit_finances(projet_id))));


--
-- Name: budget_mouvement budget_mouvement_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_mouvement_select ON bancarisation.budget_mouvement FOR SELECT TO authenticated USING (
CASE
    WHEN (champ = ANY (ARRAY['statut'::text, 'annee'::text, 'mois_debut'::text, 'mois_fin'::text])) THEN (prive.niveau_projet(projet_id) >= 2)
    ELSE prive.voit_finances(projet_id)
END);


--
-- Name: budget_mouvement budget_mouvement_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY budget_mouvement_update ON bancarisation.budget_mouvement FOR UPDATE TO authenticated USING (((prive.niveau_projet(projet_id) >= 3) AND ((champ = ANY (ARRAY['statut'::text, 'annee'::text, 'mois_debut'::text, 'mois_fin'::text])) OR prive.voit_finances(projet_id)))) WITH CHECK (((prive.niveau_projet(projet_id) >= 3) AND ((champ = ANY (ARRAY['statut'::text, 'annee'::text, 'mois_debut'::text, 'mois_fin'::text])) OR prive.voit_finances(projet_id))));


--
-- Name: cadastre_parcelle; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.cadastre_parcelle ENABLE ROW LEVEL SECURITY;

--
-- Name: cadastre_parcelle cadastre_parcelle_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY cadastre_parcelle_delete ON bancarisation.cadastre_parcelle FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: cadastre_parcelle cadastre_parcelle_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY cadastre_parcelle_insert ON bancarisation.cadastre_parcelle FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: cadastre_parcelle cadastre_parcelle_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY cadastre_parcelle_select ON bancarisation.cadastre_parcelle FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: cadastre_parcelle_ug; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.cadastre_parcelle_ug ENABLE ROW LEVEL SECURITY;

--
-- Name: cadastre_parcelle_ug cadastre_parcelle_ug_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY cadastre_parcelle_ug_delete ON bancarisation.cadastre_parcelle_ug FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: cadastre_parcelle_ug cadastre_parcelle_ug_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY cadastre_parcelle_ug_insert ON bancarisation.cadastre_parcelle_ug FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: cadastre_parcelle_ug cadastre_parcelle_ug_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY cadastre_parcelle_ug_select ON bancarisation.cadastre_parcelle_ug FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: cadastre_parcelle_ug cadastre_parcelle_ug_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY cadastre_parcelle_ug_update ON bancarisation.cadastre_parcelle_ug FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: cadastre_parcelle cadastre_parcelle_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY cadastre_parcelle_update ON bancarisation.cadastre_parcelle FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: checklist_modele; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.checklist_modele ENABLE ROW LEVEL SECURITY;

--
-- Name: checklist_modele checklist_modele_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY checklist_modele_delete ON bancarisation.checklist_modele FOR DELETE TO authenticated USING ((prive.role_org(organisation_id) = 'admin'::text));


--
-- Name: checklist_modele_etape; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.checklist_modele_etape ENABLE ROW LEVEL SECURITY;

--
-- Name: checklist_modele_etape checklist_modele_etape_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY checklist_modele_etape_delete ON bancarisation.checklist_modele_etape FOR DELETE TO authenticated USING ((prive.role_org(organisation_id) = 'admin'::text));


--
-- Name: checklist_modele_etape checklist_modele_etape_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY checklist_modele_etape_insert ON bancarisation.checklist_modele_etape FOR INSERT TO authenticated WITH CHECK ((prive.role_org(organisation_id) = 'admin'::text));


--
-- Name: checklist_modele_etape checklist_modele_etape_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY checklist_modele_etape_select ON bancarisation.checklist_modele_etape FOR SELECT TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: checklist_modele_etape checklist_modele_etape_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY checklist_modele_etape_update ON bancarisation.checklist_modele_etape FOR UPDATE TO authenticated USING ((prive.role_org(organisation_id) = 'admin'::text)) WITH CHECK ((prive.role_org(organisation_id) = 'admin'::text));


--
-- Name: checklist_modele checklist_modele_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY checklist_modele_insert ON bancarisation.checklist_modele FOR INSERT TO authenticated WITH CHECK ((prive.role_org(organisation_id) = 'admin'::text));


--
-- Name: checklist_modele checklist_modele_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY checklist_modele_select ON bancarisation.checklist_modele FOR SELECT TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: checklist_modele checklist_modele_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY checklist_modele_update ON bancarisation.checklist_modele FOR UPDATE TO authenticated USING ((prive.role_org(organisation_id) = 'admin'::text)) WITH CHECK ((prive.role_org(organisation_id) = 'admin'::text));


--
-- Name: constat_controle; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.constat_controle ENABLE ROW LEVEL SECURITY;

--
-- Name: constat_controle constat_controle_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY constat_controle_delete ON bancarisation.constat_controle FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: constat_controle constat_controle_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY constat_controle_insert ON bancarisation.constat_controle FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: constat_controle constat_controle_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY constat_controle_select ON bancarisation.constat_controle FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: constat_controle constat_controle_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY constat_controle_update ON bancarisation.constat_controle FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: constat_gain; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.constat_gain ENABLE ROW LEVEL SECURITY;

--
-- Name: constat_gain constat_gain_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY constat_gain_delete ON bancarisation.constat_gain FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: constat_gain constat_gain_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY constat_gain_insert ON bancarisation.constat_gain FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: constat_gain constat_gain_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY constat_gain_select ON bancarisation.constat_gain FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: constat_gain constat_gain_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY constat_gain_update ON bancarisation.constat_gain FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: couche_non_georeferencee; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.couche_non_georeferencee ENABLE ROW LEVEL SECURITY;

--
-- Name: couche_non_georeferencee couche_non_georeferencee_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY couche_non_georeferencee_delete ON bancarisation.couche_non_georeferencee FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: couche_non_georeferencee couche_non_georeferencee_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY couche_non_georeferencee_insert ON bancarisation.couche_non_georeferencee FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: couche_non_georeferencee couche_non_georeferencee_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY couche_non_georeferencee_select ON bancarisation.couche_non_georeferencee FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: couche_non_georeferencee couche_non_georeferencee_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY couche_non_georeferencee_update ON bancarisation.couche_non_georeferencee FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: credit_lot; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.credit_lot ENABLE ROW LEVEL SECURITY;

--
-- Name: credit_lot credit_lot_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_lot_delete ON bancarisation.credit_lot FOR DELETE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: credit_lot credit_lot_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_lot_insert ON bancarisation.credit_lot FOR INSERT TO authenticated WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: credit_lot credit_lot_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_lot_select ON bancarisation.credit_lot FOR SELECT TO authenticated USING (prive.voit_finances(projet_id));


--
-- Name: credit_lot credit_lot_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_lot_update ON bancarisation.credit_lot FOR UPDATE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: credit_palier; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.credit_palier ENABLE ROW LEVEL SECURITY;

--
-- Name: credit_palier credit_palier_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_palier_delete ON bancarisation.credit_palier FOR DELETE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: credit_palier credit_palier_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_palier_insert ON bancarisation.credit_palier FOR INSERT TO authenticated WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: credit_palier credit_palier_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_palier_select ON bancarisation.credit_palier FOR SELECT TO authenticated USING (prive.voit_finances(projet_id));


--
-- Name: credit_palier credit_palier_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_palier_update ON bancarisation.credit_palier FOR UPDATE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: credit_vente; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.credit_vente ENABLE ROW LEVEL SECURITY;

--
-- Name: credit_vente credit_vente_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_vente_delete ON bancarisation.credit_vente FOR DELETE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: credit_vente credit_vente_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_vente_insert ON bancarisation.credit_vente FOR INSERT TO authenticated WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: credit_vente credit_vente_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_vente_select ON bancarisation.credit_vente FOR SELECT TO authenticated USING (prive.voit_finances(projet_id));


--
-- Name: credit_vente credit_vente_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY credit_vente_update ON bancarisation.credit_vente FOR UPDATE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: demande; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.demande ENABLE ROW LEVEL SECURITY;

--
-- Name: demande demande_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY demande_delete ON bancarisation.demande FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: demande demande_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY demande_insert ON bancarisation.demande FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: demande_message; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.demande_message ENABLE ROW LEVEL SECURITY;

--
-- Name: demande_message demande_message_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY demande_message_delete ON bancarisation.demande_message FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: demande_message demande_message_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY demande_message_insert ON bancarisation.demande_message FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: demande_message demande_message_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY demande_message_select ON bancarisation.demande_message FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: demande_message demande_message_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY demande_message_update ON bancarisation.demande_message FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: demande demande_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY demande_select ON bancarisation.demande FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: demande demande_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY demande_update ON bancarisation.demande FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: documents; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.documents ENABLE ROW LEVEL SECURITY;

--
-- Name: documents documents_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY documents_delete ON bancarisation.documents FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 4));


--
-- Name: documents documents_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY documents_insert ON bancarisation.documents FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: documents documents_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY documents_select ON bancarisation.documents FOR SELECT TO authenticated USING (((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)) AND ((NOT prive.acces_restreint(projet_id)) OR (EXISTS ( SELECT 1
   FROM bancarisation.rapport_suivi rs
  WHERE (rs.document_id = documents.id))) OR (EXISTS ( SELECT 1
   FROM bancarisation.rapport_bilan rb
  WHERE (rb.document_id = documents.id))))));


--
-- Name: documents documents_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY documents_update ON bancarisation.documents FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: droit_documents; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.droit_documents ENABLE ROW LEVEL SECURITY;

--
-- Name: droit_documents droit_documents_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY droit_documents_delete ON bancarisation.droit_documents FOR DELETE TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: droit_documents droit_documents_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY droit_documents_insert ON bancarisation.droit_documents FOR INSERT TO authenticated WITH CHECK ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: droit_documents droit_documents_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY droit_documents_select ON bancarisation.droit_documents FOR SELECT TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: droit_documents droit_documents_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY droit_documents_update ON bancarisation.droit_documents FOR UPDATE TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global)))) WITH CHECK ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: droits_fonciers; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.droits_fonciers ENABLE ROW LEVEL SECURITY;

--
-- Name: droits_fonciers droits_fonciers_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY droits_fonciers_delete ON bancarisation.droits_fonciers FOR DELETE TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: droits_fonciers droits_fonciers_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY droits_fonciers_insert ON bancarisation.droits_fonciers FOR INSERT TO authenticated WITH CHECK ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: droits_fonciers droits_fonciers_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY droits_fonciers_select ON bancarisation.droits_fonciers FOR SELECT TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: droits_fonciers droits_fonciers_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY droits_fonciers_update ON bancarisation.droits_fonciers FOR UPDATE TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global)))) WITH CHECK ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: echeance; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.echeance ENABLE ROW LEVEL SECURITY;

--
-- Name: echeance echeance_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY echeance_delete ON bancarisation.echeance FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: echeance echeance_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY echeance_insert ON bancarisation.echeance FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: echeance echeance_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY echeance_select ON bancarisation.echeance FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: echeance echeance_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY echeance_update ON bancarisation.echeance FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: emprise_projet; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.emprise_projet ENABLE ROW LEVEL SECURITY;

--
-- Name: emprise_projet emprise_projet_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY emprise_projet_delete ON bancarisation.emprise_projet FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: emprise_projet emprise_projet_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY emprise_projet_insert ON bancarisation.emprise_projet FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: emprise_projet emprise_projet_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY emprise_projet_select ON bancarisation.emprise_projet FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: emprise_projet emprise_projet_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY emprise_projet_update ON bancarisation.emprise_projet FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: export_geomce; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.export_geomce ENABLE ROW LEVEL SECURITY;

--
-- Name: export_geomce export_geomce_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY export_geomce_delete ON bancarisation.export_geomce FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: export_geomce export_geomce_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY export_geomce_insert ON bancarisation.export_geomce FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: export_geomce export_geomce_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY export_geomce_select ON bancarisation.export_geomce FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: export_geomce export_geomce_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY export_geomce_update ON bancarisation.export_geomce FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: export_projet; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.export_projet ENABLE ROW LEVEL SECURITY;

--
-- Name: export_projet export_projet_backend_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY export_projet_backend_select ON bancarisation.export_projet FOR SELECT TO kererc_backend USING (true);


--
-- Name: export_projet export_projet_backend_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY export_projet_backend_update ON bancarisation.export_projet FOR UPDATE TO kererc_backend USING (true) WITH CHECK (true);


--
-- Name: export_projet export_projet_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY export_projet_insert ON bancarisation.export_projet FOR INSERT TO authenticated WITH CHECK ((bancarisation.peut_exporter_projet(projet_id) AND (demande_par = auth.uid()) AND (organisation_id = ( SELECT p.organisation_id
   FROM bancarisation.projets p
  WHERE (p.id = export_projet.projet_id)))));


--
-- Name: export_projet export_projet_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY export_projet_select ON bancarisation.export_projet FOR SELECT TO authenticated USING ((bancarisation.peut_exporter_projet(projet_id) OR bancarisation.peut_lire_archive_export(organisation_id)));


--
-- Name: extraction_import; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.extraction_import ENABLE ROW LEVEL SECURITY;

--
-- Name: extraction_import extraction_import_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY extraction_import_delete ON bancarisation.extraction_import FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: extraction_import extraction_import_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY extraction_import_insert ON bancarisation.extraction_import FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: extraction_import extraction_import_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY extraction_import_select ON bancarisation.extraction_import FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: extraction_import extraction_import_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY extraction_import_update ON bancarisation.extraction_import FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: geomce_communes; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.geomce_communes ENABLE ROW LEVEL SECURITY;

--
-- Name: geomce_communes geomce_communes_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY geomce_communes_select ON bancarisation.geomce_communes FOR SELECT TO authenticated USING (true);


--
-- Name: geomce_lin; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.geomce_lin ENABLE ROW LEVEL SECURITY;

--
-- Name: geomce_lin geomce_lin_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY geomce_lin_select ON bancarisation.geomce_lin FOR SELECT TO authenticated USING (true);


--
-- Name: geomce_pct; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.geomce_pct ENABLE ROW LEVEL SECURITY;

--
-- Name: geomce_pct geomce_pct_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY geomce_pct_select ON bancarisation.geomce_pct FOR SELECT TO authenticated USING (true);


--
-- Name: geomce_surf; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.geomce_surf ENABLE ROW LEVEL SECURITY;

--
-- Name: geomce_surf geomce_surf_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY geomce_surf_select ON bancarisation.geomce_surf FOR SELECT TO authenticated USING (true);


--
-- Name: geometrie_mouvement; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.geometrie_mouvement ENABLE ROW LEVEL SECURITY;

--
-- Name: geometrie_mouvement geometrie_mouvement_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY geometrie_mouvement_delete ON bancarisation.geometrie_mouvement FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: geometrie_mouvement geometrie_mouvement_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY geometrie_mouvement_insert ON bancarisation.geometrie_mouvement FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: geometrie_mouvement geometrie_mouvement_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY geometrie_mouvement_select ON bancarisation.geometrie_mouvement FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: geometrie_mouvement geometrie_mouvement_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY geometrie_mouvement_update ON bancarisation.geometrie_mouvement FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: invitation; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.invitation ENABLE ROW LEVEL SECURITY;

--
-- Name: journal_actions; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.journal_actions ENABLE ROW LEVEL SECURITY;

--
-- Name: journal_actions journal_actions_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY journal_actions_insert ON bancarisation.journal_actions FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 2));


--
-- Name: journal_actions journal_actions_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY journal_actions_select ON bancarisation.journal_actions FOR SELECT TO authenticated USING (prive.est_interne(projet_id));


--
-- Name: journal_audit; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.journal_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: journal_audit journal_audit_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY journal_audit_select ON bancarisation.journal_audit FOR SELECT TO authenticated USING ((((organisation_id IS NOT NULL) AND (prive.role_org(organisation_id) = 'admin'::text)) OR ((projet_id IS NOT NULL) AND prive.peut_partager(projet_id))));


--
-- Name: ligne_budget; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.ligne_budget ENABLE ROW LEVEL SECURITY;

--
-- Name: ligne_budget ligne_budget_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ligne_budget_delete ON bancarisation.ligne_budget FOR DELETE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: ligne_budget ligne_budget_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ligne_budget_insert ON bancarisation.ligne_budget FOR INSERT TO authenticated WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: ligne_budget ligne_budget_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ligne_budget_select ON bancarisation.ligne_budget FOR SELECT TO authenticated USING (prive.voit_finances(projet_id));


--
-- Name: ligne_budget ligne_budget_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ligne_budget_update ON bancarisation.ligne_budget FOR UPDATE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: membre; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.membre ENABLE ROW LEVEL SECURITY;

--
-- Name: membre_organisation; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.membre_organisation ENABLE ROW LEVEL SECURITY;

--
-- Name: membre_organisation membre_organisation_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY membre_organisation_select ON bancarisation.membre_organisation FOR SELECT TO authenticated USING (((utilisateur_id = ( SELECT auth.uid() AS uid)) OR (organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global)))));


--
-- Name: note_interne; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.note_interne ENABLE ROW LEVEL SECURITY;

--
-- Name: note_interne note_interne_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY note_interne_delete ON bancarisation.note_interne FOR DELETE TO authenticated USING (((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: note_interne note_interne_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY note_interne_insert ON bancarisation.note_interne FOR INSERT TO authenticated WITH CHECK (((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: note_interne note_interne_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY note_interne_select ON bancarisation.note_interne FOR SELECT TO authenticated USING (((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))) AND (projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles))));


--
-- Name: note_interne note_interne_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY note_interne_update ON bancarisation.note_interne FOR UPDATE TO authenticated USING (((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK (((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: occurrence; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.occurrence ENABLE ROW LEVEL SECURITY;

--
-- Name: occurrence occurrence_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_delete ON bancarisation.occurrence FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: occurrence_finance; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.occurrence_finance ENABLE ROW LEVEL SECURITY;

--
-- Name: occurrence_finance occurrence_finance_ajout; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_finance_ajout ON bancarisation.occurrence_finance FOR INSERT TO authenticated WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: occurrence_finance occurrence_finance_lecture; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_finance_lecture ON bancarisation.occurrence_finance FOR SELECT TO authenticated USING (prive.voit_finances(projet_id));


--
-- Name: occurrence_finance occurrence_finance_modif; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_finance_modif ON bancarisation.occurrence_finance FOR UPDATE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: occurrence_finance occurrence_finance_suppr; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_finance_suppr ON bancarisation.occurrence_finance FOR DELETE TO authenticated USING ((prive.voit_finances(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: occurrence occurrence_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_insert ON bancarisation.occurrence FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: occurrence_responsable; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.occurrence_responsable ENABLE ROW LEVEL SECURITY;

--
-- Name: occurrence_responsable occurrence_responsable_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_responsable_delete ON bancarisation.occurrence_responsable FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: occurrence_responsable occurrence_responsable_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_responsable_insert ON bancarisation.occurrence_responsable FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: occurrence_responsable occurrence_responsable_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_responsable_select ON bancarisation.occurrence_responsable FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: occurrence_responsable occurrence_responsable_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_responsable_update ON bancarisation.occurrence_responsable FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: occurrence occurrence_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_select ON bancarisation.occurrence FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: occurrence occurrence_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY occurrence_update ON bancarisation.occurrence FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: organisation_closure; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.organisation_closure ENABLE ROW LEVEL SECURITY;

--
-- Name: organisation_closure organisation_closure_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY organisation_closure_select ON bancarisation.organisation_closure FOR SELECT TO authenticated USING (((ancetre_id IN ( SELECT prive.organisations_lisibles() AS organisations_lisibles)) AND (descendant_id IN ( SELECT prive.organisations_lisibles() AS organisations_lisibles))));


--
-- Name: organisations; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.organisations ENABLE ROW LEVEL SECURITY;

--
-- Name: organisations organisations_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY organisations_select ON bancarisation.organisations FOR SELECT TO authenticated USING ((id IN ( SELECT prive.organisations_lisibles() AS organisations_lisibles)));


--
-- Name: parametre_signal; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.parametre_signal ENABLE ROW LEVEL SECURITY;

--
-- Name: parametre_signal parametre_signal_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY parametre_signal_select ON bancarisation.parametre_signal FOR SELECT TO authenticated USING (true);


--
-- Name: parcelles; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.parcelles ENABLE ROW LEVEL SECURITY;

--
-- Name: parcelles parcelles_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY parcelles_delete ON bancarisation.parcelles FOR DELETE TO authenticated USING ((((projet_id IS NOT NULL) AND prive.est_interne(projet_id) AND (prive.niveau_projet(projet_id) >= 3)) OR ((projet_id IS NULL) AND (organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))))));


--
-- Name: parcelles parcelles_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY parcelles_insert ON bancarisation.parcelles FOR INSERT TO authenticated WITH CHECK ((((projet_id IS NOT NULL) AND prive.est_interne(projet_id) AND (prive.niveau_projet(projet_id) >= 3)) OR ((projet_id IS NULL) AND (organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))))));


--
-- Name: parcelles parcelles_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY parcelles_select ON bancarisation.parcelles FOR SELECT TO authenticated USING ((((projet_id IS NOT NULL) AND prive.est_interne(projet_id)) OR ((projet_id IS NULL) AND (organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))))));


--
-- Name: parcelles parcelles_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY parcelles_update ON bancarisation.parcelles FOR UPDATE TO authenticated USING ((((projet_id IS NOT NULL) AND prive.est_interne(projet_id) AND (prive.niveau_projet(projet_id) >= 3)) OR ((projet_id IS NULL) AND (organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global)))))) WITH CHECK ((((projet_id IS NOT NULL) AND prive.est_interne(projet_id) AND (prive.niveau_projet(projet_id) >= 3)) OR ((projet_id IS NULL) AND (organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))))));


--
-- Name: personnes; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.personnes ENABLE ROW LEVEL SECURITY;

--
-- Name: personnes personnes_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY personnes_delete ON bancarisation.personnes FOR DELETE TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: personnes personnes_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY personnes_insert ON bancarisation.personnes FOR INSERT TO authenticated WITH CHECK ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: personnes personnes_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY personnes_select ON bancarisation.personnes FOR SELECT TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: personnes personnes_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY personnes_update ON bancarisation.personnes FOR UPDATE TO authenticated USING ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global)))) WITH CHECK ((organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: plan_cao; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.plan_cao ENABLE ROW LEVEL SECURITY;

--
-- Name: plan_cao_calque; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.plan_cao_calque ENABLE ROW LEVEL SECURITY;

--
-- Name: plan_cao_calque plan_cao_calque_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_calque_delete ON bancarisation.plan_cao_calque FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao_calque plan_cao_calque_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_calque_insert ON bancarisation.plan_cao_calque FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao_calque plan_cao_calque_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_calque_select ON bancarisation.plan_cao_calque FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: plan_cao_calque plan_cao_calque_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_calque_update ON bancarisation.plan_cao_calque FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao plan_cao_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_delete ON bancarisation.plan_cao FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao_entite; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.plan_cao_entite ENABLE ROW LEVEL SECURITY;

--
-- Name: plan_cao_entite plan_cao_entite_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_entite_delete ON bancarisation.plan_cao_entite FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao_entite plan_cao_entite_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_entite_insert ON bancarisation.plan_cao_entite FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao_entite plan_cao_entite_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_entite_select ON bancarisation.plan_cao_entite FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: plan_cao_entite plan_cao_entite_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_entite_update ON bancarisation.plan_cao_entite FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao_groupe; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.plan_cao_groupe ENABLE ROW LEVEL SECURITY;

--
-- Name: plan_cao_groupe plan_cao_groupe_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_groupe_delete ON bancarisation.plan_cao_groupe FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao_groupe plan_cao_groupe_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_groupe_insert ON bancarisation.plan_cao_groupe FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao_groupe plan_cao_groupe_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_groupe_select ON bancarisation.plan_cao_groupe FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: plan_cao_groupe plan_cao_groupe_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_groupe_update ON bancarisation.plan_cao_groupe FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao plan_cao_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_insert ON bancarisation.plan_cao FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: plan_cao plan_cao_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_select ON bancarisation.plan_cao FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: plan_cao plan_cao_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY plan_cao_update ON bancarisation.plan_cao FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: prescription_couverture; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.prescription_couverture ENABLE ROW LEVEL SECURITY;

--
-- Name: prescription_couverture prescription_couverture_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY prescription_couverture_delete ON bancarisation.prescription_couverture FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: prescription_couverture prescription_couverture_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY prescription_couverture_insert ON bancarisation.prescription_couverture FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: prescription_couverture prescription_couverture_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY prescription_couverture_select ON bancarisation.prescription_couverture FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: prescription_couverture prescription_couverture_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY prescription_couverture_update ON bancarisation.prescription_couverture FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: prestataires; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.prestataires ENABLE ROW LEVEL SECURITY;

--
-- Name: prestataires prestataires_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY prestataires_insert ON bancarisation.prestataires FOR INSERT TO authenticated WITH CHECK ((EXISTS ( SELECT 1
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: prestataires prestataires_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY prestataires_select ON bancarisation.prestataires FOR SELECT TO authenticated USING (true);


--
-- Name: prestataires prestataires_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY prestataires_update ON bancarisation.prestataires FOR UPDATE TO authenticated USING ((EXISTS ( SELECT 1
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global)))) WITH CHECK ((EXISTS ( SELECT 1
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))));


--
-- Name: profils; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.profils ENABLE ROW LEVEL SECURITY;

--
-- Name: profils profils_select_collegues; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY profils_select_collegues ON bancarisation.profils FOR SELECT TO authenticated USING (((utilisateur_id IN ( SELECT m.utilisateur_id
   FROM bancarisation.membre_organisation m
  WHERE (m.organisation_id IN ( SELECT mes_appartenances.organisation_id
           FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))))) OR (utilisateur_id IN ( SELECT pa.utilisateur_id
   FROM bancarisation.projet_acces pa
  WHERE (pa.projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles))))));


--
-- Name: profils profils_select_soi; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY profils_select_soi ON bancarisation.profils FOR SELECT TO authenticated USING ((utilisateur_id = ( SELECT auth.uid() AS uid)));


--
-- Name: profils profils_update_soi; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY profils_update_soi ON bancarisation.profils FOR UPDATE TO authenticated USING ((utilisateur_id = ( SELECT auth.uid() AS uid))) WITH CHECK ((utilisateur_id = ( SELECT auth.uid() AS uid)));


--
-- Name: projet_acces; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.projet_acces ENABLE ROW LEVEL SECURITY;

--
-- Name: projet_acces projet_acces_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_acces_delete ON bancarisation.projet_acces FOR DELETE TO authenticated USING (prive.peut_partager(projet_id));


--
-- Name: projet_acces projet_acces_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_acces_insert ON bancarisation.projet_acces FOR INSERT TO authenticated WITH CHECK (prive.peut_partager(projet_id));


--
-- Name: projet_acces projet_acces_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_acces_select ON bancarisation.projet_acces FOR SELECT TO authenticated USING (((utilisateur_id = ( SELECT auth.uid() AS uid)) OR (organisation_id IN ( SELECT mes_appartenances.organisation_id
   FROM prive.mes_appartenances() mes_appartenances(organisation_id, role, acces_global))) OR prive.peut_partager(projet_id)));


--
-- Name: projet_acces projet_acces_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_acces_update ON bancarisation.projet_acces FOR UPDATE TO authenticated USING (prive.peut_partager(projet_id)) WITH CHECK (prive.peut_partager(projet_id));


--
-- Name: projet_contact; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.projet_contact ENABLE ROW LEVEL SECURITY;

--
-- Name: projet_contact projet_contact_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_contact_delete ON bancarisation.projet_contact FOR DELETE TO authenticated USING ((prive.est_interne(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: projet_contact projet_contact_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_contact_insert ON bancarisation.projet_contact FOR INSERT TO authenticated WITH CHECK ((prive.est_interne(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: projet_contact projet_contact_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_contact_select ON bancarisation.projet_contact FOR SELECT TO authenticated USING (((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)) AND (prive.est_interne(projet_id) OR partage_partenaires)));


--
-- Name: projet_contact projet_contact_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_contact_update ON bancarisation.projet_contact FOR UPDATE TO authenticated USING ((prive.est_interne(projet_id) AND (prive.niveau_projet(projet_id) >= 3))) WITH CHECK ((prive.est_interne(projet_id) AND (prive.niveau_projet(projet_id) >= 3)));


--
-- Name: projet_geometries; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.projet_geometries ENABLE ROW LEVEL SECURITY;

--
-- Name: projet_geometries projet_geometries_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_geometries_delete ON bancarisation.projet_geometries FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projet_geometries projet_geometries_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_geometries_insert ON bancarisation.projet_geometries FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projet_geometries projet_geometries_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_geometries_select ON bancarisation.projet_geometries FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: projet_geometries projet_geometries_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_geometries_update ON bancarisation.projet_geometries FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projet_metadata; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.projet_metadata ENABLE ROW LEVEL SECURITY;

--
-- Name: projet_metadata projet_metadata_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_metadata_delete ON bancarisation.projet_metadata FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projet_metadata projet_metadata_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_metadata_insert ON bancarisation.projet_metadata FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projet_metadata projet_metadata_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_metadata_select ON bancarisation.projet_metadata FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: projet_metadata projet_metadata_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_metadata_update ON bancarisation.projet_metadata FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projet_prestataire; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.projet_prestataire ENABLE ROW LEVEL SECURITY;

--
-- Name: projet_prestataire projet_prestataire_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_prestataire_delete ON bancarisation.projet_prestataire FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projet_prestataire projet_prestataire_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_prestataire_insert ON bancarisation.projet_prestataire FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projet_prestataire projet_prestataire_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_prestataire_select ON bancarisation.projet_prestataire FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: projet_prestataire projet_prestataire_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projet_prestataire_update ON bancarisation.projet_prestataire FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: projets; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.projets ENABLE ROW LEVEL SECURITY;

--
-- Name: projets projets_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projets_delete ON bancarisation.projets FOR DELETE TO authenticated USING ((prive.role_org(organisation_id) = 'admin'::text));


--
-- Name: projets projets_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projets_insert ON bancarisation.projets FOR INSERT TO authenticated WITH CHECK ((prive.role_org(organisation_id) IS NOT NULL));


--
-- Name: projets projets_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projets_select ON bancarisation.projets FOR SELECT TO authenticated USING ((id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: projets projets_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY projets_update ON bancarisation.projets FOR UPDATE TO authenticated USING ((prive.niveau_projet(id) >= 3)) WITH CHECK ((prive.niveau_projet(id) >= 3));


--
-- Name: rapport_bilan; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.rapport_bilan ENABLE ROW LEVEL SECURITY;

--
-- Name: rapport_bilan rapport_bilan_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY rapport_bilan_delete ON bancarisation.rapport_bilan FOR DELETE TO authenticated USING (((prive.niveau_projet(projet_id) >= 3) AND ((type_bilan = 'ecologique'::text) OR prive.voit_finances(projet_id))));


--
-- Name: rapport_bilan rapport_bilan_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY rapport_bilan_insert ON bancarisation.rapport_bilan FOR INSERT TO authenticated WITH CHECK (((prive.niveau_projet(projet_id) >= 3) AND ((type_bilan = 'ecologique'::text) OR prive.voit_finances(projet_id)) AND ((statut <> 'valide'::text) OR (prive.niveau_projet(projet_id) = 4))));


--
-- Name: rapport_bilan rapport_bilan_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY rapport_bilan_select ON bancarisation.rapport_bilan FOR SELECT TO authenticated USING (((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)) AND ((type_bilan = 'ecologique'::text) OR prive.voit_finances(projet_id)) AND (prive.est_interne(projet_id) OR (statut = 'valide'::text))));


--
-- Name: rapport_bilan rapport_bilan_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY rapport_bilan_update ON bancarisation.rapport_bilan FOR UPDATE TO authenticated USING (((prive.niveau_projet(projet_id) >= 3) AND ((type_bilan = 'ecologique'::text) OR prive.voit_finances(projet_id)))) WITH CHECK (((prive.niveau_projet(projet_id) >= 3) AND ((type_bilan = 'ecologique'::text) OR prive.voit_finances(projet_id)) AND ((statut <> 'valide'::text) OR (prive.niveau_projet(projet_id) = 4))));


--
-- Name: rapport_suivi; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.rapport_suivi ENABLE ROW LEVEL SECURITY;

--
-- Name: rapport_suivi rapport_suivi_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY rapport_suivi_delete ON bancarisation.rapport_suivi FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: rapport_suivi rapport_suivi_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY rapport_suivi_insert ON bancarisation.rapport_suivi FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: rapport_suivi rapport_suivi_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY rapport_suivi_select ON bancarisation.rapport_suivi FOR SELECT TO authenticated USING (((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)) AND (prive.est_interne(projet_id) OR (statut = 'depose'::text))));


--
-- Name: rapport_suivi rapport_suivi_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY rapport_suivi_update ON bancarisation.rapport_suivi FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ref_geomce_categorie; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.ref_geomce_categorie ENABLE ROW LEVEL SECURITY;

--
-- Name: ref_geomce_categorie ref_geomce_categorie_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ref_geomce_categorie_select ON bancarisation.ref_geomce_categorie FOR SELECT TO authenticated USING (true);


--
-- Name: role_contact_ref; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.role_contact_ref ENABLE ROW LEVEL SECURITY;

--
-- Name: role_contact_ref role_contact_ref_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY role_contact_ref_select ON bancarisation.role_contact_ref FOR SELECT TO authenticated USING (true);


--
-- Name: satellite_captures; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.satellite_captures ENABLE ROW LEVEL SECURITY;

--
-- Name: satellite_captures satellite_captures_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY satellite_captures_delete ON bancarisation.satellite_captures FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: satellite_captures satellite_captures_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY satellite_captures_insert ON bancarisation.satellite_captures FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: satellite_captures satellite_captures_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY satellite_captures_select ON bancarisation.satellite_captures FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: satellite_captures satellite_captures_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY satellite_captures_update ON bancarisation.satellite_captures FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: site_agrement; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.site_agrement ENABLE ROW LEVEL SECURITY;

--
-- Name: site_agrement site_agrement_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY site_agrement_delete ON bancarisation.site_agrement FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: site_agrement site_agrement_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY site_agrement_insert ON bancarisation.site_agrement FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: site_agrement site_agrement_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY site_agrement_select ON bancarisation.site_agrement FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: site_agrement site_agrement_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY site_agrement_update ON bancarisation.site_agrement FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: transfert_projet; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.transfert_projet ENABLE ROW LEVEL SECURITY;

--
-- Name: transfert_projet transfert_projet_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY transfert_projet_select ON bancarisation.transfert_projet FOR SELECT TO authenticated USING ((prive.peut_partager(projet_id) OR (prive.role_org(org_source) = 'admin'::text) OR (prive.role_org(org_cible) = 'admin'::text)));


--
-- Name: ug_lin_parcelles; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.ug_lin_parcelles ENABLE ROW LEVEL SECURITY;

--
-- Name: ug_lin_parcelles ug_lin_parcelles_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_lin_parcelles_delete ON bancarisation.ug_lin_parcelles FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ug_lin_parcelles ug_lin_parcelles_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_lin_parcelles_insert ON bancarisation.ug_lin_parcelles FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ug_lin_parcelles ug_lin_parcelles_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_lin_parcelles_select ON bancarisation.ug_lin_parcelles FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: ug_lin_parcelles ug_lin_parcelles_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_lin_parcelles_update ON bancarisation.ug_lin_parcelles FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ug_pct_parcelles; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.ug_pct_parcelles ENABLE ROW LEVEL SECURITY;

--
-- Name: ug_pct_parcelles ug_pct_parcelles_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_pct_parcelles_delete ON bancarisation.ug_pct_parcelles FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ug_pct_parcelles ug_pct_parcelles_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_pct_parcelles_insert ON bancarisation.ug_pct_parcelles FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ug_pct_parcelles ug_pct_parcelles_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_pct_parcelles_select ON bancarisation.ug_pct_parcelles FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: ug_pct_parcelles ug_pct_parcelles_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_pct_parcelles_update ON bancarisation.ug_pct_parcelles FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ug_surf_parcelles; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.ug_surf_parcelles ENABLE ROW LEVEL SECURITY;

--
-- Name: ug_surf_parcelles ug_surf_parcelles_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_surf_parcelles_delete ON bancarisation.ug_surf_parcelles FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ug_surf_parcelles ug_surf_parcelles_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_surf_parcelles_insert ON bancarisation.ug_surf_parcelles FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: ug_surf_parcelles ug_surf_parcelles_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_surf_parcelles_select ON bancarisation.ug_surf_parcelles FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: ug_surf_parcelles ug_surf_parcelles_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY ug_surf_parcelles_update ON bancarisation.ug_surf_parcelles FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_lin; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.unites_de_gestion_lin ENABLE ROW LEVEL SECURITY;

--
-- Name: unites_de_gestion_lin unites_de_gestion_lin_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_lin_delete ON bancarisation.unites_de_gestion_lin FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_lin unites_de_gestion_lin_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_lin_insert ON bancarisation.unites_de_gestion_lin FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_lin unites_de_gestion_lin_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_lin_select ON bancarisation.unites_de_gestion_lin FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: unites_de_gestion_lin unites_de_gestion_lin_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_lin_update ON bancarisation.unites_de_gestion_lin FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_pct; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.unites_de_gestion_pct ENABLE ROW LEVEL SECURITY;

--
-- Name: unites_de_gestion_pct unites_de_gestion_pct_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_pct_delete ON bancarisation.unites_de_gestion_pct FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_pct unites_de_gestion_pct_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_pct_insert ON bancarisation.unites_de_gestion_pct FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_pct unites_de_gestion_pct_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_pct_select ON bancarisation.unites_de_gestion_pct FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: unites_de_gestion_pct unites_de_gestion_pct_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_pct_update ON bancarisation.unites_de_gestion_pct FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_surf; Type: ROW SECURITY; Schema: bancarisation; Owner: postgres
--

ALTER TABLE bancarisation.unites_de_gestion_surf ENABLE ROW LEVEL SECURITY;

--
-- Name: unites_de_gestion_surf unites_de_gestion_surf_delete; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_surf_delete ON bancarisation.unites_de_gestion_surf FOR DELETE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_surf unites_de_gestion_surf_insert; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_surf_insert ON bancarisation.unites_de_gestion_surf FOR INSERT TO authenticated WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: unites_de_gestion_surf unites_de_gestion_surf_select; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_surf_select ON bancarisation.unites_de_gestion_surf FOR SELECT TO authenticated USING ((projet_id IN ( SELECT prive.projets_lisibles() AS projets_lisibles)));


--
-- Name: unites_de_gestion_surf unites_de_gestion_surf_update; Type: POLICY; Schema: bancarisation; Owner: postgres
--

CREATE POLICY unites_de_gestion_surf_update ON bancarisation.unites_de_gestion_surf FOR UPDATE TO authenticated USING ((prive.niveau_projet(projet_id) >= 3)) WITH CHECK ((prive.niveau_projet(projet_id) >= 3));


--
-- Name: SCHEMA bancarisation; Type: ACL; Schema: -; Owner: postgres
--

GRANT USAGE ON SCHEMA bancarisation TO anon;
GRANT USAGE ON SCHEMA bancarisation TO authenticated;
GRANT USAGE ON SCHEMA bancarisation TO service_role;
GRANT USAGE ON SCHEMA bancarisation TO kererc_backend;


--
-- Name: SCHEMA prive; Type: ACL; Schema: -; Owner: postgres
--

GRANT USAGE ON SCHEMA prive TO authenticated;


--
-- Name: FUNCTION accepter_transfert(p_transfert uuid); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.accepter_transfert(p_transfert uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.accepter_transfert(p_transfert uuid) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.accepter_transfert(p_transfert uuid) TO service_role;


--
-- Name: FUNCTION annuler_transfert(p_transfert uuid); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.annuler_transfert(p_transfert uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.annuler_transfert(p_transfert uuid) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.annuler_transfert(p_transfert uuid) TO service_role;


--
-- Name: FUNCTION appliquer_calage_plan(p_plan_id uuid); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.appliquer_calage_plan(p_plan_id uuid) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.appliquer_calage_plan(p_plan_id uuid) TO service_role;


--
-- Name: FUNCTION appliquer_checklist_modele(p_modele_id uuid, p_occurrence_ids uuid[]); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.appliquer_checklist_modele(p_modele_id uuid, p_occurrence_ids uuid[]) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.appliquer_checklist_modele(p_modele_id uuid, p_occurrence_ids uuid[]) TO service_role;


--
-- Name: FUNCTION contacts_partages(p_projet uuid); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.contacts_partages(p_projet uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.contacts_partages(p_projet uuid) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.contacts_partages(p_projet uuid) TO service_role;


--
-- Name: FUNCTION creer_modele_depuis_action(p_occurrence_id uuid, p_nom text, p_organisation_id uuid); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.creer_modele_depuis_action(p_occurrence_id uuid, p_nom text, p_organisation_id uuid) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.creer_modele_depuis_action(p_occurrence_id uuid, p_nom text, p_organisation_id uuid) TO service_role;


--
-- Name: FUNCTION demander_transfert(p_projet uuid, p_org_cible uuid, p_garder_lecture_source boolean, p_finances_source boolean, p_commentaire text); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.demander_transfert(p_projet uuid, p_org_cible uuid, p_garder_lecture_source boolean, p_finances_source boolean, p_commentaire text) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.demander_transfert(p_projet uuid, p_org_cible uuid, p_garder_lecture_source boolean, p_finances_source boolean, p_commentaire text) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.demander_transfert(p_projet uuid, p_org_cible uuid, p_garder_lecture_source boolean, p_finances_source boolean, p_commentaire text) TO service_role;


--
-- Name: FUNCTION log_budget_mouvement(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.log_budget_mouvement() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.log_budget_mouvement() TO service_role;


--
-- Name: FUNCTION log_occurrence_finance(); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.log_occurrence_finance() FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.log_occurrence_finance() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.log_occurrence_finance() TO service_role;


--
-- Name: FUNCTION maj_statut_demande(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.maj_statut_demande() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.maj_statut_demande() TO service_role;


--
-- Name: FUNCTION mes_droits_projets(p_ids uuid[]); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.mes_droits_projets(p_ids uuid[]) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.mes_droits_projets(p_ids uuid[]) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.mes_droits_projets(p_ids uuid[]) TO service_role;


--
-- Name: FUNCTION mon_contexte(); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.mon_contexte() FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.mon_contexte() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.mon_contexte() TO service_role;


--
-- Name: FUNCTION normalize_ug_id(raw text); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.normalize_ug_id(raw text) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.normalize_ug_id(raw text) TO service_role;


--
-- Name: FUNCTION normalize_ug_ids(arr text[]); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.normalize_ug_ids(arr text[]) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.normalize_ug_ids(arr text[]) TO service_role;


--
-- Name: FUNCTION peut_exporter_projet(p_projet_id uuid); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.peut_exporter_projet(p_projet_id uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.peut_exporter_projet(p_projet_id uuid) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.peut_exporter_projet(p_projet_id uuid) TO service_role;


--
-- Name: FUNCTION peut_lire_archive_export(p_organisation uuid); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.peut_lire_archive_export(p_organisation uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.peut_lire_archive_export(p_organisation uuid) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.peut_lire_archive_export(p_organisation uuid) TO service_role;


--
-- Name: FUNCTION proteger_rapport_valide(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.proteger_rapport_valide() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.proteger_rapport_valide() TO service_role;


--
-- Name: FUNCTION rattacher_export_transfert(p_transfert uuid, p_export uuid); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.rattacher_export_transfert(p_transfert uuid, p_export uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.rattacher_export_transfert(p_transfert uuid, p_export uuid) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.rattacher_export_transfert(p_transfert uuid, p_export uuid) TO service_role;


--
-- Name: FUNCTION rebuild_organisation_closure(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.rebuild_organisation_closure() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.rebuild_organisation_closure() TO service_role;


--
-- Name: FUNCTION refuser_transfert(p_transfert uuid, p_motif text); Type: ACL; Schema: bancarisation; Owner: postgres
--

REVOKE ALL ON FUNCTION bancarisation.refuser_transfert(p_transfert uuid, p_motif text) FROM PUBLIC;
GRANT ALL ON FUNCTION bancarisation.refuser_transfert(p_transfert uuid, p_motif text) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.refuser_transfert(p_transfert uuid, p_motif text) TO service_role;


--
-- Name: FUNCTION seuil(p_code text); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.seuil(p_code text) TO authenticated;
GRANT ALL ON FUNCTION bancarisation.seuil(p_code text) TO service_role;


--
-- Name: FUNCTION tg_action_etape_before(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.tg_action_etape_before() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.tg_action_etape_before() TO service_role;


--
-- Name: FUNCTION tg_note_interne_before(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.tg_note_interne_before() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.tg_note_interne_before() TO service_role;


--
-- Name: FUNCTION tg_occurrence_responsable_before(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.tg_occurrence_responsable_before() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.tg_occurrence_responsable_before() TO service_role;


--
-- Name: FUNCTION trg_credit_vente_affecte_terminal(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.trg_credit_vente_affecte_terminal() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.trg_credit_vente_affecte_terminal() TO service_role;


--
-- Name: FUNCTION trg_credit_vente_stock(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.trg_credit_vente_stock() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.trg_credit_vente_stock() TO service_role;


--
-- Name: FUNCTION trg_organisation_closure(); Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON FUNCTION bancarisation.trg_organisation_closure() TO authenticated;
GRANT ALL ON FUNCTION bancarisation.trg_organisation_closure() TO service_role;


--
-- Name: FUNCTION acces_restreint(p_projet uuid); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.acces_restreint(p_projet uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION prive.acces_restreint(p_projet uuid) TO authenticated;


--
-- Name: FUNCTION creer_profil(); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.creer_profil() FROM PUBLIC;


--
-- Name: FUNCTION deriver_rattachement(); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.deriver_rattachement() FROM PUBLIC;


--
-- Name: FUNCTION est_interne(p_projet uuid); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.est_interne(p_projet uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION prive.est_interne(p_projet uuid) TO authenticated;


--
-- Name: FUNCTION interdire_modification(); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.interdire_modification() FROM PUBLIC;


--
-- Name: FUNCTION mes_appartenances(); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.mes_appartenances() FROM PUBLIC;
GRANT ALL ON FUNCTION prive.mes_appartenances() TO authenticated;


--
-- Name: FUNCTION niveau_projet(p_projet uuid); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.niveau_projet(p_projet uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION prive.niveau_projet(p_projet uuid) TO authenticated;


--
-- Name: FUNCTION niveau_valeur(p_niveau text); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.niveau_valeur(p_niveau text) FROM PUBLIC;
GRANT ALL ON FUNCTION prive.niveau_valeur(p_niveau text) TO authenticated;


--
-- Name: FUNCTION occurrence_finance_projet(); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.occurrence_finance_projet() FROM PUBLIC;
GRANT ALL ON FUNCTION prive.occurrence_finance_projet() TO authenticated;


--
-- Name: FUNCTION organisation_auteur(p_projet uuid); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.organisation_auteur(p_projet uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION prive.organisation_auteur(p_projet uuid) TO authenticated;


--
-- Name: FUNCTION organisations_lisibles(); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.organisations_lisibles() FROM PUBLIC;
GRANT ALL ON FUNCTION prive.organisations_lisibles() TO authenticated;


--
-- Name: FUNCTION peut_partager(p_projet uuid); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.peut_partager(p_projet uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION prive.peut_partager(p_projet uuid) TO authenticated;


--
-- Name: FUNCTION projets_lisibles(); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.projets_lisibles() FROM PUBLIC;
GRANT ALL ON FUNCTION prive.projets_lisibles() TO authenticated;


--
-- Name: FUNCTION role_org(p_org uuid); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.role_org(p_org uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION prive.role_org(p_org uuid) TO authenticated;


--
-- Name: FUNCTION voit_finances(p_projet uuid); Type: ACL; Schema: prive; Owner: postgres
--

REVOKE ALL ON FUNCTION prive.voit_finances(p_projet uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION prive.voit_finances(p_projet uuid) TO authenticated;


--
-- Name: TABLE acte_dreal; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.acte_dreal TO authenticated;
GRANT ALL ON TABLE bancarisation.acte_dreal TO service_role;


--
-- Name: TABLE action_etape; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.action_etape TO authenticated;
GRANT ALL ON TABLE bancarisation.action_etape TO service_role;


--
-- Name: TABLE action_fiche; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.action_fiche TO authenticated;
GRANT ALL ON TABLE bancarisation.action_fiche TO service_role;


--
-- Name: TABLE action_fiche_arrete; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.action_fiche_arrete TO authenticated;
GRANT ALL ON TABLE bancarisation.action_fiche_arrete TO service_role;


--
-- Name: TABLE annotation_terrain; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.annotation_terrain TO authenticated;
GRANT ALL ON TABLE bancarisation.annotation_terrain TO service_role;


--
-- Name: TABLE arrete; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.arrete TO authenticated;
GRANT ALL ON TABLE bancarisation.arrete TO service_role;


--
-- Name: TABLE arrete_prescription; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.arrete_prescription TO authenticated;
GRANT ALL ON TABLE bancarisation.arrete_prescription TO service_role;


--
-- Name: TABLE bilan_suivi; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.bilan_suivi TO authenticated;
GRANT ALL ON TABLE bancarisation.bilan_suivi TO service_role;


--
-- Name: TABLE budget_baseline; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.budget_baseline TO authenticated;
GRANT ALL ON TABLE bancarisation.budget_baseline TO service_role;


--
-- Name: TABLE budget_import; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.budget_import TO authenticated;
GRANT ALL ON TABLE bancarisation.budget_import TO service_role;


--
-- Name: TABLE budget_mouvement; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.budget_mouvement TO authenticated;
GRANT ALL ON TABLE bancarisation.budget_mouvement TO service_role;


--
-- Name: TABLE cadastre_parcelle; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.cadastre_parcelle TO authenticated;
GRANT ALL ON TABLE bancarisation.cadastre_parcelle TO service_role;


--
-- Name: TABLE cadastre_parcelle_ug; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.cadastre_parcelle_ug TO authenticated;
GRANT ALL ON TABLE bancarisation.cadastre_parcelle_ug TO service_role;


--
-- Name: TABLE checklist_modele; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.checklist_modele TO authenticated;
GRANT ALL ON TABLE bancarisation.checklist_modele TO service_role;


--
-- Name: TABLE checklist_modele_etape; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.checklist_modele_etape TO authenticated;
GRANT ALL ON TABLE bancarisation.checklist_modele_etape TO service_role;


--
-- Name: TABLE constat_controle; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.constat_controle TO authenticated;
GRANT ALL ON TABLE bancarisation.constat_controle TO service_role;


--
-- Name: TABLE constat_gain; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.constat_gain TO authenticated;
GRANT ALL ON TABLE bancarisation.constat_gain TO service_role;


--
-- Name: SEQUENCE constat_gain_id_seq; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON SEQUENCE bancarisation.constat_gain_id_seq TO authenticated;
GRANT ALL ON SEQUENCE bancarisation.constat_gain_id_seq TO service_role;


--
-- Name: TABLE couche_non_georeferencee; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.couche_non_georeferencee TO authenticated;
GRANT ALL ON TABLE bancarisation.couche_non_georeferencee TO service_role;


--
-- Name: TABLE credit_lot; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.credit_lot TO authenticated;
GRANT ALL ON TABLE bancarisation.credit_lot TO service_role;


--
-- Name: SEQUENCE credit_lot_id_seq; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON SEQUENCE bancarisation.credit_lot_id_seq TO authenticated;
GRANT ALL ON SEQUENCE bancarisation.credit_lot_id_seq TO service_role;


--
-- Name: TABLE credit_palier; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.credit_palier TO authenticated;
GRANT ALL ON TABLE bancarisation.credit_palier TO service_role;


--
-- Name: SEQUENCE credit_palier_id_seq; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON SEQUENCE bancarisation.credit_palier_id_seq TO authenticated;
GRANT ALL ON SEQUENCE bancarisation.credit_palier_id_seq TO service_role;


--
-- Name: TABLE credit_vente; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.credit_vente TO authenticated;
GRANT ALL ON TABLE bancarisation.credit_vente TO service_role;


--
-- Name: SEQUENCE credit_vente_id_seq; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON SEQUENCE bancarisation.credit_vente_id_seq TO authenticated;
GRANT ALL ON SEQUENCE bancarisation.credit_vente_id_seq TO service_role;


--
-- Name: TABLE demande; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.demande TO authenticated;
GRANT ALL ON TABLE bancarisation.demande TO service_role;


--
-- Name: TABLE demande_message; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.demande_message TO authenticated;
GRANT ALL ON TABLE bancarisation.demande_message TO service_role;


--
-- Name: TABLE documents; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.documents TO authenticated;
GRANT ALL ON TABLE bancarisation.documents TO service_role;


--
-- Name: TABLE droit_documents; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.droit_documents TO authenticated;
GRANT ALL ON TABLE bancarisation.droit_documents TO service_role;


--
-- Name: TABLE droits_fonciers; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.droits_fonciers TO authenticated;
GRANT ALL ON TABLE bancarisation.droits_fonciers TO service_role;


--
-- Name: TABLE echeance; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.echeance TO authenticated;
GRANT ALL ON TABLE bancarisation.echeance TO service_role;


--
-- Name: TABLE emprise_projet; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.emprise_projet TO authenticated;
GRANT ALL ON TABLE bancarisation.emprise_projet TO service_role;


--
-- Name: TABLE export_geomce; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.export_geomce TO authenticated;
GRANT ALL ON TABLE bancarisation.export_geomce TO service_role;


--
-- Name: TABLE export_projet; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.export_projet TO service_role;
GRANT SELECT,INSERT ON TABLE bancarisation.export_projet TO authenticated;
GRANT SELECT,UPDATE ON TABLE bancarisation.export_projet TO kererc_backend;


--
-- Name: TABLE extraction_import; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.extraction_import TO authenticated;
GRANT ALL ON TABLE bancarisation.extraction_import TO service_role;


--
-- Name: TABLE geomce_communes; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.geomce_communes TO authenticated;
GRANT ALL ON TABLE bancarisation.geomce_communes TO service_role;


--
-- Name: TABLE geomce_lin; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.geomce_lin TO authenticated;
GRANT ALL ON TABLE bancarisation.geomce_lin TO service_role;


--
-- Name: TABLE geomce_pct; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.geomce_pct TO authenticated;
GRANT ALL ON TABLE bancarisation.geomce_pct TO service_role;


--
-- Name: TABLE geomce_surf; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.geomce_surf TO authenticated;
GRANT ALL ON TABLE bancarisation.geomce_surf TO service_role;


--
-- Name: TABLE geometrie_mouvement; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.geometrie_mouvement TO authenticated;
GRANT ALL ON TABLE bancarisation.geometrie_mouvement TO service_role;


--
-- Name: TABLE invitation; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.invitation TO service_role;


--
-- Name: TABLE journal_actions; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.journal_actions TO authenticated;
GRANT ALL ON TABLE bancarisation.journal_actions TO service_role;


--
-- Name: TABLE journal_audit; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.journal_audit TO service_role;
GRANT SELECT ON TABLE bancarisation.journal_audit TO authenticated;


--
-- Name: SEQUENCE journal_audit_id_seq; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON SEQUENCE bancarisation.journal_audit_id_seq TO authenticated;
GRANT ALL ON SEQUENCE bancarisation.journal_audit_id_seq TO service_role;


--
-- Name: TABLE ligne_budget; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.ligne_budget TO authenticated;
GRANT ALL ON TABLE bancarisation.ligne_budget TO service_role;


--
-- Name: TABLE membre; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.membre TO authenticated;
GRANT ALL ON TABLE bancarisation.membre TO service_role;


--
-- Name: TABLE membre_organisation; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.membre_organisation TO authenticated;
GRANT ALL ON TABLE bancarisation.membre_organisation TO service_role;


--
-- Name: TABLE note_interne; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.note_interne TO authenticated;
GRANT ALL ON TABLE bancarisation.note_interne TO service_role;


--
-- Name: TABLE occurrence; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.occurrence TO authenticated;
GRANT ALL ON TABLE bancarisation.occurrence TO service_role;


--
-- Name: TABLE occurrence_finance; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.occurrence_finance TO authenticated;
GRANT ALL ON TABLE bancarisation.occurrence_finance TO service_role;


--
-- Name: TABLE occurrence_responsable; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.occurrence_responsable TO authenticated;
GRANT ALL ON TABLE bancarisation.occurrence_responsable TO service_role;


--
-- Name: TABLE organisation_closure; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.organisation_closure TO authenticated;
GRANT ALL ON TABLE bancarisation.organisation_closure TO service_role;


--
-- Name: TABLE organisations; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.organisations TO authenticated;
GRANT ALL ON TABLE bancarisation.organisations TO service_role;


--
-- Name: TABLE parametre_signal; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.parametre_signal TO authenticated;
GRANT ALL ON TABLE bancarisation.parametre_signal TO service_role;


--
-- Name: TABLE parcelles; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.parcelles TO authenticated;
GRANT ALL ON TABLE bancarisation.parcelles TO service_role;


--
-- Name: TABLE personnes; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.personnes TO authenticated;
GRANT ALL ON TABLE bancarisation.personnes TO service_role;


--
-- Name: TABLE plan_cao; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.plan_cao TO authenticated;
GRANT ALL ON TABLE bancarisation.plan_cao TO service_role;


--
-- Name: TABLE plan_cao_calque; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.plan_cao_calque TO authenticated;
GRANT ALL ON TABLE bancarisation.plan_cao_calque TO service_role;


--
-- Name: TABLE plan_cao_entite; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.plan_cao_entite TO authenticated;
GRANT ALL ON TABLE bancarisation.plan_cao_entite TO service_role;


--
-- Name: SEQUENCE plan_cao_entite_id_seq; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON SEQUENCE bancarisation.plan_cao_entite_id_seq TO authenticated;
GRANT ALL ON SEQUENCE bancarisation.plan_cao_entite_id_seq TO service_role;


--
-- Name: TABLE plan_cao_groupe; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.plan_cao_groupe TO authenticated;
GRANT ALL ON TABLE bancarisation.plan_cao_groupe TO service_role;


--
-- Name: TABLE prescription_couverture; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.prescription_couverture TO authenticated;
GRANT ALL ON TABLE bancarisation.prescription_couverture TO service_role;


--
-- Name: TABLE prestataires; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.prestataires TO authenticated;
GRANT ALL ON TABLE bancarisation.prestataires TO service_role;


--
-- Name: TABLE profils; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.profils TO service_role;
GRANT SELECT ON TABLE bancarisation.profils TO authenticated;


--
-- Name: COLUMN profils.nom; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT UPDATE(nom) ON TABLE bancarisation.profils TO authenticated;


--
-- Name: COLUMN profils.prenom; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT UPDATE(prenom) ON TABLE bancarisation.profils TO authenticated;


--
-- Name: TABLE projet_acces; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.projet_acces TO authenticated;
GRANT ALL ON TABLE bancarisation.projet_acces TO service_role;


--
-- Name: TABLE projet_contact; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.projet_contact TO authenticated;
GRANT ALL ON TABLE bancarisation.projet_contact TO service_role;


--
-- Name: TABLE projet_geometries; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.projet_geometries TO authenticated;
GRANT ALL ON TABLE bancarisation.projet_geometries TO service_role;


--
-- Name: TABLE projet_metadata; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.projet_metadata TO authenticated;
GRANT ALL ON TABLE bancarisation.projet_metadata TO service_role;


--
-- Name: TABLE projet_prestataire; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.projet_prestataire TO authenticated;
GRANT ALL ON TABLE bancarisation.projet_prestataire TO service_role;


--
-- Name: TABLE projets; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.projets TO authenticated;
GRANT ALL ON TABLE bancarisation.projets TO service_role;


--
-- Name: TABLE rapport_bilan; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.rapport_bilan TO authenticated;
GRANT ALL ON TABLE bancarisation.rapport_bilan TO service_role;


--
-- Name: TABLE rapport_suivi; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.rapport_suivi TO authenticated;
GRANT ALL ON TABLE bancarisation.rapport_suivi TO service_role;


--
-- Name: TABLE ref_geomce_categorie; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.ref_geomce_categorie TO authenticated;
GRANT ALL ON TABLE bancarisation.ref_geomce_categorie TO service_role;


--
-- Name: TABLE role_contact_ref; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.role_contact_ref TO authenticated;
GRANT ALL ON TABLE bancarisation.role_contact_ref TO service_role;


--
-- Name: TABLE satellite_captures; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.satellite_captures TO authenticated;
GRANT ALL ON TABLE bancarisation.satellite_captures TO service_role;


--
-- Name: TABLE site_agrement; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.site_agrement TO authenticated;
GRANT ALL ON TABLE bancarisation.site_agrement TO service_role;


--
-- Name: SEQUENCE site_agrement_id_seq; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON SEQUENCE bancarisation.site_agrement_id_seq TO authenticated;
GRANT ALL ON SEQUENCE bancarisation.site_agrement_id_seq TO service_role;


--
-- Name: TABLE transfert_projet; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.transfert_projet TO service_role;
GRANT SELECT ON TABLE bancarisation.transfert_projet TO authenticated;


--
-- Name: TABLE ug_lin_parcelles; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.ug_lin_parcelles TO authenticated;
GRANT ALL ON TABLE bancarisation.ug_lin_parcelles TO service_role;


--
-- Name: TABLE ug_pct_parcelles; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.ug_pct_parcelles TO authenticated;
GRANT ALL ON TABLE bancarisation.ug_pct_parcelles TO service_role;


--
-- Name: TABLE ug_surf_parcelles; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.ug_surf_parcelles TO authenticated;
GRANT ALL ON TABLE bancarisation.ug_surf_parcelles TO service_role;


--
-- Name: TABLE unites_de_gestion_lin; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.unites_de_gestion_lin TO authenticated;
GRANT ALL ON TABLE bancarisation.unites_de_gestion_lin TO service_role;


--
-- Name: TABLE unites_de_gestion_pct; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.unites_de_gestion_pct TO authenticated;
GRANT ALL ON TABLE bancarisation.unites_de_gestion_pct TO service_role;


--
-- Name: TABLE unites_de_gestion_surf; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.unites_de_gestion_surf TO authenticated;
GRANT ALL ON TABLE bancarisation.unites_de_gestion_surf TO service_role;


--
-- Name: TABLE v_occurrence_action_fiche; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_occurrence_action_fiche TO authenticated;
GRANT ALL ON TABLE bancarisation.v_occurrence_action_fiche TO service_role;


--
-- Name: TABLE v_action_fiche_conformite; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_action_fiche_conformite TO authenticated;
GRANT ALL ON TABLE bancarisation.v_action_fiche_conformite TO service_role;


--
-- Name: TABLE v_action_portefeuille; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_action_portefeuille TO authenticated;
GRANT ALL ON TABLE bancarisation.v_action_portefeuille TO service_role;


--
-- Name: TABLE v_projet_statut_controle; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_projet_statut_controle TO authenticated;
GRANT ALL ON TABLE bancarisation.v_projet_statut_controle TO service_role;


--
-- Name: TABLE v_projet_statut_effectif; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_projet_statut_effectif TO authenticated;
GRANT ALL ON TABLE bancarisation.v_projet_statut_effectif TO service_role;


--
-- Name: TABLE v_bannette_a_traiter; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_bannette_a_traiter TO authenticated;
GRANT ALL ON TABLE bancarisation.v_bannette_a_traiter TO service_role;


--
-- Name: TABLE v_budget_annuel; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_budget_annuel TO authenticated;
GRANT ALL ON TABLE bancarisation.v_budget_annuel TO service_role;


--
-- Name: TABLE v_budget_delta_annuel; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_budget_delta_annuel TO authenticated;
GRANT ALL ON TABLE bancarisation.v_budget_delta_annuel TO service_role;


--
-- Name: TABLE v_budget_ecarts; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_budget_ecarts TO authenticated;
GRANT ALL ON TABLE bancarisation.v_budget_ecarts TO service_role;


--
-- Name: TABLE v_ug_parcelles; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_ug_parcelles TO authenticated;
GRANT ALL ON TABLE bancarisation.v_ug_parcelles TO service_role;


--
-- Name: TABLE v_contact_projet; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_contact_projet TO authenticated;
GRANT ALL ON TABLE bancarisation.v_contact_projet TO service_role;


--
-- Name: TABLE v_credit_stock; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_credit_stock TO authenticated;
GRANT ALL ON TABLE bancarisation.v_credit_stock TO service_role;


--
-- Name: TABLE v_credit_suivi; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_credit_suivi TO authenticated;
GRANT ALL ON TABLE bancarisation.v_credit_suivi TO service_role;


--
-- Name: TABLE v_fil_interne; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_fil_interne TO authenticated;
GRANT ALL ON TABLE bancarisation.v_fil_interne TO service_role;


--
-- Name: TABLE v_frontend_mesures_projets; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_frontend_mesures_projets TO authenticated;
GRANT ALL ON TABLE bancarisation.v_frontend_mesures_projets TO service_role;


--
-- Name: TABLE v_frontend_mesures_par_departement; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_frontend_mesures_par_departement TO authenticated;
GRANT ALL ON TABLE bancarisation.v_frontend_mesures_par_departement TO service_role;


--
-- Name: TABLE v_mesure_conformite; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_mesure_conformite TO authenticated;
GRANT ALL ON TABLE bancarisation.v_mesure_conformite TO service_role;


--
-- Name: TABLE v_occurrence_calendrier; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_occurrence_calendrier TO authenticated;
GRANT ALL ON TABLE bancarisation.v_occurrence_calendrier TO service_role;


--
-- Name: TABLE v_organisation_arbre; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_organisation_arbre TO authenticated;
GRANT ALL ON TABLE bancarisation.v_organisation_arbre TO service_role;


--
-- Name: TABLE v_parc_bilan_matrice; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_parc_bilan_matrice TO authenticated;
GRANT ALL ON TABLE bancarisation.v_parc_bilan_matrice TO service_role;


--
-- Name: TABLE v_parc_financier_annuel; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_parc_financier_annuel TO authenticated;
GRANT ALL ON TABLE bancarisation.v_parc_financier_annuel TO service_role;


--
-- Name: TABLE v_projet_annee_obligation; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_projet_annee_obligation TO authenticated;
GRANT ALL ON TABLE bancarisation.v_projet_annee_obligation TO service_role;


--
-- Name: TABLE v_parc_signal; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_parc_signal TO authenticated;
GRANT ALL ON TABLE bancarisation.v_parc_signal TO service_role;


--
-- Name: TABLE v_parc_projet; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_parc_projet TO authenticated;
GRANT ALL ON TABLE bancarisation.v_parc_projet TO service_role;


--
-- Name: TABLE v_parc_organisation; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_parc_organisation TO authenticated;
GRANT ALL ON TABLE bancarisation.v_parc_organisation TO service_role;


--
-- Name: TABLE v_portefeuille_organisation; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_portefeuille_organisation TO authenticated;
GRANT ALL ON TABLE bancarisation.v_portefeuille_organisation TO service_role;


--
-- Name: TABLE v_prescription_couverture; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_prescription_couverture TO authenticated;
GRANT ALL ON TABLE bancarisation.v_prescription_couverture TO service_role;


--
-- Name: TABLE v_prochaine_mesure_par_ug; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_prochaine_mesure_par_ug TO authenticated;
GRANT ALL ON TABLE bancarisation.v_prochaine_mesure_par_ug TO service_role;


--
-- Name: TABLE v_projet_activite; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_projet_activite TO authenticated;
GRANT ALL ON TABLE bancarisation.v_projet_activite TO service_role;


--
-- Name: TABLE v_projet_liste_organisation; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_projet_liste_organisation TO authenticated;
GRANT ALL ON TABLE bancarisation.v_projet_liste_organisation TO service_role;


--
-- Name: TABLE v_projet_liste_resume; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_projet_liste_resume TO authenticated;
GRANT ALL ON TABLE bancarisation.v_projet_liste_resume TO service_role;


--
-- Name: TABLE v_projet_prestataires; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_projet_prestataires TO authenticated;
GRANT ALL ON TABLE bancarisation.v_projet_prestataires TO service_role;


--
-- Name: TABLE v_registre_ventes; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_registre_ventes TO authenticated;
GRANT ALL ON TABLE bancarisation.v_registre_ventes TO service_role;


--
-- Name: TABLE v_tresorerie_site; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_tresorerie_site TO authenticated;
GRANT ALL ON TABLE bancarisation.v_tresorerie_site TO service_role;


--
-- Name: TABLE v_ug_lin; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_ug_lin TO authenticated;
GRANT ALL ON TABLE bancarisation.v_ug_lin TO service_role;


--
-- Name: TABLE v_ug_pct; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_ug_pct TO authenticated;
GRANT ALL ON TABLE bancarisation.v_ug_pct TO service_role;


--
-- Name: TABLE v_ug_surf; Type: ACL; Schema: bancarisation; Owner: postgres
--

GRANT ALL ON TABLE bancarisation.v_ug_surf TO authenticated;
GRANT ALL ON TABLE bancarisation.v_ug_surf TO service_role;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: bancarisation; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA bancarisation GRANT ALL ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA bancarisation GRANT ALL ON SEQUENCES TO service_role;


--
-- Name: DEFAULT PRIVILEGES FOR FUNCTIONS; Type: DEFAULT ACL; Schema: bancarisation; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA bancarisation GRANT ALL ON FUNCTIONS TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA bancarisation GRANT ALL ON FUNCTIONS TO service_role;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: bancarisation; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA bancarisation GRANT ALL ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA bancarisation GRANT ALL ON TABLES TO service_role;


--
-- PostgreSQL database dump complete
--

\unrestrict 2bx13sqcgcAkaaeJGaOXUl1DTlWH1We5i401DA53E4ucaxp5d503XmDgLzi9r6x

