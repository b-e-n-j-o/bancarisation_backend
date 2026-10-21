"""Rendu PDF des livrables d'export. WeasyPrint et Jinja2, comme les bilans.

Le template des bilans financiers n'est pas modifié : leur pied de page reste le leur.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _css(valeur: str) -> str:
    return (valeur or "").replace("\\", "").replace('"', "'").replace("\n", " ")


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["css"] = _css
    return env


def rendre_html(template: str, contexte: dict[str, Any]) -> str:
    return _env().get_template(template).render(**contexte)


def rendre_pdf(template: str, contexte: dict[str, Any], destination: Path) -> Path:
    from weasyprint import HTML

    destination.parent.mkdir(parents=True, exist_ok=True)
    html = rendre_html(template, contexte)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(destination)
    return destination
