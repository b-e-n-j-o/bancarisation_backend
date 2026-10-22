"""Classeur validé → JeuDonnees, en passant par le générateur d'occurrences existant."""

from __future__ import annotations

import uuid
from calendar import monthrange
from datetime import date

from api.ocr.calcul_occurrences import generer
from api.ocr.models import Echeance, FenetreIntervention, Recurrence, TypeRecurrence
from api.reimport.jeu_donnees import JeuDonnees
from api.reimport.modele.constats import Constat
from api.reimport.modele.listes import DISPOSITIFS, ROLES
from api.reimport.modele.normaliser import (
    annee,
    code,
    code_ug,
    date_fr,
    liste_codes,
    mois,
    nombre,
    recurrence as regle_de,
    statut,
    texte,
    type_ug,
)

_ROLES = {lib.lower(): cod for cod, lib in ROLES}
_ROLES.update({cod: cod for cod, _lib in ROLES})
_DISPO = {lib.lower(): cod for cod, lib in DISPOSITIFS}
_DISPO.update({cod: cod for cod, _lib in DISPOSITIFS})


def construire(donnees: dict, organisation_id: str, import_id: str, geometries: dict[str, dict], figer: bool) -> tuple[JeuDonnees, list[Constat], dict]:
    constats: list[Constat] = []
    projet_id = str(uuid.uuid4())
    annee_n = annee(donnees["projet"].get("Année de début (année N)")) or date.today().year
    duree = int(nombre(donnees["projet"].get("Durée (années)")) or 1)
    annee_fin = annee_n + duree - 1
    decision = date_fr(donnees["projet"].get("Date de la décision"))

    mesures = {}
    for ligne in donnees["mesures"]:
        mesures[code(ligne.get("Code"))] = ligne
    ugs = {}
    for ligne in donnees["ug"]:
        ident = code_ug(ligne.get("Code"))
        if ident:
            ugs[ident] = ligne

    projet = {
        "id": projet_id,
        "organisation_id": organisation_id,
        "nom": texte(donnees["projet"].get("Nom")),
        "commune": texte(donnees["projet"].get("Commune")),
        "departement": texte(donnees["projet"].get("Département")) or None,
        "reference_interne": texte(donnees["projet"].get("Référence interne")) or None,
        "type_procedure": texte(donnees["projet"].get("Procédure")) or None,
        "type_dispositif": _DISPO.get(texte(donnees["projet"].get("Type de dispositif")).lower(), "obligation"),
        "reference_decision": texte(donnees["projet"].get("Référence de la décision")) or None,
        "date_decision": decision.isoformat() if decision else None,
        "reference_ei": texte(donnees["projet"].get("Référence étude d'impact")) or None,
        "duree_annees": duree,
        "description": texte(donnees["projet"].get("Description")) or None,
        "origine_import": "modele",
        "import_id": import_id,
    }
    meta = {
        "id": str(uuid.uuid4()),
        "projet_id": projet_id,
        "horizon_debut": annee_n,
        "horizon_fin": annee_fin,
        "horizon_duree_ans": duree,
        "metadata_json": {"source": "modele"},
    }

    fiches = []
    for cle, ligne in mesures.items():
        ug_ids = liste_codes(ligne.get("UG concernées"), ug=True)
        fiches.append({
            "id": str(uuid.uuid4()),
            "projet_id": projet_id,
            "cle": cle,
            "code": cle,
            "categorie": _categorie(cle),
            "titre": texte(ligne.get("Libellé")),
            "contenu_integral": texte(ligne.get("Libellé")),
            "fiche_json": {},
            "confiance": 1,
            "ug_ids": _pg_array(ug_ids),
            "lib_thema": texte(ligne.get("Catégorie Théma")) or "autre",
        })

    echeances_py, plan_par_ref, ids_echeance = _echeances(donnees, mesures, annee_n, annee_fin, constats)
    occurrences_gen, non_placables = generer(echeances_py, annee_fin=annee_fin)
    for e in non_placables:
        constats.append(Constat("avertissement", "Actions – plan", None, "Réf. action", f"{e.code_operation} n'a produit aucune occurrence."))

    fusion = {}
    for ligne in donnees["calendrier"]:
        ref = code(ligne.get("Réf. action"))
        an = annee(ligne.get("Année"))
        if ref and an:
            fusion.setdefault((ref, an), []).append(ligne)

    remplacees = 0
    generees = 0
    depuis_cal = 0
    occurrences = []
    finances = []
    vues = []
    for occ in occurrences_gen:
        ref = code(occ.echeance_cle)
        clef = (ref, occ.annee)
        if clef in fusion:
            remplacees += 1
            continue
        generees += 1
        plan = plan_par_ref[ref]
        statut_occ = _statut_genere(occ.annee, occ.mois_fin)
        _ajouter_occurrence(
            occurrences, finances, vues, projet_id, ids_echeance[ref], occ, plan, statut_occ, None,
        )

    for (ref, an), lignes in fusion.items():
        plan = plan_par_ref[ref]
        for ligne in lignes:
            depuis_cal += 1
            _ajouter_calendrier(occurrences, finances, vues, projet_id, ids_echeance[ref], plan, ligne, constats)

    echeances_libres = []
    for ligne in donnees["calendrier"]:
        if code(ligne.get("Réf. action")):
            continue
        depuis_cal += 1
        echeance = _echeance_libre(projet_id, mesures, ligne)
        echeances_libres.append(echeance)
        _ajouter_libre(occurrences, finances, vues, projet_id, mesures, ligne, echeance["id"])

    personnes, contacts = _contacts(donnees, projet_id, organisation_id, constats)
    unites, emprise = _geometries(ugs, geometries, projet_id, constats)
    _avertissements_metier(donnees, mesures, ugs, annee_n, annee_fin, constats)

    tables = {
        "projets": [projet],
        "projet_metadata": [meta],
        "action_fiche": fiches,
        "echeance": _lignes_echeance(echeances_py, ids_echeance, projet_id, plan_par_ref) + echeances_libres,
        "occurrence": occurrences,
        "occurrence_finance": [f for f in finances if f],
        "personnes": personnes,
        "projet_contact": contacts,
    }
    for kind, rows in unites.items():
        tables[f"unites_de_gestion_{kind}"] = rows
    if emprise:
        tables["emprise_projet"] = emprise

    if figer and any(f.get("montant_ht") for f in finances):
        total = sum(float(f.get("montant_ht") or 0) for f in finances)
        tables["budget_baseline"] = [{
            "id": str(uuid.uuid4()),
            "projet_id": projet_id,
            "libelle": "Budget déclaré à l'import",
            "mode": "completer",
            "nb_occurrences": len(occurrences),
            "total_ht": total,
        }]

    resume = {
        "mesures": len(fiches),
        "ug": len(ugs),
        "ug_sans_geometrie": len(ugs) - sum(len(v) for v in unites.values()),
        "echeances": len(echeances_py) + len(echeances_libres),
        "occurrences_generees": generees,
        "occurrences_calendrier": depuis_cal,
        "occurrences_remplacees": remplacees,
        "contacts": len(contacts),
        "annee_debut": annee_n,
        "annee_fin": annee_fin,
        "total_prevu": sum(float(f.get("montant_ht") or 0) for f in finances if f),
        "total_engage": sum(float(f.get("montant_engage") or 0) for f in finances if f),
        "total_realise": sum(float(f.get("montant_realise") or 0) for f in finances if f),
        "frise": _frise(vues),
    }
    jeu = JeuDonnees(tables=tables, conserver_ids=True, source="modele", meta=resume)
    return jeu, constats, resume


def _echeances(donnees, mesures, annee_n, annee_fin, constats):
    objets = []
    plan = {}
    ids = {}
    for ligne in donnees["plan"]:
        ref = code(ligne.get("Réf. action"))
        if not ref:
            continue
        ids[ref] = str(uuid.uuid4())
        plan[ref] = ligne
        debut = annee(ligne.get("Première année")) or annee_n
        fin = annee(ligne.get("Dernière année")) or annee_fin
        regle = regle_de(ligne.get("Récurrence")) or "annuelle"
        if regle == "ponctuelle":
            recurrence = Recurrence(type=TypeRecurrence.ponctuel, ancrage_annee=annee(ligne.get("Années")) or debut, annee_fin=annee(ligne.get("Années")) or debut)
        elif regle == "tous_les_n":
            recurrence = Recurrence(type=TypeRecurrence.periodique, intervalle_ans=int(nombre(ligne.get("N")) or 1), ancrage_annee=debut, annee_fin=fin)
        elif regle == "annees":
            annees = [int(a) for a in liste_codes(ligne.get("Années")) if annee(a)]
            recurrence = Recurrence(type=TypeRecurrence.explicite, ancrage_annee=min(annees) if annees else debut, annee_fin=fin, annees=annees)
        else:
            recurrence = Recurrence(type=TypeRecurrence.periodique, intervalle_ans=1, ancrage_annee=debut, annee_fin=fin)
        md, mf = mois(ligne.get("Mois de début")) or 1, mois(ligne.get("Mois de fin")) or 12
        if mf < md:
            constats.append(Constat(
                "avertissement", "Actions – plan", ligne["_ligne"], "Mois de fin",
                "La fenêtre passe le nouvel an. Elle est enregistrée ainsi.",
            ))
        mesure = mesures.get(code(ligne.get("Mesure")), {})
        objets.append(Echeance(
            id=ref,
            code_operation=ref,
            type_operation=_categorie(code(ligne.get("Mesure"))),
            type_metier="autre",
            libelle=texte(ligne.get("Libellé")),
            lib_thema=texte(mesure.get("Catégorie Théma")) or "autre",
            ug_ids=liste_codes(ligne.get("UG"), ug=True),
            recurrence=recurrence,
            fenetre_intervention=FenetreIntervention(
                debut=f"{md:02d}-01",
                fin=f"{mf:02d}-01",
                traverse_nouvel_an=mf < md,
            ),
            confiance=1,
        ))
    return objets, plan, ids


def _lignes_echeance(objets, ids, projet_id, plan):
    lignes = []
    for e in objets:
        lignes.append({
            "id": ids[e.id],
            "projet_id": projet_id,
            "cle": e.id,
            "action_cle": code(plan[e.id].get("Mesure")),
            "code_operation": e.code_operation,
            "type_operation": e.type_operation or "MG",
            "type_metier": "autre",
            "libelle": e.libelle,
            "ug_ids": _pg_array(e.ug_ids),
            "recurrence": e.recurrence.model_dump(mode="json"),
            "fenetre_debut": e.fenetre_intervention.debut,
            "fenetre_fin": e.fenetre_intervention.fin,
            "fenetre_traverse_nouvel_an": e.fenetre_intervention.traverse_nouvel_an,
            "confiance": 1,
            "lib_thema": e.lib_thema,
        })
    return lignes


def _ajouter_occurrence(occurrences, finances, vues, projet_id, echeance_id, occ, plan, statut_occ, ligne):
    oid = str(uuid.uuid4())
    prevu = nombre(plan.get("Coût unitaire HT"))
    occurrences.append({
        "id": oid,
        "projet_id": projet_id,
        "echeance_id": echeance_id,
        "annee": occ.annee,
        "code": occ.code,
        "titre": occ.titre,
        "categorie": occ.categorie or "MG",
        "statut": statut_occ,
        "ug_ids": _pg_array(list(occ.ug_ids)),
        "mois_debut": occ.mois_debut,
        "mois_fin": occ.mois_fin,
        "traverse_nouvel_an": occ.traverse_nouvel_an,
        "origine": "user",
        "prestataire": texte(plan.get("Prestataire")) or None,
        "lib_thema": occ.lib_thema,
    })
    finances.append(_finance(oid, projet_id, prevu, None, None, occ.annee))
    vues.append({"annee": occ.annee, "statut": statut_occ})


def _ajouter_calendrier(occurrences, finances, vues, projet_id, echeance_id, plan, ligne, constats):
    oid = str(uuid.uuid4())
    an = annee(ligne.get("Année"))
    md = mois(ligne.get("Mois de début")) or mois(plan.get("Mois de début"))
    mf = mois(ligne.get("Mois de fin")) or mois(plan.get("Mois de fin"))
    st = statut(ligne.get("Statut")) or "planifie"
    realisee = date_fr(ligne.get("Date de réalisation"))
    if st == "realise" and realisee is None:
        constats.append(Constat("avertissement", "Actions – calendrier", ligne["_ligne"], "Date de réalisation", "Statut réalisée sans date."))
    if an and an > date.today().year and st == "realise":
        constats.append(Constat("avertissement", "Actions – calendrier", ligne["_ligne"], "Statut", "Une action future est marquée réalisée."))
    prevu = nombre(ligne.get("Montant prévu HT"))
    if prevu is None:
        prevu = nombre(plan.get("Coût unitaire HT"))
    engage = nombre(ligne.get("Montant engagé"))
    realise = nombre(ligne.get("Montant réalisé"))
    if prevu is not None and ((engage or 0) > prevu or (realise or 0) > prevu):
        constats.append(Constat("avertissement", "Actions – calendrier", ligne["_ligne"], "Montant engagé", "Engagé ou réalisé supérieur au prévu."))
    ugs = liste_codes(ligne.get("UG"), ug=True) or liste_codes(plan.get("UG"), ug=True)
    occurrences.append({
        "id": oid,
        "projet_id": projet_id,
        "echeance_id": echeance_id,
        "annee": an,
        "code": code(plan.get("Réf. action")),
        "titre": texte(ligne.get("Libellé")) or texte(plan.get("Libellé")),
        "categorie": _categorie(code(plan.get("Mesure"))),
        "statut": st,
        "ug_ids": _pg_array(ugs),
        "mois_debut": md,
        "mois_fin": mf,
        "traverse_nouvel_an": bool(md and mf and mf < md),
        "origine": "user",
        "date_realisation": realisee.isoformat() if realisee else None,
        "commentaire": texte(ligne.get("Commentaire")) or None,
        "prestataire": texte(ligne.get("Prestataire")) or texte(plan.get("Prestataire")) or None,
        "lib_thema": "autre",
    })
    finances.append(_finance(oid, projet_id, prevu, engage, realise, an))
    vues.append({"annee": an, "statut": st})


def _echeance_libre(projet_id: str, mesures: dict, ligne: dict) -> dict:
    """Une échéance ponctuelle rattache l'action libre à sa mesure."""
    mesure = code(ligne.get("Mesure"))
    an = annee(ligne.get("Année"))
    md = mois(ligne.get("Mois de début")) or 1
    mf = mois(ligne.get("Mois de fin")) or md
    fiche = mesures.get(mesure, {})
    return {
        "id": str(uuid.uuid4()),
        "projet_id": projet_id,
        "cle": f"libre-{uuid.uuid4()}",
        "action_cle": mesure,
        "code_operation": mesure,
        "type_operation": _categorie(mesure),
        "type_metier": "autre",
        "libelle": texte(ligne.get("Libellé")),
        "ug_ids": _pg_array(liste_codes(ligne.get("UG"), ug=True)),
        "recurrence": {"type": "ponctuel", "ancrage_annee": an, "annee_fin": an},
        "fenetre_debut": f"{md:02d}-01",
        "fenetre_fin": f"{mf:02d}-01",
        "fenetre_traverse_nouvel_an": bool(mf < md),
        "confiance": 1,
        "lib_thema": texte(fiche.get("Catégorie Théma")) or "autre",
    }


def _ajouter_libre(occurrences, finances, vues, projet_id, mesures, ligne, echeance_id: str):
    oid = str(uuid.uuid4())
    an = annee(ligne.get("Année"))
    st = statut(ligne.get("Statut")) or "planifie"
    occurrences.append({
        "id": oid,
        "projet_id": projet_id,
        "echeance_id": echeance_id,
        "annee": an,
        "code": code(ligne.get("Mesure")),
        "titre": texte(ligne.get("Libellé")),
        "categorie": _categorie(code(ligne.get("Mesure"))),
        "statut": st,
        "ug_ids": _pg_array(liste_codes(ligne.get("UG"), ug=True)),
        "mois_debut": mois(ligne.get("Mois de début")),
        "mois_fin": mois(ligne.get("Mois de fin")),
        "origine": "user",
        "prestataire": texte(ligne.get("Prestataire")) or None,
        "lib_thema": texte(mesures.get(code(ligne.get("Mesure")), {}).get("Catégorie Théma")) or "autre",
    })
    finances.append(_finance(
        oid, projet_id,
        nombre(ligne.get("Montant prévu HT")),
        nombre(ligne.get("Montant engagé")),
        nombre(ligne.get("Montant réalisé")),
        an,
    ))
    vues.append({"annee": an, "statut": st})


def _finance(oid, projet_id, prevu, engage, realise, an):
    if prevu is None and engage is None and realise is None:
        return None
    return {
        "occurrence_id": oid,
        "projet_id": projet_id,
        "montant_ht": prevu,
        "montant_engage": engage,
        "montant_realise": realise,
        "montant_initial": prevu,
        "annee_initiale": an,
    }


def _contacts(donnees, projet_id, organisation_id, constats):
    personnes, liens = [], []
    for ligne in donnees.get("contacts") or []:
        pid = str(uuid.uuid4())
        role = _ROLES.get(texte(ligne.get("Rôle")).lower())
        if role is None:
            constats.append(Constat("erreur", "Contacts", ligne["_ligne"], "Rôle", "Rôle inconnu du référentiel."))
            continue
        personnes.append({
            "id": pid,
            "organisation_id": organisation_id,
            "type_personne": "physique",
            "nom": texte(ligne.get("Nom")),
            "email": texte(ligne.get("Email")) or None,
            "telephone": texte(ligne.get("Téléphone")) or None,
            "notes": texte(ligne.get("Organisation")) or None,
        })
        liens.append({
            "id": str(uuid.uuid4()),
            "projet_id": projet_id,
            "personne_id": pid,
            "role_code": role,
            "partage_partenaires": False,
        })
    return personnes, liens


def _geometries(ugs, geometries, projet_id, constats):
    unites: dict[str, list] = {"surf": [], "lin": [], "pct": []}
    emprise = []
    for code_u, ligne in ugs.items():
        geom = geometries.get(code_u)
        kind = type_ug(ligne.get("Type")) or "surf"
        if not geom:
            constats.append(Constat(
                "avertissement", "UG", ligne["_ligne"], "Code",
                f"{code_u} n'a pas de géométrie : le code est conservé sur les mesures et les actions, "
                "mais l'unité n'apparaîtra pas sur la carte. La table des UG exige une géométrie.",
            ))
            continue
        if geom["kind"] != kind and geom["kind"] != "emprise":
            constats.append(Constat(
                "erreur", "SIG", None, code_u,
                f"Le type SIG ({geom['kind']}) ne correspond pas au type de l'onglet UG ({kind}).",
            ))
            continue
        unites[kind].append({
            "id": str(uuid.uuid4()),
            "projet_id": projet_id,
            "ug_id": code_u,
            "libelle": texte(ligne.get("Libellé")),
            "description": "",
            "geom_wkt": geom["wkt"],
            "origine": "user",
            "source_fichier": geom.get("source"),
        })
    if "emprise" in geometries:
        emprise.append({
            "id": str(uuid.uuid4()),
            "projet_id": projet_id,
            "libelle": "Emprise projet",
            "description": "",
            "geom_wkt": geometries["emprise"]["wkt"],
            "source_fichier": geometries["emprise"].get("source"),
        })
    return unites, emprise


def _avertissements_metier(donnees, mesures, ugs, annee_n, annee_fin, constats):
    mesures_citees = {code(l.get("Mesure")) for l in donnees["plan"]} | {code(l.get("Mesure")) for l in donnees["calendrier"]}
    for cle in mesures:
        if cle not in mesures_citees:
            constats.append(Constat("avertissement", "Mesures", None, "Code", f"{cle} n'est citée par aucune action."))
    ug_citees = set()
    for ligne in donnees["plan"] + donnees["mesures"]:
        ug_citees.update(liste_codes(ligne.get("UG") or ligne.get("UG concernées"), ug=True))
    for cle in ugs:
        if cle not in ug_citees:
            constats.append(Constat("avertissement", "UG", None, "Code", f"{cle} n'est citée par aucune mesure."))
    for ligne in donnees["plan"] + donnees["calendrier"]:
        an = annee(ligne.get("Année") or ligne.get("Première année") or ligne.get("Années"))
        if an and not (annee_n <= an <= annee_fin):
            onglet = "Actions – calendrier" if "Année" in ligne and ligne.get("Année") else "Actions – plan"
            constats.append(Constat("avertissement", onglet, ligne.get("_ligne"), "Année", f"L'année {an} est hors de la durée du projet."))


def _statut_genere(an: int, mois_fin: int | None) -> str:
    mois = mois_fin or 12
    fin = date(an, mois, monthrange(an, mois)[1])
    return "a_confirmer" if fin < date.today() else "planifie"


def _categorie(code_mesure: str) -> str:
    prefixe = "".join(c for c in code_mesure if c.isalpha())[:2]
    return prefixe if prefixe in {"EP", "TU", "TE", "SE", "MG"} else "MG"


def _pg_array(valeurs: list[str]) -> str:
    return "{" + ",".join(valeurs) + "}"


def _frise(vues: list[dict]) -> list[dict]:
    par_annee: dict[int, dict[str, int]] = {}
    for vue in vues:
        if not vue.get("annee"):
            continue
        seau = par_annee.setdefault(int(vue["annee"]), {})
        seau[vue["statut"]] = seau.get(vue["statut"], 0) + 1
    return [{"annee": an, "statuts": seau} for an, seau in sorted(par_annee.items())]
