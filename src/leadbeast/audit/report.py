"""ReportRenderer: Jinja to HTML (A4), then WeasyPrint to PDF when installed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from leadbeast.core.clock import now
from leadbeast.core.config import get_settings

TEMPLATES = Path(__file__).resolve().parent.parent / "web" / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


class HtmlPdfRenderer:
    def html(self, audit: dict[str, Any]) -> str:
        st = get_settings()
        return _env.get_template("report.html").render(
            a=audit, operator={"name": st.operator_name, "phone": st.operator_phone,
                               "address": st.operator_address, "email": st.operator_email},
            generated=now().strftime("%d %B %Y"),
        )

    def pdf(self, audit: dict[str, Any]) -> bytes:
        try:
            from weasyprint import HTML
        except ImportError as e:  # optional extra: pip install leadbeast[pdf]
            raise RuntimeError("PDF rendering needs the 'pdf' extra (WeasyPrint)") from e
        return bytes(HTML(string=self.html(audit)).write_pdf())


def save(audit_id: str, html: str) -> Path:
    d = Path(get_settings().reports_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{audit_id}.html"
    p.write_text(html, encoding="utf-8")
    return p
