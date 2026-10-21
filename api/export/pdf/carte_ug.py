"""Carte statique des unités de gestion. Fond IGN facultatif."""

from __future__ import annotations

from pathlib import Path

import httpx


def ecrire_carte(
    destination: Path,
    projet_nom: str,
    genere_le: str,
    couches: list[tuple[str, list[dict]]],
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely import wkb

    destination.parent.mkdir(parents=True, exist_ok=True)
    groupes = _groupes(couches, wkb)
    if not groupes:
        raise RuntimeError("Aucune géométrie à cartographier.")
    pages = [_etendue_totale(groupes)]
    if len(_sites(groupes)) > 1:
        pages.extend(_sites(groupes))
    fig_w, fig_h = (11.7, 8.3) if _large(pages[0]) else (11.7, 8.3)
    fig, axes = plt.subplots(len(pages), 1, figsize=(fig_w, fig_h * len(pages)), squeeze=False)
    couleurs = {
        "emprise_projet": "#245c3a",
        "ug_surf": "#3d8b5f",
        "ug_lin": "#1d4ed8",
        "ug_pct": "#b45309",
    }
    for ax, (titre, xmin, ymin, xmax, ymax, geoms) in zip(axes[:, 0], pages):
        fond = _fond_ign(xmin, ymin, xmax, ymax)
        if fond is not None:
            ax.imshow(fond, extent=(xmin, xmax, ymin, ymax), origin="upper", zorder=0)
        for nom, geom, etiquette in geoms:
            if geom.geom_type in {"Polygon", "MultiPolygon"}:
                _trace_polygone(ax, geom, couleurs.get(nom, "#64748b"))
            elif geom.geom_type in {"LineString", "MultiLineString"}:
                _trace_ligne(ax, geom, couleurs.get(nom, "#1d4ed8"))
            else:
                ax.scatter([geom.centroid.x], [geom.centroid.y], s=18, c=couleurs.get(nom, "#b45309"), zorder=3)
            if etiquette and geom.centroid:
                ax.annotate(etiquette, (geom.centroid.x, geom.centroid.y), fontsize=6, ha="center")
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.set_title(titre, loc="left", color="#245c3a")
        _echelle(ax, xmin, ymin, xmax)
        ax.annotate(
            "",
            xy=(xmax - (xmax - xmin) * 0.04, ymax - (ymax - ymin) * 0.06),
            xytext=(xmax - (xmax - xmin) * 0.04, ymax - (ymax - ymin) * 0.14),
            arrowprops={"arrowstyle": "-|>", "color": "#1c241c"},
        )
        ax.text(xmax - (xmax - xmin) * 0.04, ymax - (ymax - ymin) * 0.04, "N", ha="center", fontsize=8)
        ax.tick_params(labelsize=6)
    fig.text(0.01, 0.01, f"Export KerERC – {projet_nom} – généré le {genere_le}", fontsize=7, color="#5c6b5c")
    fig.tight_layout()
    fig.savefig(destination, format="pdf")
    plt.close(fig)


def _groupes(couches, wkb_mod) -> list[tuple[str, object, str]]:
    sortie = []
    for nom, rows in couches:
        for row in rows:
            brut = row.get("_wkb")
            if not brut:
                continue
            try:
                geom = wkb_mod.loads(bytes(brut))
            except Exception:
                continue
            etiquette = str(row.get("ug_id") or row.get("libelle") or "")
            sortie.append((nom, geom, etiquette[:24]))
    return sortie


def _etendue_totale(geoms):
    xmin = min(g.bounds[0] for _, g, _ in geoms)
    ymin = min(g.bounds[1] for _, g, _ in geoms)
    xmax = max(g.bounds[2] for _, g, _ in geoms)
    ymax = max(g.bounds[3] for _, g, _ in geoms)
    return ("Vue d'ensemble", *_marge(xmin, ymin, xmax, ymax), geoms)


def _sites(geoms) -> list[tuple]:
    restants = list(geoms)
    sites = []
    while restants:
        nom, graine, _ = restants.pop(0)
        groupe = [(nom, graine, _)]
        cx, cy = graine.centroid.x, graine.centroid.y
        encore = []
        for item in restants:
            autre = item[1]
            if (autre.centroid.x - cx) ** 2 + (autre.centroid.y - cy) ** 2 <= 5_000 ** 2:
                groupe.append(item)
            else:
                encore.append(item)
        restants = encore
        if len(geoms) > 1 and len(groupe) != len(geoms):
            xmin = min(g.bounds[0] for _, g, _ in groupe)
            ymin = min(g.bounds[1] for _, g, _ in groupe)
            xmax = max(g.bounds[2] for _, g, _ in groupe)
            ymax = max(g.bounds[3] for _, g, _ in groupe)
            sites.append((f"Zoom {len(sites) + 1}", *_marge(xmin, ymin, xmax, ymax), groupe))
    return sites


def _marge(xmin, ymin, xmax, ymax):
    dx = max(xmax - xmin, 100)
    dy = max(ymax - ymin, 100)
    return xmin - dx * 0.08, ymin - dy * 0.08, xmax + dx * 0.08, ymax + dy * 0.08


def _large(_page) -> bool:
    return True


def _trace_polygone(ax, geom, couleur: str) -> None:
    parties = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    for partie in parties:
        x, y = partie.exterior.xy
        ax.fill(x, y, facecolor=couleur, edgecolor="#1c241c", alpha=0.45, linewidth=0.6, zorder=2)


def _trace_ligne(ax, geom, couleur: str) -> None:
    parties = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
    for partie in parties:
        x, y = partie.xy
        ax.plot(x, y, color=couleur, linewidth=1.2, zorder=2)


def _echelle(ax, xmin, ymin, xmax) -> None:
    largeur = xmax - xmin
    choisi = max(int(largeur * 0.2), 1)
    for longueur in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
        if longueur < largeur * 0.25:
            choisi = longueur
    y = ymin + (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.04
    x0 = xmin + largeur * 0.05
    ax.plot([x0, x0 + choisi], [y, y], color="#1c241c", linewidth=2, solid_capstyle="butt")
    ax.text(x0, y, f"{choisi:g} m", fontsize=6, va="bottom")


def _fond_ign(xmin, ymin, xmax, ymax):
    url = (
        "https://data.geopf.fr/wms-r/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
        "&LAYERS=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLES=&CRS=EPSG:2154"
        f"&BBOX={xmin},{ymin},{xmax},{ymax}&WIDTH=900&HEIGHT=640&FORMAT=image/png"
    )
    try:
        reponse = httpx.get(url, timeout=8.0)
        if reponse.status_code >= 400 or not reponse.content:
            return None
        from io import BytesIO
        import matplotlib.pyplot as plt
        return plt.imread(BytesIO(reponse.content))
    except Exception:
        return None
