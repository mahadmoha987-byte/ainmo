import asyncio

from starlette.requests import Request

import api


def test_current_analysis_pdf_is_public(monkeypatch):
    monkeypatch.setattr(api, "_gis_lookup_cached", lambda *_args: {"lote": {}})
    monkeypatch.setattr(api.calc, "calculate", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(api.pdf_report, "generate_pdf", lambda **_kwargs: b"%PDF-public")

    request = Request({"type": "http", "method": "GET", "path": "/api/report", "headers": []})
    response = asyncio.run(
        api.report_endpoint(
            request=request,
            lng=-74.052,
            lat=4.668,
            vis_en_sitio=False,
            anu_m2=None,
            frente_m=None,
            ancho_via_m=None,
            address="CL 85 # 11-53",
            expected_lotcodigo=None,
            preview=False,
        )
    )

    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-public"
    assert response.headers["content-disposition"] == 'attachment; filename="prefactibilidad.pdf"'
