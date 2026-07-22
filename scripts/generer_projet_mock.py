#!/usr/bin/env python3
"""Génère un ou plusieurs projets bancarisation synthétiques (batch).

Chaque dossier :
  - UUID projet distinct
  - référence interne à 4 chiffres unique en base (ex. ``4827``)
  - année de décision tirée dans ``[2000, 2020]``
  - durée 30 ou 50 ans
  - ~60 occurrences (Théma + prestataires catalogue)
  - métadonnées minimales (champs métier vides)
  - pas de documents / géométries

Usage (depuis ``backend/``) ::

    python scripts/generer_projet_mock.py 3
    python scripts/generer_projet_mock.py --projets 5 --nb 60
    python scripts/generer_projet_mock.py 1 --dry-run

Connexion : ``api.db.env.get_database_url`` (``backend/.env``).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from api.db.env import get_database_url  # noqa: E402

ORG_KERELIA = UUID("a1000000-0000-0000-0000-000000000003")
ANNEE_COURANTE = 2026
ANNEE_DECISION_MIN = 2000
ANNEE_DECISION_MAX = 2020
DUREES_ANS = (30, 50)
THEMA_PATH = _BACKEND / "api" / "ocr" / "extractions" / "catalogue" / "thema.json"

UG_IDS = ["ug1", "ug2", "ug3"]

# Seed SQL 009_prestataires.sql
PRESTATAIRES: list[tuple[UUID, str]] = [
    (UUID("b2000000-0000-4000-8000-000000000001"), "Écosphère Conseil"),
    (UUID("b2000000-0000-4000-8000-000000000002"), "Biotope Occitanie"),
    (UUID("b2000000-0000-4000-8000-000000000003"), "Génie Écologique du Midi"),
    (UUID("b2000000-0000-4000-8000-000000000004"), "Atelier des Haies"),
    (UUID("b2000000-0000-4000-8000-000000000005"), "Naturalia Environnement"),
    (UUID("b2000000-0000-4000-8000-000000000006"), "TerrOïko"),
    (UUID("b2000000-0000-4000-8000-000000000007"), "LPO Mission Conseil"),
    (UUID("b2000000-0000-4000-8000-000000000008"), "Sologne Nature Services"),
    (UUID("b2000000-0000-4000-8000-000000000009"), "AquaTerra Restauration"),
    (UUID("b2000000-0000-4000-8000-00000000000a"), "Paysages & Biodiversité SARL"),
    (UUID("b2000000-0000-4000-8000-00000000000b"), "Faune-Flore Expertise"),
    (UUID("b2000000-0000-4000-8000-00000000000c"), "Compenseo Ingénierie"),
]

ACTIONS: list[dict[str, str]] = [
    {
        "code": "MG1",
        "categorie": "MG",
        "titre": "Pilotage et coordination à la mise en œuvre du programme d'actions",
    },
    {
        "code": "MG2",
        "categorie": "MG",
        "titre": "Mises à jour du plan de gestion et bilan de fin de mesure compensatoire",
    },
    {
        "code": "SE1",
        "categorie": "SE",
        "titre": "Suivi des habitats naturels, de la flore et de la faune",
    },
    {
        "code": "TE1",
        "categorie": "TE",
        "titre": (
            "Entretenir les landes humides en faveur du Fadet des laîches "
            "et des oiseaux landicoles"
        ),
    },
    {
        "code": "TU1",
        "categorie": "TU",
        "titre": (
            "Adapter les itinéraires techniques sylvicoles en faveur de "
            "Fadet des laîches et des oiseaux landicoles"
        ),
    },
    {
        "code": "TU2",
        "categorie": "TU",
        "titre": (
            "Créer et maintenir des espaces de landes en faveur du Fadet "
            "des laîches et des oiseaux landicoles"
        ),
    },
    {
        "code": "TU3",
        "categorie": "TU",
        "titre": "Création des zones d'étrépage",
    },
]

POIDS_ACTIONS = {
    "MG1": 6,
    "MG2": 4,
    "SE1": 12,
    "TE1": 12,
    "TU1": 8,
    "TU2": 10,
    "TU3": 8,
}


@dataclass(frozen=True)
class ThemaMesure:
    code: str
    intitule: str


@dataclass(frozen=True)
class ProfilProjet:
    projet_id: UUID
    horizon_debut: int
    duree_ans: int
    reference: str  # 4 chiffres

    @property
    def annee_fin(self) -> int:
        return self.horizon_debut + self.duree_ans - 1

    @property
    def nom(self) -> str:
        """Libellé = UUID projet (différenciation sans noms de sites)."""
        return str(self.projet_id)


def money(value: float | Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def load_thema(path: Path) -> list[ThemaMesure]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[ThemaMesure] = []
    for famille in data.get("familles", []):
        for groupe in famille.get("groupes", []):
            for m in groupe.get("mesures", []):
                code = (m.get("code") or "").strip()
                intitule = (m.get("intitule") or "").strip()
                if code and intitule:
                    out.append(ThemaMesure(code=code, intitule=intitule))
    if not out:
        raise RuntimeError(f"Catalogue Théma vide : {path}")
    return out


def code_affiche(code: str) -> str:
    letters = "".join(c for c in code if c.isalpha())
    digits = "".join(c for c in code if c.isdigit())
    if letters and digits:
        return f"{letters} {digits}"
    return code


def pick_ugs(rng: random.Random) -> list[str]:
    n = rng.choices([1, 2, 3], weights=[0.55, 0.35, 0.10], k=1)[0]
    return sorted(rng.sample(UG_IDS, n))


def fenetre_mois(rng: random.Random) -> tuple[int, int, bool]:
    duree = rng.randint(1, 4)
    if rng.random() < 0.12:
        debut = rng.randint(10, 12)
        fin = duree - (12 - debut + 1)
        fin = max(1, min(4, fin if fin > 0 else duree))
        return debut, fin, True
    debut = rng.randint(1, 12 - duree + 1)
    return debut, debut + duree - 1, False


def choisir_annee(
    rng: random.Random,
    *,
    horizon_debut: int,
    annee_fin: int,
) -> int:
    annees = list(range(horizon_debut, annee_fin + 1))
    poids: list[float] = []
    for a in annees:
        if a <= horizon_debut + 2:
            w = 1.5
        elif a <= ANNEE_COURANTE:
            w = 1.8
        elif a <= ANNEE_COURANTE + 3:
            w = 1.2
        else:
            w = 0.55
        poids.append(w)
    return rng.choices(annees, weights=poids, k=1)[0]


def statut_et_budgets(
    rng: random.Random,
    *,
    annee: int,
    mois_fin: int,
    traverse: bool,
    horizon_debut: int,
) -> dict[str, Any]:
    initial = money(rng.uniform(1800, 42000))
    if rng.random() < 0.35:
        prevu = money(initial * Decimal(str(rng.uniform(0.72, 1.38))))
    else:
        prevu = initial

    annee_initiale = annee
    if rng.random() < 0.12 and annee > horizon_debut:
        annee_initiale = max(horizon_debut, annee - rng.randint(1, 3))

    passe = annee < ANNEE_COURANTE or (
        annee == ANNEE_COURANTE and not traverse and mois_fin <= 3
    )
    futur = annee > ANNEE_COURANTE

    roll = rng.random()
    if passe:
        if roll < 0.08:
            statut = "supprime"
        elif roll < 0.14:
            statut = "repousse"
        elif roll < 0.20:
            statut = "a_confirmer"
        else:
            statut = "realise"
    elif futur:
        if roll < 0.06:
            statut = "supprime"
        elif roll < 0.14:
            statut = "repousse"
        elif roll < 0.28:
            statut = "a_confirmer"
        else:
            statut = "planifie"
    else:
        if roll < 0.06:
            statut = "supprime"
        elif roll < 0.12:
            statut = "repousse"
        elif roll < 0.22:
            statut = "a_confirmer"
        elif roll < 0.48:
            statut = "en_cours"
        elif roll < 0.72:
            statut = "realise"
        else:
            statut = "planifie"

    engage: Decimal | None = None
    realise: Decimal | None = None
    date_realisation: date | None = None
    commentaire: str | None = None

    if statut == "realise":
        engage = money(prevu * Decimal(str(rng.uniform(0.92, 1.05))))
        realise = money(engage * Decimal(str(rng.uniform(0.90, 1.02))))
        mois = mois_fin if not traverse else min(12, mois_fin + 1)
        jour = rng.randint(5, 27)
        try:
            date_realisation = date(annee if not traverse else annee + 1, mois, jour)
        except ValueError:
            date_realisation = date(annee, min(mois, 12), 15)
    elif statut == "en_cours":
        engage = money(prevu * Decimal(str(rng.uniform(0.85, 1.0))))
        if rng.random() < 0.45:
            realise = money(engage * Decimal(str(rng.uniform(0.15, 0.55))))
    elif statut == "repousse":
        commentaire = rng.choice(
            [
                "Report météo / accessibilité chantier",
                "Report suite avenant planning",
                "Report validation maître d'ouvrage",
            ]
        )
        if annee_initiale == annee:
            annee_initiale = max(horizon_debut, annee - rng.randint(1, 2))
    elif statut == "supprime":
        commentaire = rng.choice(
            [
                "Mesure annulée — économie de programme",
                "Doublon fusionné avec une autre occurrence",
                "Abandon suite évolution du plan de gestion",
            ]
        )
        if rng.random() < 0.7:
            prevu = money(0)
    elif statut == "a_confirmer":
        commentaire = "Montant / fenêtre à confirmer avec le prestataire"

    return {
        "statut": statut,
        "montant_initial": initial,
        "annee_initiale": annee_initiale,
        "montant_ht": prevu,
        "montant_engage": engage,
        "montant_realise": realise,
        "date_realisation": date_realisation,
        "commentaire": commentaire,
    }


def thematique_pour_action(
    rng: random.Random,
    action: dict[str, str],
    catalogue: list[ThemaMesure],
) -> ThemaMesure:
    cat = action["categorie"]
    if cat in {"MG", "SE"} and rng.random() < 0.55:
        return ThemaMesure(code="autre", intitule=action["titre"])
    prefs = {
        "TU": ("C1", "C2"),
        "TE": ("C2", "C3"),
        "SE": ("C1", "C2", "C3"),
        "MG": ("C1", "C2", "C3"),
    }.get(cat, ("C1", "C2", "C3"))
    candidats = [m for m in catalogue if m.code.startswith(prefs)]
    return rng.choice(candidats or catalogue)


def construire_occurrences(
    rng: random.Random,
    *,
    nb: int,
    catalogue: list[ThemaMesure],
    horizon_debut: int,
    annee_fin: int,
) -> list[dict[str, Any]]:
    codes = list(POIDS_ACTIONS.keys())
    weights = [POIDS_ACTIONS[c] for c in codes]
    actions_by_code = {a["code"]: a for a in ACTIONS}

    occs: list[dict[str, Any]] = []
    for _ in range(nb):
        action = actions_by_code[rng.choices(codes, weights=weights, k=1)[0]]
        thema = thematique_pour_action(rng, action, catalogue)
        annee = choisir_annee(rng, horizon_debut=horizon_debut, annee_fin=annee_fin)
        mois_debut, mois_fin, traverse = fenetre_mois(rng)
        bud = statut_et_budgets(
            rng,
            annee=annee,
            mois_fin=mois_fin,
            traverse=traverse,
            horizon_debut=horizon_debut,
        )
        annee_exec = annee
        if bud["statut"] == "repousse" and bud["annee_initiale"] == annee:
            annee_exec = min(annee_fin, annee + rng.randint(1, 2))
            bud["annee_initiale"] = annee

        prest_id, prest_nom = rng.choice(PRESTATAIRES)
        titre = thema.intitule if thema.code != "autre" else action["titre"]
        if rng.random() < 0.25 and thema.code != "autre":
            titre = f"{titre} ({action['code']})"

        occs.append(
            {
                "action_code": action["code"],
                "annee": annee_exec,
                "code": code_affiche(action["code"]),
                "titre": titre,
                "categorie": action["categorie"],
                "lib_thema": thema.code,
                "ug_ids": pick_ugs(rng),
                "mois_debut": mois_debut,
                "mois_fin": mois_fin,
                "traverse_nouvel_an": traverse,
                "prestataire_id": prest_id,
                "prestataire": prest_nom,
                "confiance": round(rng.uniform(0.82, 0.99), 3),
                **bud,
            }
        )

    occs.sort(key=lambda o: (o["annee"], o["mois_debut"] or 0, o["code"]))
    return occs


def charger_references_existantes(cur: psycopg.Cursor) -> set[str]:
    cur.execute(
        """
        SELECT reference_interne
        FROM bancarisation.projets
        WHERE reference_interne IS NOT NULL
          AND reference_interne ~ '^[0-9]{4}$'
        """
    )
    return {row["reference_interne"] for row in cur.fetchall()}


def allouer_reference(
    rng: random.Random,
    prises: set[str],
) -> str:
    for _ in range(5000):
        ref = f"{rng.randint(0, 9999):04d}"
        if ref not in prises:
            prises.add(ref)
            return ref
    raise RuntimeError("Impossible d'allouer une référence à 4 chiffres libre.")


def tirer_profil(rng: random.Random, prises: set[str]) -> ProfilProjet:
    debut = rng.randint(ANNEE_DECISION_MIN, ANNEE_DECISION_MAX)
    duree = rng.choice(DUREES_ANS)
    ref = allouer_reference(rng, prises)
    return ProfilProjet(
        projet_id=uuid4(),
        horizon_debut=debut,
        duree_ans=duree,
        reference=ref,
    )


def ensure_prestataires(cur: psycopg.Cursor) -> None:
    for pid, nom_p in PRESTATAIRES:
        cur.execute(
            """
            INSERT INTO bancarisation.prestataires (id, nom)
            VALUES (%s, %s)
            ON CONFLICT (id) DO UPDATE SET nom = EXCLUDED.nom
            """,
            (str(pid), nom_p),
        )


def inserer_projet(
    cur: psycopg.Cursor,
    *,
    occs: list[dict[str, Any]],
    profil: ProfilProjet,
    rng: random.Random,
) -> dict[str, Any]:
    projet_id = profil.projet_id
    mois = rng.randint(1, 12)
    jour = rng.randint(1, 28)

    cur.execute(
        """
        INSERT INTO bancarisation.projets (
            id, organisation_id, nom, reference_interne, commune, departement,
            description, type_procedure, date_decision, duree_annees, statut
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            str(projet_id),
            str(ORG_KERELIA),
            profil.nom,
            profil.reference,
            None,
            None,
            None,
            None,
            date(profil.horizon_debut, mois, jour),
            profil.duree_ans,
            "en_cours",
        ),
    )

    # Métadonnées minimales (champs métier vides)
    cur.execute(
        """
        INSERT INTO bancarisation.projet_metadata (
            projet_id, nom_operation, maitre_ouvrage, operateur, communes,
            arrete_numero, arrete_date, horizon_debut, horizon_fin,
            horizon_duree_ans, metadata_json, confiance,
            champs_a_confirmer, avertissements
        ) VALUES (
            %s, NULL, NULL, NULL, '{}',
            NULL, NULL, %s, %s, %s,
            %s, NULL, '{}', '{}'
        )
        """,
        (
            str(projet_id),
            profil.horizon_debut,
            profil.annee_fin,
            profil.duree_ans,
            Jsonb({"source": "generer_projet_mock", "mock": True, "ref": profil.reference}),
        ),
    )

    echeance_par_action: dict[str, UUID] = {}
    for a in ACTIONS:
        cle = a["code"].lower()
        fiche = {
            "id": cle,
            "code": a["code"],
            "categorie": a["categorie"],
            "titre": a["titre"],
            "lib_thema": "autre",
            "ug_ids": UG_IDS,
            "mock": True,
        }
        cur.execute(
            """
            INSERT INTO bancarisation.action_fiche (
                projet_id, cle, code, categorie, titre, contenu_integral,
                fiche_json, ug_ids, lib_thema, confiance,
                champs_a_confirmer, avertissements
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                str(projet_id),
                cle,
                a["code"],
                a["categorie"],
                a["titre"],
                a["titre"],
                Jsonb(fiche),
                UG_IDS,
                "autre",
                0.9,
                [],
                [],
            ),
        )

        ech_id = uuid4()
        cur.execute(
            """
            INSERT INTO bancarisation.echeance (
                id, projet_id, cle, action_cle, code_operation, type_operation,
                type_metier, libelle, lib_thema, ug_ids, recurrence,
                fenetre_debut, fenetre_fin, confiance,
                champs_a_confirmer, avertissements
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                str(ech_id),
                str(projet_id),
                f"ech-{cle}",
                cle,
                a["code"],
                a["categorie"],
                "mesure",
                a["titre"],
                "autre",
                UG_IDS,
                Jsonb({"type": "pluriannuelle", "commentaire": "mock"}),
                "01",
                "12",
                0.9,
                [],
                [],
            ),
        )
        echeance_par_action[a["code"]] = ech_id

    total_baseline = Decimal("0")
    for o in occs:
        ech_id = echeance_par_action[o["action_code"]]
        cur.execute(
            """
            INSERT INTO bancarisation.occurrence (
                projet_id, echeance_id, annee, code, titre, categorie, lib_thema,
                statut, ug_ids, mois_debut, mois_fin, traverse_nouvel_an,
                origine, confiance, champs_a_confirmer, avertissements,
                date_realisation, commentaire,
                montant_ht, montant_initial, annee_initiale,
                montant_engage, montant_realise,
                prestataire, prestataire_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s
            )
            """,
            (
                str(projet_id),
                str(ech_id),
                o["annee"],
                o["code"],
                o["titre"],
                o["categorie"],
                o["lib_thema"],
                o["statut"],
                o["ug_ids"],
                o["mois_debut"],
                o["mois_fin"],
                o["traverse_nouvel_an"],
                "user",
                o["confiance"],
                [],
                ["occurrence mock"],
                o["date_realisation"],
                o["commentaire"],
                o["montant_ht"],
                o["montant_initial"],
                o["annee_initiale"],
                o["montant_engage"],
                o["montant_realise"],
                o["prestataire"],
                str(o["prestataire_id"]),
            ),
        )
        total_baseline += o["montant_initial"]

    cur.execute(
        """
        INSERT INTO bancarisation.budget_baseline (
            projet_id, libelle, commentaire, mode, nb_occurrences, total_ht
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            str(projet_id),
            "Baseline mock initiale",
            f"Générée pour ref={profil.reference}",
            "completer",
            len(occs),
            total_baseline,
        ),
    )

    used = {o["prestataire_id"] for o in occs}
    for pid in used:
        cur.execute(
            """
            INSERT INTO bancarisation.projet_prestataire (projet_id, prestataire_id, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (projet_id, prestataire_id) DO NOTHING
            """,
            (str(projet_id), str(pid), "intervenant"),
        )

    return {
        "projet_id": str(projet_id),
        "reference": profil.reference,
        "horizon": f"{profil.horizon_debut}–{profil.annee_fin}",
        "duree_ans": profil.duree_ans,
        "nb_occurrences": len(occs),
        "total_baseline_ht": float(total_baseline),
    }


def stats_statuts(occs: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for o in occs:
        out[o["statut"]] = out.get(o["statut"], 0) + 1
    return dict(sorted(out.items()))


def main() -> None:
    p = argparse.ArgumentParser(
        description="Génère N projets mock bancarisation (batch)."
    )
    p.add_argument(
        "projets",
        nargs="?",
        type=int,
        default=None,
        help="Nombre de dossiers à générer",
    )
    p.add_argument(
        "--projets",
        dest="projets_flag",
        type=int,
        default=None,
        help="Nombre de dossiers (alternative à l'argument positionnel)",
    )
    p.add_argument(
        "--nb",
        type=int,
        default=60,
        help="Occurrences par dossier (défaut 60)",
    )
    p.add_argument("--seed", type=int, default=None, help="Graine RNG (reproductible)")
    p.add_argument("--dry-run", action="store_true", help="Sans écriture en base")
    p.add_argument("--thema", type=Path, default=THEMA_PATH)
    args = p.parse_args()

    n_projets = args.projets_flag if args.projets_flag is not None else args.projets
    if n_projets is None:
        n_projets = 1
    if n_projets < 1:
        raise SystemExit("Le nombre de projets doit être ≥ 1.")

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    rng = random.Random(seed)
    catalogue = load_thema(args.thema)

    print(
        f"🎲 seed={seed}  dossiers={n_projets}  occ/dossier={args.nb}  "
        f"thema={len(catalogue)} mesures"
    )

    if args.dry_run:
        prises: set[str] = set()
        apercus = []
        for i in range(n_projets):
            profil = tirer_profil(rng, prises)
            occs = construire_occurrences(
                rng,
                nb=args.nb,
                catalogue=catalogue,
                horizon_debut=profil.horizon_debut,
                annee_fin=profil.annee_fin,
            )
            apercus.append(
                {
                    "i": i + 1,
                    "projet_id": str(profil.projet_id),
                    "reference": profil.reference,
                    "horizon": f"{profil.horizon_debut}–{profil.annee_fin}",
                    "duree_ans": profil.duree_ans,
                    "statuts": stats_statuts(occs),
                }
            )
        print("\n🔍 dry-run — rien écrit en base.")
        print(json.dumps(apercus, ensure_ascii=False, indent=2))
        return

    url = get_database_url()
    recaps: list[dict[str, Any]] = []
    with psycopg.connect(url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            ensure_prestataires(cur)
            prises = charger_references_existantes(cur)

            for i in range(n_projets):
                profil = tirer_profil(rng, prises)
                occs = construire_occurrences(
                    rng,
                    nb=args.nb,
                    catalogue=catalogue,
                    horizon_debut=profil.horizon_debut,
                    annee_fin=profil.annee_fin,
                )
                recap = inserer_projet(cur, occs=occs, profil=profil, rng=rng)
                recaps.append(recap)
                print(
                    f"  [{i + 1}/{n_projets}] ref={recap['reference']}  "
                    f"{recap['horizon']} ({recap['duree_ans']} ans)  "
                    f"{recap['projet_id']}"
                )

        conn.commit()

    print(f"\n✅ {len(recaps)} projet(s) mock créé(s) :")
    for r in recaps:
        print(
            f"   · ref={r['reference']}  id={r['projet_id']}\n"
            f"     horizon={r['horizon']}  "
            f"occ={r['nb_occurrences']}  baseline={r['total_baseline_ht']:.0f} €"
        )


if __name__ == "__main__":
    main()
