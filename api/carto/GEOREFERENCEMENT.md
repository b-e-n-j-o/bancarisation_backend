# Géoréférencement des plans CAO (DXF)

Trace de l’implémentation du 7 septembre 2026.
Le calage n’est **pas** « coller le DXF sur OSM ». C’est une **similitude 2D**
(translation + rotation + échelle) du repère du dessin vers **Lambert-93 (EPSG:2154)**,
le même CRS que les UG et le cadastre. MapLibre affiche ensuite en lng/lat (4326)
par-dessus l’ortho IGN déjà utilisée par la carto projet.

---

## Réponse courte

| Question | Réponse |
|---|---|
| Chaque entité a-t-elle une géométrie géoréférencée **en base** ? | **Oui**, au moment de « Enregistrer le calage ». `ST_Affine` est appliqué à **toutes** les lignes de `plan_cao_entite`. |
| Les 2 points suffisent-ils, le reste est-il calculé à la volée ? | Les 2 points servent **uniquement** à estimer 4 nombres (`tx`, `ty`, `rotation_rad`, `echelle`). Ensuite **la même** transformation s’applique à tout le dessin. |
| Que voit MapLibre au « Voir la carte » ? | Pas `geom` PostGIS. Le GET renvoie encore `geom_local` (GeoJSON SRID 0) + les 4 paramètres. Le navigateur **rejoue** la similitude côté client, puis Lambert-93 → lng/lat. |
| Le cadastre PCI du rectangle de calage est-il persisté ? | **Non**. Session uniquement. |

Donc : **double piste**. La base a les copies 2154 / 3857 (pour plus tard : export, croisement UG, mesures). L’overlay carto actuel est un calcul à la volée à partir du dessin brut + des 4 paramètres.

---

## 1. Avant tout calage — ce qui est déjà en base

À l’import DXF (`POST /api/projets/{id}/cao/plans`) :

1. `ezdxf` éclate les blocs, convertit OCS → WCS, discrétise les courbes.
2. Chaque entité vectorielle est écrite dans `bancarisation.plan_cao_entite` :
   - `geom_local` = géométrie **SRID 0** (repère du dessin, éventuellement normalisée en mètres via `$INSUNITS`).
   - `geom` et `geom_3857` = **NULL**.
3. `plan_cao.calage_mode = 'non_cale'`, `statut = 'analyse'`.

`geom_local` n’est **jamais** écrasé ensuite. C’est la source de vérité du dessin.

L’onglet CAO (bac à sable) n’est pas géoréférencé : `remap.ts` plaque le dessin dans une fausse fenêtre ~0.02° pour MapLibre, sans fond IGN. Ne pas confondre avec le calage.

---

## 2. Wizard de calage (front)

Ordre des étapes (`calageSession.ts`) :

1. **`zone`** — deux clics sur l’ortho IGN (coins d’un rectangle).
2. Hit WFS PCI Express (`CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle`). Si `numberMatched > 5000`, la bbox est **réduite de moitié autour du centre**, nouveau hit, jusqu’à passer sous le cap (`pci_wfs.py`). Les parcelles orange sont en mémoire de session, **pas en SQL**.
3. **`src1` / `dst1` / `src2` / `dst2`** — couple 1 puis couple 2 :
   - source = clic sur le DXF (coordonnées **locales**, accrochage sommets) ;
   - cible = clic sur l’ortho / UG / angle PCI (lng/lat → Lambert-93 via `crs.ts`).
4. **`apercu`** — overlay DXF à opacité réglable. Rien n’est encore écrit en `geom`.
5. **Enregistrer** → `PUT .../calage`.

« Continuer sans PCI » saute l’étape 2 si le cadastre déjà chargé autour des UG suffit.

---

## 3. Pile au clic « Enregistrer le calage »

Ce n’est **pas** « on n’enregistre que les 2 points et on improvise le reste à chaque affichage sans rien en base ».

### 3.1 Les 2 points → 4 paramètres (Helmert)

Les paires envoyées :

```json
{
  "mode": "deux_points",
  "paires": [
    { "src": [x1, y1], "dst_2154": [X1, Y1] },
    { "src": [x2, y2], "dst_2154": [X2, Y2] }
  ]
}
```

`src` = unités du dessin. `dst_2154` = mètres Lambert-93.

Le backend **recalcule** la similitude (il ne fait pas confiance aux coeffs du client) :

```
échelle     = |T2 − T1| / |S2 − S1|
rotation    = atan2(T2 − T1) − atan2(S2 − S1)
tx, ty      = T1 − R_échelle,rotation(S1)
```

Une similitude, c’est une **isométrie + homothétie** : pas de cisaillement. Un APS plan 2D, 2 points suffisent. Un 3ᵉ point ne servirait que s’il restait un cisaillement (scan mal mis à l’échelle).

### 3.2 La même affine sur **toutes** les entités

`ST_Affine` 2D : `x' = a x + b y + xoff`, `y' = d x + e y + yoff`

avec `a = s·cosθ`, `b = −s·sinθ`, `d = s·sinθ`, `e = s·cosθ`.

```sql
UPDATE bancarisation.plan_cao_entite
SET
  geom = ST_SetSRID(
    ST_MakeValid(ST_Affine(ST_Force2D(geom_local), a, b, d, e, tx, ty)),
    2154
  ),
  geom_3857 = ST_Transform(…même affine…, 3857)
WHERE plan_id = :plan;
```

Et sur l’en-tête du plan :

- `calage_mode = 'deux_points'`
- `tx`, `ty`, `rotation_rad`, `echelle`, `srid_cible = 2154`
- `statut = 'cale'`
- `metadata.calage.paires` = les 2 GCP (pour recaler / audit)

**Chaque polyligne, polygone, texte, point DXF** a donc, après ce `UPDATE`, sa copie géoréférencée. Ce n’est pas un sous-ensemble.

Les rasters / images du DXF ne passent pas encore dans `ST_Affine` (hors scope du 7 sept.).

---

## 4. « Voir la carte » / overlay MapLibre — ce qui se passe vraiment

Après enregistrement (ou au rechargement de la page) :

1. `GET /api/projets/{id}/cao/plans/{plan_id}` lit **`ST_AsGeoJSON(geom_local)`**, pas `geom`.
2. Le JSON emporte aussi `calage: { tx, ty, rotation_rad, echelle, mode, paires }`.
3. `CaoOverlayLayer` appelle `geojsonCale()` dans le navigateur :
   - chaque sommet local → Helmert → XY 2154 ;
   - XY 2154 → lng/lat (`crs.ts`, conique Lambert GRS80, sans `proj4`) ;
   - source GeoJSON MapLibre, **sous** les UG (`before: project-emprise-fill`).
4. Filtres calques = les mêmes que le bac à sable (`visibles`).

Pourquoi ce dédoublement ?

- L’aperçu **avant** enregistrement ne peut pas lire `geom` (encore NULL) : il **doit** transformer `geom_local` à la volée.
- Après enregistrement, on a gardé le même chemin pour ne pas charger deux GeoJSON différents. Les colonnes `geom` / `geom_3857` sont prêtes pour le métier SIG (elles ne servent pas encore à l’affichage).

Conséquence : si on recalait sans relancer `ST_Affine`, la carte suivrait les nouveaux paramètres mais `geom` en base serait **périmé**. Aujourd’hui « Recaler » + Enregistrer relance bien l’`UPDATE` complet.

« Voir la carte » ne refait **pas** un second envoi de toutes les géométries : le plan est déjà dans le state React (`caoPreview`). Un F5 refait le GET (tout `geom_local` en GeoJSON).

---

## 5. Chaîne de CRS (à ne pas mélanger)

```
DXF (unités dessin, SRID 0)
  → geom_local                    [jamais modifié]
  → similitude Helmert
  → geom EPSG:2154                [PostGIS, après Enregistrer]
  → ST_Transform → geom_3857      [PostGIS, même moment]
  → overlay MapLibre : 2154 → 4326 côté client (affichage)
UG / cadastre projet :
  → stockés 2154 + 3857
  → servis à MapLibre en 4326 via ST_Transform(geom_3857, 4326)
Fond : WMTS IGN ORTHOIMAGERY.ORTHOPHOTOS (tuiles Web Mercator)
```

Le bac à sable CAO **non calé** utilise un faux lng/lat (`SPAN_DEG = 0.02`). Ça n’a rien à voir avec 2154.

---

## 6. Tables et endpoints

### `plan_cao`

| Colonne | Rôle |
|---|---|
| `calage_mode` | `non_cale` \| `deux_points` \| (`srid_direct`, `manuel` réservés) |
| `tx`, `ty`, `rotation_rad`, `echelle` | Helmert |
| `srid_cible` | 2154 |
| `statut` | `analyse` → `cale` |
| `metadata.calage.paires` | GCP |

### `plan_cao_entite`

| Colonne | SRID | Quand |
|---|---|---|
| `geom_local` | 0 | import DXF, immuable |
| `geom` | 2154 | après Enregistrer |
| `geom_3857` | 3857 | après Enregistrer |

Migration index (optionnelle) : `036_plan_cao_calage.sql`.

### HTTP

| Méthode | Chemin | Effet |
|---|---|---|
| `POST` | `.../cao/plans` | parse DXF, persist `geom_local` |
| `POST` | `.../cao/pci-bbox` | WFS PCI, **non persisté** |
| `GET` | `.../cao/plans/{id}/export-shp` | ZIP shapefile **Lambert-93** depuis `geom` (plan déjà calé). Un SHP par type : points / lignes / polygones. |
| `PUT` | `.../cao/plans/{id}/calage` | Helmert + `ST_Affine` toutes entités |
| `GET` | `.../cao/plans/{id}` | GeoJSON **local** + params de calage |

Fichiers : `calage.py`, `persist.appliquer_calage`, `pci_wfs.py`, `similitude.ts`, `crs.ts`, `CaoOverlayLayer.tsx`, `GeometriesProjetMap.tsx`.

---

## 7. Limites utiles à retenir

- **2 points = similitude Helmert** (translation + rotation + échelle uniforme). C’est le **bon** modèle pour un DXF CAO cartésien. Pas de cisaillement, pas d’homographie : on ne « déforme » pas le dessin. Un modèle plus riche (affine 6 params, rubber-sheeting) serait pour un scan papier / photo, pas pour un APS.
- **Dérive au bord** : ce n’est pas un défaut du modèle. Ça arrive si les 2 GCP sont trop proches, mal homologues, ou si le DXF et le cadastre ne décrivent pas le même objet. Sur un dessin à l’échelle, 2 bons points éloignés collent **partout**.
- **`$INSUNITS = 0`** n’empêche pas le calage : l’échelle des 2 points corrige « 1 unité ≠ 1 m ».
- **Traits CAO ≠ contours d’UG.** Le calage aligne le repère, pas le métier. L’APS dessine souvent l’emprise projet, pas le polygone d’obligation.
- **PCI de calage** : jetable. Le snapshot `cadastre_parcelle` du projet (autour des UG) est un autre flux.
- **Overlay ≠ `geom` PostGIS.** L’affichage MapLibre rejoue Helmert côté client. L’export SHP (`GET .../export-shp`) et un `ST_Intersects` UG ∩ DXF lisent `geom` (2154). Un ZIP = autant de shapefiles que de types présents (`_pct`, `_lin`, `_pol`), attributs `calque`, `dxf_type`, `handle`, `texte`.
- **`srid_direct`** (plan déjà dessiné en L93) et **calage manuel** (glisser / tourner le calque) sont prévus dans le CHECK SQL, pas branchés dans l’UI du 7 sept.

---

## 8. Schéma mental

```
                    2 clics DXF              2 clics ortho / PCI
                         │                            │
                         └──────────┬─────────────────┘
                                    ▼
                         Helmert (tx, ty, θ, s)
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            ST_Affine (PostGIS)              geojsonCale (navigateur)
            toutes les entités               aperçu + overlay carto
            geom + geom_3857                 à partir de geom_local
                    │                               │
                    ▼                               ▼
            vérité SIG (2154)                ce que MapLibre dessine
```

---

## 9. Précision — ce qu’on peut dire aux clients

On n’est **jamais à 100 %** au sens géomètre-expert / GNSS. Le calage aligne le DXF **sur le même référentiel cartographique** que les UG (Lambert-93 + PCI / ortho IGN), pas sur le terrain.

La qualité dépend surtout de :

1. **Homologie** — le même coin physique sur le DXF et sur la carte (pas un mur APS vs un angle parcelle voisin).
2. **Baseline** — les 2 points **éloignés** (idéalement diagonale du plan). Un millimètre d’erreur de clic, ou 20 cm de mauvais sommet, est multiplié loin des GCP si ceux-ci sont trop proches.
3. **Cible** — un snap PCI Express réduit l’erreur de clic, mais le cadastre lui-même n’est pas du bornage (précision métrique typique, variable urbain / rural). On colle au **cadastre de l’app**, pas à la « vérité terrain ».
4. **Dessin source** — un APS à l’échelle uniforme se cale bien. Un croquis, un scan, un plan déjà déformé ne se rattrape pas avec 2 points.

**Phrase type :** *« Le calage 2 points aligne le plan CAO sur le cadastre et l’ortho déjà utilisés pour les UG. Avec deux angles homologues, éloignés, accrochés au PCI, le dessin est cartographiquement cohérent avec le reste du dossier. Ce n’est pas un levé. Un 3ᵉ point connu (contrôle visuel, pas calculé) permet de juger le résidu. »*

Ordre de grandeur honnête : à proximité des GCP bien snappés, l’écart est surtout celui du cadastre + du clic (souvent submétrique à métrique). Loin de la baseline, ou si les points ne sont pas homologues, ça se dégrade. Un désaccord DXF ↔ parcelle après un bon calage est en général un **désaccord métier** (l’APS n’est pas la limite cadastrale), pas un bug de projection.
