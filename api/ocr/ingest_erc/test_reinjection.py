from ingestion.modeles import Fait, Source, ZoneCandidate, Document, PageTexte
from ingestion.reconcile_ref import reconcilier
from ingestion.reinjection import faits_depuis_validation, fusionner_faits_user, verrouiller, QuestionsOuvertes

S = lambda d, l: Source(doc=d, loc=l)
faits = [
  Fait(type="ancre_temporelle", cle="projet", valeur={"etat_zero": 2022, "N": 2023}, source=S("arrete.pdf","p.16")),
  Fait(type="ancre_temporelle", cle="projet", valeur={"etat_zero": 2022}, source=S("plan.pdf","p.9")),
  Fait(type="ancre_temporelle", cle="projet", valeur={"etat_zero": 2023}, source=S("ITK.xlsx","F!L2")),
  Fait(type="coef_totaux", cle="projet", valeur={"k": 1.1, "scores": {1.1: 61}, "nb_totaux": 175}, source=S("ITK.xlsx","F")),
  Fait(type="action_intitule", cle="SE2", valeur="Suivi oiseaux", source=S("plan.pdf","p.40")),
  Fait(type="action_ug", cle="SE2", valeur=[""], source=S("plan.pdf","p.40")),
  Fait(type="action_ug", cle="SE2", valeur=["UG1","UG5"], source=S("ITK.xlsx","Synthèse!H15")),
  Fait(type="action_ug", cle="TU1", valeur=["UG1"], source=S("ITK.xlsx","Synthèse!H3")),
  Fait(type="action_ug", cle="TU1", valeur=["UG1"], source=S("plan.pdf","p.30")),
  Fait(type="ug_libelle", cle="UG5", valeur="Lande fauvette", source=S("plan.pdf","p.12")),
]
zones = [ZoneCandidate(id="Compensation_Fauvette", couche="Compensation_Fauvette", nb_entites=1, surface_ha=2.0, type_erc_nom="C"),
         ZoneCandidate(id="Compensation_Fadet::Action=UG 1", couche="Compensation_Fadet", filtre={"Action":"UG 1"}, ug_attribut="UG1", nb_entites=2, surface_ha=13.7, type_erc_nom="C")]
docs = [Document(nom="plan.pdf", chemin="", sha256="", extension="pdf", role="plan_gestion", pages=[PageTexte(num=1, texte="plan de gestion lycee", nb_mots=4)])]

r1 = reconcilier(faits, zones, [], docs)
print("TOUR 1", [(q.id, q.options, q.proposition) for q in r1.questions])
print("SE2 ugs", r1.actions[0].code, r1.actions[0].ugs.valeur, r1.actions[0].ugs.confiance)
rep = {q.id: q.proposition or (q.options[0] if q.options else "UG1") for q in r1.questions}
rep["ancre_etat_zero:projet"] = "Le tableau ITK de 2023 est une version antérieure, l'état zéro terrain a été fait fin 2022"
corr = {"projet": {}, "ugs": [{"id": "UG5", "origine": "ia", "supprime": False, "valeurs": {"code": "UG5b"}}], "actions": []}
fu = fusionner_faits_user([], faits_depuis_validation(rep, corr, r1.questions))
r2 = reconcilier(faits + fu, zones, [], docs)
print("TOUR 2 questions", [q.id for q in r2.questions])
print("projet", {k: (c.valeur, c.confiance) for k, c in r2.projet.items()})
print("ugs", [(u.ug_code, u.zone_sig.valeur, u.zone_sig.confiance) for u in r2.ugs])
print("actions", [(a.code, a.ugs.valeur, a.ugs.confiance) for a in r2.actions])
print("commentaires", [(c["portee"], c["texte"][:50]) for c in r2.decisions["commentaires"]])
ref = verrouiller(r2)
print("verrou", ref.empreinte[:12], ref.projet["annee_N"], ref.provenance["projet.annee_etat_zero"]["origine"])