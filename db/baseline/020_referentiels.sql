--
-- PostgreSQL database dump
--

\restrict LTnN4umFBvkGSIzwA5y3WB3cYR0mHCxpEgmojT8hzU77WTO3xORmuXrmehgzAgI

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
-- Data for Name: parametre_signal; Type: TABLE DATA; Schema: bancarisation; Owner: postgres
--

SET SESSION AUTHORIZATION DEFAULT;


COPY bancarisation.parametre_signal (code, valeur, libelle, maj_le) FROM stdin;
bilan_manquant_critique	2	Nombre de bilans non remis à partir duquel le signal est critique	2026-07-22 14:33:50.479379+00
retard_critique_ans	2	Ancienneté (années) d'un retard d'exécution le rendant critique	2026-07-22 14:33:50.479379+00
sous_conso_seuil	0.60	Taux d'exécution sur exercices échus en deçà duquel alerter	2026-07-22 14:33:50.479379+00
sous_conso_critique	0.40	Taux d'exécution rendant le signal critique	2026-07-22 14:33:50.479379+00
derive_seuil_pct	0.10	Dérive budgétaire relative à partir de laquelle alerter	2026-07-22 14:33:50.479379+00
derive_critique_pct	0.25	Dérive budgétaire relative rendant le signal critique	2026-07-22 14:33:50.479379+00
silence_mois	12	Absence d'activité (mois) déclenchant le signal	2026-07-22 14:33:50.479379+00
silence_critique_mois	24	Absence d'activité (mois) rendant le signal critique	2026-07-22 14:33:50.479379+00
\.



--
-- Data for Name: ref_geomce_categorie; Type: TABLE DATA; Schema: bancarisation; Owner: postgres
--


COPY bancarisation.ref_geomce_categorie (code, libelle, niveau, parent) FROM stdin;
E	Évitement	1	\N
R	Réduction	1	\N
C	Compensation	1	\N
A	Accompagnement	1	\N
Z	Autre / à préciser	1	\N
C1	Compensation écologique	2	C
C2	Compensation forestière	2	C
C3	Compensation agricole	2	C
R1	Réduction à la source	2	R
E1	Évitement géographique	2	E
A1	Mesure d'accompagnement	2	A
Zz	Autre (niveau 2)	2	Z
C1-1	Création / restauration d'habitats	3	C1
C1-2	Gestion d'habitats	3	C1
C1-3	Continuités écologiques	3	C1
C2-1	Boisement / reboisement	3	C2
R1-1	Adaptation du projet	3	R1
E1-1	Déplacement d'emprise	3	E1
A1-1	Suivi écologique	3	A1
Zzz	Autre (niveau 3)	3	Zz
C1-1-a	Création de milieux ouverts	4	C1-1
C1-1-b	Création / restauration de zones humides	4	C1-1
C1-1-c	Création / restauration de haies / bocage	4	C1-1
C1-2-a	Gestion de milieux ouverts	4	C1-2
C1-2-b	Gestion de zones humides	4	C1-2
C1-3-a	Corridor / trame verte	4	C1-3
C2-1-a	Plantation forestière	4	C2-1
R1-1-a	Réduction d'emprise	4	R1-1
E1-1-a	Évitement d'habitat sensible	4	E1-1
A1-1-a	Suivi faune / flore	4	A1-1
Zzzz	Autre (niveau 4) — à préciser dans GéoMCE	4	Zzz
\.



--
-- Data for Name: role_contact_ref; Type: TABLE DATA; Schema: bancarisation; Owner: postgres
--


COPY bancarisation.role_contact_ref (code, libelle, categorie, ordre) FROM stdin;
maitre_ouvrage	Maître d'ouvrage	maitrise_ouvrage	10
maitre_ouvrage_delegue	Maître d'ouvrage délégué	maitrise_ouvrage	11
charge_mission_moa	Chargé de mission MOA	maitrise_ouvrage	12
operateur_compensation	Opérateur de compensation	operateur	20
charge_projet	Chargé de projet	operateur	21
ecologue_referent	Écologue référent	operateur	22
proprietaire	Propriétaire	foncier	30
preneur_bail	Preneur à bail	foncier	31
gestionnaire_site	Gestionnaire de site	foncier	32
safer	SAFER	foncier	33
notaire	Notaire	foncier	34
prestataire_travaux	Prestataire travaux	prestataire	40
prestataire_suivi	Prestataire de suivi	prestataire	41
bureau_etudes	Bureau d'études	prestataire	42
dreal	DREAL	controle	50
ddt	DDT / DDTM	controle	51
ofb	OFB	controle	52
csrpn	CSRPN	controle	53
commune	Commune	collectivite	60
intercommunalite	Intercommunalité	collectivite	61
departement	Département	collectivite	62
region	Région	collectivite	63
association	Association	tiers	70
riverain	Riverain	tiers	71
autre	Autre	tiers	79
\.



--
-- PostgreSQL database dump complete
--

\unrestrict LTnN4umFBvkGSIzwA5y3WB3cYR0mHCxpEgmojT8hzU77WTO3xORmuXrmehgzAgI

