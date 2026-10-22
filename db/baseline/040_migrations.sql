--
-- PostgreSQL database dump
--

\restrict M1qBadbdV7FUqpATiCGBb8KqJRiwZzEvY1irkqKzCWw8TSOrdHTMuaf8UD1NNIN

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

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.schema_migrations (
    version text NOT NULL,
    appliquee_le timestamp with time zone DEFAULT now()
);


ALTER TABLE public.schema_migrations OWNER TO postgres;

--
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.schema_migrations (version, appliquee_le) FROM stdin;
030z_vue_action_portefeuille_invoker	2026-10-07 15:21:22.68501+00
054_contexte_droits	2026-10-07 15:28:24.778622+00
055_occurrence_finance	2026-10-08 06:59:05.75045+00
056_occurrence_drop_montants	2026-10-08 07:10:29.772908+00
058_mes_droits_qualite_interne	2026-10-09 08:28:05.933442+00
067_anon_privileges_defaut	2026-10-21 13:15:41.508129+00
068_export_projet	2026-10-21 14:09:38.011517+00
069_export_transfert	2026-10-21 14:59:48.168177+00
070_export_droits_backend	2026-10-21 15:19:57.491039+00
071_roles_matrice	2026-10-22 11:40:55.094868+00
063_statut_archive	2026-10-22 12:35:47.58976+00
\.


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: schema_migrations; Type: ROW SECURITY; Schema: public; Owner: postgres
--

ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY;

--
-- Name: TABLE schema_migrations; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.schema_migrations TO service_role;


--
-- PostgreSQL database dump complete
--

\unrestrict M1qBadbdV7FUqpATiCGBb8KqJRiwZzEvY1irkqKzCWw8TSOrdHTMuaf8UD1NNIN

insert into public.schema_migrations (version) values ('baseline_20261022') on conflict do nothing;
