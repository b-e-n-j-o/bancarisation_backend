# Analyse détaillée des DXF (extraction enrichie)

Complément de [`GEOREFERENCEMENT.md`](./GEOREFERENCEMENT.md), qui décrit le **calage**
(similitude vers Lambert-93). Ici : **ce qu’on extrait du fichier CAO**, comment
c’est plus profond que la version du 7 septembre 2026, et **ce que l’onglet CAO
affiche** désormais.

Date de cette note : 8 septembre 2026.  
Fichier de référence testé : plan APS géomètre / MOE (`19.054 APS_2025.10.17.dxf`,
DXF AC1015, ~74 000 entités vectorielles après éclatement).

---

## 1. Pourquoi enrichir l’extraction

La première version (tables `035`, aperçu local, groupes de calques, calage)
traitait le DXF comme un **tas de polylignes colorées par calque**. C’était
suffisant pour « voir le dessin » et le caler. Ça ne l’était pas pour la
compensation écologique :

- un bloc `ARBRE` (houppier vu de haut + ATTRIB `ESSENCE`, `DIAM_TRONC`, `NUMERO`)
  ressortait en **cercles orphelins** et **textes flottants** ;
- le calque `0`, fourre-tout AutoCAD, était **exclu** — or c’est aussi le
  gisement topo (`ATPOINT` / MAT+ALT) ;
- les couleurs ByLayer / ByBlock / ACI étaient perdues ;
- le Z des points cotés et des courbes n’était pas stocké ;
- on ne pouvait pas **réimporter proprement** (le DXF restait dans `documents`).

L’objectif n’est **pas** un visualiseur 3D (MESH, 3DSOLID : comptés, ignorés).
C’est de garder le **contexte métier** collé à chaque géométrie 2D (+ Z terrain).

---

## 2. Hier vs maintenant

| Sujet | Version 7 sept. | Version actuelle |
|---|---|---|
| Éclatement INSERT | Géométrie seule | Géométrie + nom de bloc + pile `chemin_blocs` + ATTRIB |
| Calque `0` | Masqué par défaut | **Inclus** (points cotés, blocs) |
| Couleur | Teinte générique / calque | Résolue : true_color → ACI → ByBlock → ByLayer |
| Z | Aplati (XY) | Conservé dans `geom_local` (XYZ) ; calage toujours 2D |
| Calques AutoCAD | Nom + compte | + éteint / gelé / verrouillé / altimétrie |
| Inventaire | Absent | Panneau groupé par bloc + export CSV |
| Inspection | Popup sommaire | Panneau : calque, type, bloc, Z, ATTRIB |
| Arbre calques | Groupes manuels | **Préfixes** (`T-CRASTE` → T > CRASTE) + groupes |
| Emprise d’affichage | Bbox brute (xrefs inclus) | **Emprise utile** (hors IMAGE hors cadre) |
| Réimport | Remplacement du même nom, DXF orphelin | Bouton **Supprimer le DXF** (cascade PostGIS + bucket) |

---

## 3. Pipeline d’extraction (en profondeur)

Ordre réel à l’import (`POST /api/projets/{id}/cao/plans`) :

```
DXF (ou ZIP DXF+photos)
        │
        ▼
ezdxf.recover          lecture tolérante
        │
        ▼
dxf_contexte           table des calques, couleurs, états
        │
        ▼
eclater_avec_contexte  INSERT / DIMENSION / PROXY → entités filles
        │                  chaque fille hérite du Contexte parent
        ▼
dxf_processor          make_path (OCS→WCS), ARC/SPLINE/HATCH,
                       Point si INSERT sans symbole,
                       GeoJSON local (SRID 0), Z conservé
        │
        ▼
dxf_raster             IMAGE / PDFUNDERLAY (emprises, pas de vectorisation)
        │
        ▼
persist                plan_cao + calques + groupes + entités PostGIS
                       + document bucket (catégorie cao)
```

### 3.1 Contexte d’éclatement (`dxf_contexte.py`)

`eclater_avec_contexte` remplace un éclatement « nu ». Un `Contexte` porte :

- `bloc` — nom de l’INSERT (ex. `ARBRE`, `ATPOINT`) ;
- `chemin_blocs` — imbrication (`SITE > ILOT > ARBRE`) ;
- `attributs` — paires tag/valeur des ATTRIB du bloc parent ;
- `couleur_bloc` — ACI ByBlock résolu pour les filles.

Sans ça, l’inventaire arboré n’existe pas : le houppier n’est plus un arbre,
juste un cercle sur un calque.

Les types éclatés incluent notamment : `INSERT`, `ACAD_PROXY_ENTITY`,
`DIMENSION`, `LEADER`, `MULTILEADER`, `MLINE`, `ACAD_TABLE`.  
Un PROXY illisible est **conservé** (warning), pas jeté.

### 3.2 Géométries (`dxf_processor.py`)

- Chemins via `ezdxf.path.make_path` : bulges, ARC, ELLIPSE, SPLINE.
- HATCH → polygones (un anneau par contour).
- INSERT **sans** géométrie de symbole → **Point** d’insertion (blocs attributs
  seuls, typique des points cotés).
- `$INSUNITS` : si déclaré, facteur vers le mètre ; si `0`, **aucun facteur**
  (bandeau « Unités non déclarées ») — à confirmer avec l’émetteur.
- `aplatir_z=False` : chaque sommet est `[x, y, z]`.
- Types 3D volumiques (`MESH`, `3DSOLID`, …) : **comptés** dans `metadata.types_3d`,
  non convertis.

### 3.3 Calques

Pour chaque calque AutoCAD on retient : couleur / ACI, éteint, gelé, verrouillé,
et un profil Z (`z_min`, `z_max`, `porte_altimetrie`). Un calque éteint dans
AutoCAD est **extrait mais masqué par défaut** (données conservées).

Le calque `0` n’est plus un fourre-tout à jeter : sur le DXF APS, c’est aussi
le stock topo.

### 3.4 Ce qui reste hors vectorisation

- IMAGE / PDFUNDERLAY : cadres + appariement par **nom de fichier** (chemins
  Windows ignorés). 19 manquants → bandeau rouge, emprises en rouge sur le plan.
- Objets 3D volumiques (voir ci-dessus).
- Quelques entités `make_path` refuse (quelques unités sur ~74 k).

---

## 4. Persistance PostGIS

Migrations (ordre) :

| SQL | Rôle |
|---|---|
| `035_plan_cao.sql` | `plan_cao`, `plan_cao_groupe`, `plan_cao_calque`, `plan_cao_entite` |
| `036_plan_cao_calage.sql` | Index gist `geom` / `geom_3857` |
| `037_plan_cao_contexte.sql` | `bloc`, `attributs jsonb`, `couleur` ; états calques ; `calque_0_inclus` défaut `true` |
| `038_plan_cao_geom_z.sql` | `geom_local` accepte XYZ (`geometry` sans contrainte 2D) |
| `039_plan_cao_calque_fk.sql` | FK calques→groupes en `RESTRICT` (plus de SET NULL sur `plan_id`) |

Colonnes utiles sur une entité :

- `geom_local` — SRID 0, XY ou XYZ, **jamais écrasé** par le calage ;
- `geom` / `geom_3857` — remplis au calage via `ST_Force2D` + `ST_Affine` ;
- `bloc`, `attributs`, `couleur`, `calque`, `dxf_type`, `handle`, `texte`.

`document_id` pointe vers `documents` (`ON DELETE SET NULL`) : supprimer le
plan **n’efface pas** tout seul le fichier bucket. D’où l’API
`DELETE /cao/plans/{id}` qui vide d’abord les tables filles (entités, calques,
groupes), puis le DXF.

À l’affichage, l’API renvoie `ST_AsGeoJSON(ST_Force2D(geom_local))` + `z`
(`ST_ZMax`) pour MapLibre, tout en gardant le Z en base et dans le panneau
d’inspection.

---

## 5. Visualisation front (onglet CAO)

Carte **sans fond OSM** : faux repère ~0,02° (`remap.ts`) pour MapLibre. Ce n’est
pas le calage (voir `GEOREFERENCEMENT.md`).

### 5.1 Ce qu’on voit maintenant (et pas hier)

- **Formes DXF réelles**, y compris symboles de blocs éclatés — houppiers d’arbres
  vus de haut, regards, habillage, etc., dans les **couleurs AutoCAD** (blanc ACI 7
  assombri sur fond beige).
- **Clic** → panneau inspect : calque, type DXF, bloc, Z, ATTRIB (essence,
  diamètre, n° de point, fil d’eau…).
- Onglet **Inventaire** : regroupement par nom de bloc, tags d’attributs, CSV.
- **Calques** : arbre par préfixe (`T-CRASTE` → dossier T > CRASTE) ; les groupes
  manuels restent en second.
- Bandeau : nb d’entités, version DXF, unités, emprise **utile**, bouton
  **Supprimer le DXF**.

### 5.2 Comment on dessine (vs avant)

| | Avant | Maintenant |
|---|---|---|
| Style | Couleur unique / calque approximatif | `couleur` résolue par entité |
| Filtres MapLibre | `$type` + calques visibles | idem, Multi* via `$type` ; Z aplati pour le GPU |
| Cadrage | `bbox_locale` brute (fusionnée avec les IMAGE) | `bboxRobuste` : hors IMAGE/PDF, hors 2 % de centroïdes aberrants |
| Interaction | Survol | Clic + inventaire + surbrillance |
| Organisation | Groupes plats | Préfixes + groupes |

Sans l’emprise utile, un DXF APS typique affiche **1 000 × 2 000 km** (cadres
photo hors repère) : le chantier réel devient un pixel, on ne voit que deux
points (ex. libellé d’une IMAGE « Rue de la Scierie »). Avec l’emprise utile,
le dessin occupe la vue beige.

L’overlay **Cartographie** (après calage) rejoue la similitude sur le même
GeoJSON local + couleurs.

---

## 6. Cycle de vie fichier

- **Import** : `POST .../cao/plans` (DXF ou ZIP). Même `nom_fichier` → remplacement
  des lignes CAO + ancien document bucket.
- **Suppression** : bouton rouge → confirmation → `DELETE .../cao/plans/{id}` :
  entités (dont colonnes 037), calques, groupes, calage, rasters JSON, DXF
  documents/bucket, `localStorage` des groupes. On peut réimporter à neuf.
- **Groupes** : PUT avec verrou `FOR UPDATE` ; détacher `groupe_id` avant DELETE
  groupes (évite le 500 `plan_id` NULL sur FK composite).

---

## 7. Limites toujours valables

- `$INSUNITS=0` : pas de conversion mètre automatique.
- CRS du dessin : **inconnu** jusqu’au calage manuel (deux points).
- IMAGE/PDF : pas rasterisés dans PostGIS ; seulement cadres + aperçu si le
  fichier est dans le ZIP.
- PROXY AutoCAD : parfois inéclatable (`Unexpected end of buffer`).
- Mixte papier / modèle : les xrefs hors cadre restent en base, ils ne
  définissent plus la fenêtre d’affichage.
- Pas de vue 3D.

---

## 8. Fichiers clés

| Fichier | Rôle |
|---|---|
| `dxf_contexte.py` | Éclatement + calques + couleurs + profil Z + inventaire blocs |
| `dxf_processor.py` | GeoJSON local, types, INSERT→Point |
| `dxf_raster.py` | IMAGE / PDF |
| `audit_dxf.py` | Audit structurel + passe processeur (`rapport.json`) |
| `persist.py` / `router.py` | PostGIS + HTTP (preview, CRUD, DELETE, groupes, calage) |
| `frontend/.../cao/` | `CaoSandbox`, `CaoMap`, `CaoCalquesPanel`, `CaoInspectPanel`, `CaoInventairePanel`, `remap.ts` |

Scripts SQL : `backend/api/ocr/db/sql/035` à `039`.

---

## 9. Lecture croisée

- Calage, Lambert-93, overlay IGN : [`GEOREFERENCEMENT.md`](./GEOREFERENCEMENT.md).
- Pour un fichier donné, relancer `audit_dxf.py` (quoter le chemin s’il contient
  un espace) : catalogues, pas les 74 k géométries.
