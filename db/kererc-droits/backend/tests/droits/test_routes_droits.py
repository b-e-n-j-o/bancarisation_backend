"""
Droits sur les routes sensibles : celles où seul le code Python protège
(module admin et accès en `postgres`, fichiers en `service_role`).

On teste surtout les REFUS : un test qui réussit une écriture interdite
nettoie derrière lui puis échoue, pour ne pas laisser la base de recette abîmée.
"""

from __future__ import annotations

import pytest

from .commun import PARTENAIRES, REFUS, ROUTES, SANS_ACCES, assert_refus, sub

# ---------------------------------------------------------------------------
# Sans jeton : rien
# ---------------------------------------------------------------------------


def test_sans_jeton_refuse(config, api, org_projet):
    for chemin in (
        ROUTES["acces"].format(projet=config.projet),
        ROUTES["membres"].format(org=org_projet),
        ROUTES["document_url"].format(document=config.document),
    ):
        r = api(None).get(chemin)
        assert r.status_code in {401, 403}, f"GET {chemin} sans jeton : {r.status_code}"


# ---------------------------------------------------------------------------
# Membres et invitations de l'organisation (module admin, en postgres)
# ---------------------------------------------------------------------------


def test_admin_liste_les_membres(api, org_projet):
    r = api("admin").get(ROUTES["membres"].format(org=org_projet))
    assert r.status_code == 200, r.text[:300]
    assert any(m.get("email") == "admin@roles.test" for m in r.json())


@pytest.mark.parametrize("role", PARTENAIRES + ["intrus"])
def test_hors_organisation_ne_liste_pas_les_membres(api, org_projet, role):
    assert_refus(api(role).get(ROUTES["membres"].format(org=org_projet)))


@pytest.mark.parametrize("role", ["gestionnaire", "contributeur", "lecteur", "sans_projet"] + PARTENAIRES + ["intrus"])
def test_seul_l_admin_invite(api, org_projet, role):
    # .test : aucun e-mail ne part, même si la route laisse passer à tort.
    corps = {"email": f"intrusion-{role.replace('_', '-')}@roles.test", "role": "membre", "portee": "entite"}
    r = api(role).post(ROUTES["invitations"].format(org=org_projet), json=corps)
    if r.is_success:
        _revoquer_invitations(api, org_projet, corps["email"])
    assert_refus(r)


def test_gestionnaire_ne_se_fait_pas_admin_par_invitation(api, org_projet):
    corps = {"email": "gestionnaire@roles.test", "role": "admin", "portee": "entite"}
    r = api("gestionnaire").post(ROUTES["invitations"].format(org=org_projet), json=corps)
    if r.is_success:
        _revoquer_invitations(api, org_projet, corps["email"])
    assert_refus(r, aussi={409})


def _revoquer_invitations(api, org, email):
    liste = api("admin").get(ROUTES["invitations"].format(org=org))
    for inv in liste.json() if liste.is_success else []:
        if inv.get("email") == email and inv.get("statut") != "revoquee":
            api("admin").post(f"/api/invitations/{inv['id']}/revoquer")


# ---------------------------------------------------------------------------
# Accès au projet (lecture des partages, escalade de rôle)
# ---------------------------------------------------------------------------


def test_admin_voit_les_acces_du_projet(config, api):
    r = api("admin").get(ROUTES["acces"].format(projet=config.projet))
    assert r.status_code == 200, r.text[:300]
    assert len(r.json()) >= 8, "les 8 comptes du seed devraient apparaître"


@pytest.mark.parametrize("role", PARTENAIRES + ["contributeur", "lecteur"])
def test_la_liste_des_acces_ne_fuit_pas(config, api, jetons, role):
    """Refus, ou au plus sa propre ligne : jamais la liste de qui d'autre a accès."""
    r = api(role).get(ROUTES["acces"].format(projet=config.projet))
    if r.status_code in REFUS:
        return
    assert r.status_code == 200, r.text[:300]
    moi = sub(jetons(role))
    autres = [a for a in r.json() if a.get("utilisateur_id") not in (moi, None)]
    assert not autres, f"{role} lit les accès d'autres personnes : {[a.get('utilisateur_id') for a in autres]}"


@pytest.mark.parametrize("role", SANS_ACCES)
def test_sans_acces_ne_voit_rien_du_projet(config, api, role):
    assert_refus(api(role).get(ROUTES["acces"].format(projet=config.projet)))
    assert_refus(api(role).get(f"/api/projets/{config.projet}"))


@pytest.mark.parametrize("role", ["contributeur", "lecteur", "sans_projet"] + PARTENAIRES + ["intrus"])
def test_personne_ne_s_accorde_la_gestion(config, api, jetons, role):
    """Escalade : s'ajouter soi-même gestionnaire, avec les finances."""
    corps = {"utilisateur_id": sub(jetons(role)), "niveau": "gestionnaire", "voir_finances": True, "qualite": None}
    r = api(role).post(ROUTES["acces"].format(projet=config.projet), json=corps)
    if r.is_success:
        _retirer_acces_cree(config, api, r)
    assert_refus(r, aussi={409})


@pytest.mark.parametrize("role", ["contributeur", "lecteur"] + PARTENAIRES)
def test_personne_ne_modifie_sa_propre_ligne(config, api, jetons, role):
    """Escalade par PATCH sur sa ligne d'accès. 405 tant que la route PATCH n'existe pas."""
    moi = sub(jetons(role))
    lignes = api("admin").get(ROUTES["acces"].format(projet=config.projet)).json()
    ligne = next((a for a in lignes if a.get("utilisateur_id") == moi), None)
    assert ligne, f"aucune ligne d'accès pour {role} : seed lancé ?"
    chemin = ROUTES["acces_ligne"].format(projet=config.projet, acces=ligne["id"])
    r = api(role).patch(chemin, json={"niveau": "gestionnaire", "voir_finances": True, "qualite": None})
    if r.is_success:
        # Remet la ligne comme avant, puis échoue.
        api("admin").patch(chemin, json={k: ligne.get(k) for k in ("niveau", "voir_finances", "qualite")})
    assert_refus(r, aussi={405})


@pytest.mark.parametrize("role", ["contributeur", "lecteur"] + PARTENAIRES + SANS_ACCES)
def test_personne_ne_retire_l_acces_d_un_autre(config, api, jetons, role):
    cible = "lecteur" if role != "lecteur" else "contributeur"
    lignes = api("admin").get(ROUTES["acces"].format(projet=config.projet)).json()
    ligne = next(a for a in lignes if a.get("utilisateur_id") == sub(jetons(cible)))
    r = api(role).delete(ROUTES["acces_ligne"].format(projet=config.projet, acces=ligne["id"]))
    assert r.status_code not in range(200, 300), (
        f"{role} a retiré l'accès de {cible} : relancer le seed, puis corriger la route"
    )
    assert_refus(r)


def _retirer_acces_cree(config, api, reponse):
    ident = (reponse.json() or {}).get("id") if reponse.content else None
    if ident:
        api("admin").delete(ROUTES["acces_ligne"].format(projet=config.projet, acces=ident))


# ---------------------------------------------------------------------------
# Fichiers (service_role : « voir un fichier, c'est voir la ligne qui le référence »)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", SANS_ACCES)
def test_sans_acces_pas_d_url_signee(config, api, role):
    r = api(role).get(ROUTES["document_url"].format(document=config.document))
    assert r.status_code == 404, f"{role} : attendu 404 (ligne invisible), obtenu {r.status_code}"


@pytest.mark.parametrize("role", ["admin", "contributeur", "lecteur", "operateur", "instructeur", "partenaire"])
def test_lecteur_du_projet_obtient_l_url(config, api, role):
    r = api(role).get(ROUTES["document_url"].format(document=config.document))
    # Le fichier du seed n'existe pas dans le bucket : selon le backend, la signature
    # peut échouer. Ce qui compte ici : pas un refus de droit.
    assert r.status_code != 403, f"{role} refusé sur un document de son projet"


@pytest.mark.parametrize("role", ["lecteur", "instructeur", "partenaire", "moa", "deposant"] + SANS_ACCES)
def test_lecture_seule_ne_depose_pas(config, api, role):
    fichier = {"fichier": ("essai.txt", b"recette", "text/plain")}
    r = api(role).post(ROUTES["document_depot"].format(projet=config.projet), files=fichier, data={"nom": "Essai"})
    if r.is_success:
        ident = (r.json() or {}).get("id")
        if ident:
            api("admin").delete(ROUTES["document"].format(document=ident))
    assert_refus(r)


@pytest.mark.parametrize("role", ["contributeur", "lecteur"] + PARTENAIRES + SANS_ACCES)
def test_seule_la_gestion_supprime_un_document(config, api, role):
    r = api(role).delete(ROUTES["document"].format(document=config.document))
    assert r.status_code not in range(200, 300), (
        f"{role} a supprimé le document de recette : relancer le seed, puis corriger la route"
    )
    assert_refus(r)
