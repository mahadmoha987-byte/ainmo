"""Additive presentation contract. Never changes legacy calculation values.

Scalar fields keep their original type; `figuras` provides their figure objects.
Status and confidence are independent. An exact formula can use uncertain GIS.
"""
from collections import Counter
from copy import deepcopy
from enum import Enum
import math


class Estado(str, Enum):
    RESUELTO = "resuelto"
    DERIVADO = "derivado"
    INSUFICIENTE = "insuficiente"
    REQUIERE_CONCEPTO = "requiere_concepto"
    NO_APLICA = "no_aplica"
    ERROR = "error"


LABELS = {
    "area_construible_max_m2": "Área construible máxima normativa",
    "area_construible_estimada": "Área construible estimada",
    "planta_maxima_m2": "Huella máxima normativa",
    "altura_base_pisos": "Altura base del mapa",
    "altura_maxima_pisos": "Altura máxima",
    "altura_con_bonus_pisos": "Altura adicional condicionada",
    "altura_con_vis_bonus_pisos": "Altura con incentivo VIS/VIP",
    "altura_maxima_rango_pisos": "Altura superior del rango (condicionada)",
    "altura_base_con_vis_bonus_pisos": "Altura base con incentivo VIS/VIP",
    "altura_bonus_manzana_con_vis_bonus_pisos": "Altura adicional con incentivo VIS/VIP",
    "aislamiento_posterior_m": "Aislamiento posterior",
    "aislamiento_lateral_m": "Aislamiento lateral",
    "retroceso_fachada_A_m": "Altura máxima de fachada (A = factor × D)",
    "area_construible_max_sin_manzana_completa_m2": "Área ICe 5,0 (sin manzana completa)",
    "area_construible_max_esquina_manzana_m2": "Área ICe 6,0 (esquina condicionada)",
    "area_construible_max_manzana_completa_m2": "Área ICe 7,0 (manzana completa)",
    "area_construible_plan_parcial_m2": "Área mediante plan parcial",
    "area_techo_ic_m2": "Techo por índice de construcción",
    "area_efectiva_m2": "Área efectiva por huella y pisos",
    "area_construible_max_vis75_m2": "Área condicionada a más de 75 % VIS/VIP",
    "pisos_implicitos_IC_IO": "Cociente IC/IO (no es altura autorizada)",
    "aislamiento_posterior_tabla": "Aislamiento posterior según altura del proyecto",
    "aislamiento_lateral_umbral_m": "Umbral de altura para aislamiento lateral",
    "aislamiento_entre_edificaciones": "Aislamiento entre edificaciones",
    "condicion_englobe_manzana": "Condición de englobe",
    "obligacion_vip": "Obligación VIP",
    "cesion_espacio_publico_m2": "Cesión de espacio público",
    "aislamiento_lateral_nivel_exigencia": "Nivel de exigencia del aislamiento lateral",
}


def _finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def _status(estado, motivo, needed=None, who=None):
    allowed = {"SDP", "curaduría", "IDPC", "topógrafo", "profesional", None}
    if who not in allowed:
        text = str(who or "").lower()
        who = ("IDPC" if "idpc" in text else "curaduría" if "curadur" in text
               else "topógrafo" if "topógraf" in text else "SDP" if "sdp" in text
               else "profesional" if who else None)
    return dict(estado=estado, motivo=motivo, que_se_necesita=needed, quien_lo_resuelve=who)


def _classify(key, obj, trat):
    value = obj.get("valor", obj.get("valor_m2", obj.get("dimension_m")))
    note = obj.get("nota") or obj.get("razon_nulo") or obj.get("error") or obj.get("condicion") or ""
    lower = note.lower()
    if isinstance(value, float) and not math.isfinite(value):
        return _status("error", "El motor produjo un valor no finito; no debe utilizarse.", "Repetir la consulta y reportar el error a Ainmo.")
    if key == "area_construible_estimada":
        if obj.get("fuera_de_rango"):
            return _status(
                "requiere_concepto",
                obj.get("motivo") or "El tamaño del predio está fuera del rango validado del modelo automatizado.",
                obj.get("que_se_necesita") or "Modelación completa del predio, sus cargas, cesiones, accesos y etapas de desarrollo.",
                obj.get("quien_lo_resuelve") or "profesional",
            )
        rng = obj.get("rango_m2")
        valid_value = _finite(value) and value > 0
        valid_range = (isinstance(rng, (list, tuple)) and len(rng) == 2
                       and all(_finite(v) and v > 0 for v in rng) and rng[0] <= rng[1])
        if valid_value or valid_range:
            return _status("derivado", "Estimación de huella por pisos, no un tope fijado numéricamente por el decreto.", "Verificar geometría, aislamientos y altura mediante modelación del proyecto.", "profesional")
        return _status("insuficiente", note or "Faltan entradas para estimar la huella y los pisos.", "Confirmar polígono, antejardín, aislamientos, perfil vial y altura base.", "profesional")
    if "CONSOLIDACION" in trat and key in {"area_construible_max_m2", "planta_maxima_m2"} and value is None:
        return _status("no_aplica", "El Decreto 555 no fija índice de construcción ni de ocupación numéricamente en tratamiento de Consolidación.")
    if key == "aislamiento_lateral_umbral_m" and _finite(value):
        return _status("resuelto", "Umbral normativo de altura; no es la dimensión del aislamiento ni determina por sí solo su exigencia en este proyecto.")
    if "concepto" in lower or "pemp" in lower or key == "area_construible_plan_parcial_m2":
        return _status("requiere_concepto", note or "La edificabilidad depende del plan parcial y su adopción.", "Consultar el instrumento aplicable y su acto de adopción.", "IDPC" if "pemp" in lower else "SDP")
    if any(s in lower for s in ("no aplica", "no se exige", "no hay separación", "no exigido")) or obj.get("aplica") is False or obj.get("requerido") is False or (key == "aislamiento_lateral_m" and value == 0):
        return _status("no_aplica", note or "Esta exigencia no aplica a la tipología o al escenario consultado.")
    if key == "aislamiento_posterior_tabla":
        return _status("insuficiente", note or "Falta la altura definitiva para seleccionar el aislamiento posterior.", "Definir altura real en metros para seleccionar la fila aplicable de la tabla del Anexo 5.", "arquitecto")
    if key == "subdivision_permitida" and isinstance(value, bool):
        return _status("resuelto", note or "La norma identifica la condición de subdivisión; verificar requisitos del proyecto.")
    if value is None and ("resultante" in lower or "no fijado" in lower or "requiere modelado" in lower):
        return _status("no_aplica", note or "La norma no fija un número: es resultante del proyecto.")
    if _finite(value):
        if key == "retroceso_fachada_A_m":
            if obj.get("confianza") == "alta":
                return _status("resuelto", note or "La relación de fachada fue resuelta con una fuente de alta confianza.")
            return _status("insuficiente", "Falta el perfil vial completo; la fuente disponible puede incluir únicamente la calzada.", "Confirmar calzada, andenes y separador del perfil vial frente al predio.", "topógrafo")
        derived = key in {"area_efectiva_m2", "pisos_implicitos_IC_IO"} or (key == "aislamiento_lateral_m" and obj.get("confianza") == "media")
        if derived:
            return _status("derivado", note or "Valor calculado a partir de las entradas del predio y una fórmula; no es una cifra literal del decreto.", "Verificar las dimensiones reales y los supuestos del cálculo.", "arquitecto / topógrafo")
        return _status("resuelto", note or "Valor obtenido de la regla o tabla aplicable; sujeto a las condiciones y fuentes indicadas.")
    if obj.get("formula"):
        return _status("insuficiente", note or "La fórmula está identificada, pero faltan entradas para obtener un valor numérico.", "Definir las dimensiones y condiciones del proyecto que exige la fórmula indicada.", "arquitecto")
    if obj.get("requerido") is True or obj.get("aplica") is True or obj.get("nivel"):
        return _status("resuelto", note or "Se identifica la regla aplicable; su fórmula y condiciones se muestran a continuación.")
    return _status("insuficiente", note or "No hay datos suficientes para resolver esta variable.", {
        "antejardin": "Consultar dimensión en mapa CU-5.5, Capa 22, campo DIMENSION, con SDP.",
        "retroceso_fachada_A_m": "Aportar perfil vial completo: calzada, andenes y separador; Capa 38 solo aporta calzada.",
        "altura_base_pisos": "Confirmar ALTURA_MAXIMA en Capa 15 y ficha normativa con SDP.",
        "altura_con_bonus_pisos": "Aportar frente, perfil vial y condiciones del proyecto para evaluar la altura adicional.",
        "aislamiento_posterior_tabla": "Definir altura real en metros para consultar la tabla del Anexo 5.",
    }.get(key, "Confirmar el dato o la condición pendiente en la fuente y en el proyecto."), "SDP" if key in {"antejardin", "altura_base_pisos"} else "arquitecto")


def annotate_result(result, lookup=None):
    """Return a copy: cached lookup dictionaries must never receive statuses."""
    d = deepcopy(result)
    if lookup and "edificabilidad" not in d:
        d["edificabilidad"] = deepcopy(lookup.get("edificabilidad") or {})
    trat = (d.get("tratamiento") or "").upper().replace("Ó", "O")
    article = "310" if "CONSOLIDACION" in trat else "304" if "RENOVACION" in trat else "338" if "MEJORAMIENTO" in trat else "281" if "DESARROLLO" in trat else None
    figures = []

    def add(path, label, obj, key, unit="", section="volumetria", ref=None, source=None):
        obj.update(_classify(key, obj, trat))
        obj.setdefault("fecha_consulta", (d.get("consulta") or {}).get("fecha"))
        figure = dict(obj, id=path, etiqueta=label, unidad=unit, seccion=section,
                      articulo_id=ref, fuente_dato=source or obj.get("fuente") or "Fuente no individualizada en el cálculo; verificar trazabilidad.")
        figures.append(figure)
        return figure

    lot = (d.get("lote") or {}).get("area_m2")
    if isinstance(lot, dict):
        f = add("lote.area_m2", "Área del lote catastral", lot, "area_lote", "m²", source="Catastro · Capa 0 · polígono proyectado en WKID 9377")
        # Area is measured from geometry, not prescribed by a decree.
        meta = _status("derivado", "Área calculada del polígono catastral; no es una cifra fijada por norma.", "Contrastar polígono y cabida con levantamiento y títulos.", "topógrafo") if _finite(lot.get("valor")) else lot
        lot.update({k: meta[k] for k in ("estado", "motivo", "que_se_necesita", "quien_lo_resuelve")})
        f.update(lot)
    if isinstance(d.get("anu"), dict):
        f = add("anu", "Área de referencia para el cálculo", d["anu"], "anu", "m²", source=d["anu"].get("fuente"))
        if _finite(d["anu"].get("valor_m2")):
            meta = _status("derivado", "Base de área adoptada para el cálculo; no es un número fijado por el decreto.", "Verificar origen del área; en Desarrollo, contrastar con el plan parcial cuando corresponda.", "arquitecto / SDP")
            d["anu"].update(meta)
            f.update(meta)
    if isinstance(d.get("antejardin"), dict):
        f = add("antejardin", "Antejardín", d["antejardin"], "antejardin", "m", ref="466:1.7", source="SDP · Capa 22 · DIMENSION / mapa CU-5.5; Anexo 5 Sección 1.7")
        if "RENOVACION" in trat:
            meta = _status("no_aplica", "No se exige antejardín general en Renovación Urbana, salvo normas de empates (Anexo 5 D.466/2024, Sección 1.7.a).")
            d["antejardin"].update(meta)
            f.update(meta, fuente_verificada="Anexo 5 D.466/2024 · Sección 1.7.a.")
    # Indices must remain visible even when they are resultant. Other lookup
    # objects receive metadata but are not counted twice alongside their metrics.
    ed = d.get("edificabilidad") or {}
    for key, obj in ed.items():
        if not isinstance(obj, dict):
            continue
        view = dict(obj)
        if "valor_m" in obj:
            view["valor"] = obj["valor_m"]
        elif "pisos_max" in obj:
            view["valor"] = obj["pisos_max"]
        obj.update(_classify(key, view, trat))
        if key in {"indice_construccion", "indice_ocupacion", "indice_construccion_vis75", "subdivision_permitida"}:
            labels = {"indice_construccion":"Índice de construcción (IC)", "indice_ocupacion":"Índice de ocupación (IO)", "indice_construccion_vis75":"IC condicionado a VIS/VIP", "subdivision_permitida":"Subdivisión predial"}
            f = add(f"edificabilidad.{key}", labels[key], obj, key, ref=f"555:{article}" if article else None,
                    source="SDP · Capa 15 TRATAMIENTO/TIPOLOGIA; Capa 21 RANGO cuando corresponde")
            if key == "subdivision_permitida" and obj.get("valor") is True:
                # Article 310.4 only establishes the prohibition in isolated typology.
                meta = _status("insuficiente", "La tipología continua no basta para autorizar una subdivisión; falta cotejar los mínimos y condiciones específicos.", "Verificar frente, área resultante y sección de subdivisiones del Anexo 5 con curaduría.", "curaduría")
                obj.update(meta)
                f.update(meta, articulo_id=None)
    for key, obj in (d.get("metrics") or {}).items():
        if not isinstance(obj, dict):
            continue
        unit = "m²" if key.endswith("m2") or key == "area_construible_estimada" else "pisos" if key.endswith("pisos") else "m" if key.endswith("_m") else ""
        ref = f"555:{article}" if article else None
        source = "SDP · Capa 15 · TRATAMIENTO y ALTURA_MAXIMA"
        if "aislamiento" in key or "retroceso" in key:
            ref = None  # Annex quotations require an exact section mapping, not a guessed article.
            source = obj.get("fuente") or "Anexo 5: sección del cálculo pendiente de cotejo documental."
        if key == "aislamiento_lateral_m":
            ref = "555:310" if obj.get("valor") == 0 and "CONSOLIDACION" in trat else "466:1.9.b"
            source = "SDP · Capa 15 · TIPOLOGIA; Anexo 5 D.466/2024, Sección 1.9.b y Art. 310.3"
        if key == "aislamiento_posterior_m" and "CONSOLIDACION" in trat:
            ref = "466:5.4.b"
            source = "SDP · Capa 15 · ALTURA_MAXIMA; Anexo 5 D.466/2024 · Sección 5.4.b"
        if key == "aislamiento_posterior_m" and "MEJORAMIENTO" in trat:
            ref = "466:4.2.b"
        if key == "aislamiento_posterior_tabla" and "RENOVACION" in trat:
            ref = "466:3.1.b"
        if key == "aislamiento_lateral_umbral_m" and "RENOVACION" in trat:
            ref = "466:3.2.a"
        if key == "retroceso_fachada_A_m":
            ref = "466:1.11.a"
            source = obj.get("fuente_D") or "SDP · Capa 38 · ANCHO (calzada, no perfil completo)"
        if key == "area_construible_plan_parcial_m2":
            ref = "555:303"
        if key == "area_construible_estimada":
            source = "Catastro Capa 0; SDP Capas 15 ALTURA_MAXIMA, 22 DIMENSION y 38 ANCHO; supuestos geométricos."
        if "253/2026" in str(obj.get("articulo", "")):
            ref = None  # Anexo 36.1 is not Article 310: do not substitute a quote.
            source = obj.get("fuente") or "Anexo 36.1 D.253/2026; transcripción pendiente de cotejo."
        add(f"metrics.{key}", LABELS.get(key, key.replace("_", " ")), obj, key, unit, ref=ref, source=source)
    reference = d.get("referencia_obligaciones_urbanisticas_2026") or {}
    if reference:
        # The legacy estado has a different purpose; preserve it unchanged.
        add("referencia_obligaciones_urbanisticas_2026.valor_tope_cop_m2", "Tope de referencia de obligaciones (no es liquidación)",
            {"valor": reference.get("valor_tope_cop_m2"), "nota": reference.get("nota"), "articulo": reference.get("cita")},
            "referencia_obligaciones", "COP/m²", section="restricciones", source=reference.get("norma"))
    pk = d.get("parking") or {}
    for key, label in [("min_pct", "Estacionamientos: mínimo"), ("max_pct", "Estacionamientos: máximo"), ("adicional_pct", "Estacionamientos: adicional"), ("min_area_m2", "Área mínima de estacionamientos"), ("max_area_m2", "Área máxima de estacionamientos"), ("adicional_area_m2", "Área adicional de estacionamientos")]:
        obj = dict(valor=pk.get(key), confianza=pk.get("confianza"), nota=pk.get("nota"))
        f = add(f"parking.{key}", label, obj, key, "%" if key.endswith("pct") else "m²", ref="555:389", source="SDP · Capa 14 · CODIGO; base de área Art. 390")
        if key.endswith("m2"):
            f.update(_status("derivado", "Área calculada con el porcentaje y la base de área del escenario.", "Confirmar base cubierta excluyendo estacionamientos y sótanos (Art. 390).", "arquitecto") if _finite(pk.get(key)) else _status("insuficiente", "No se dispone de una base de área para calcular los metros cuadrados de estacionamientos.", "Definir área cubierta del proyecto para estacionamientos según Art. 390.", "arquitecto"))
    restriction_names = {"bic":"Patrimonio / BIC", "aerocivil":"Restricción aeronáutica", "cerros_orientales":"Cerros Orientales", "movimientos_en_masa":"Amenaza por movimientos en masa", "inundacion":"Amenaza por inundación", "ronda_hidrica":"Ronda hídrica"}
    for key, coverage in (d.get("cobertura_restricciones") or {}).items():
        if coverage == "consultado":
            continue
        # A successfully queried layer is not evidence of absence of a restriction.
        figures.append(dict(id=f"cobertura_restricciones.{key}", etiqueta=restriction_names.get(key,key), valor=None, unidad="", seccion="restricciones", articulo_id=None,
            fuente_dato="Cobertura de consulta GIS; no es un concepto de la autoridad.", fecha_consulta=(d.get("consulta") or {}).get("fecha"),
            **_status("requiere_concepto" if coverage == "consultado" else "insuficiente",
                "Se consultó la capa; la aplicabilidad y las condiciones del instrumento deben verificarse." if coverage == "consultado" else "No se verificó espacialmente esta restricción; SIN_DATO no significa ausencia de afectación.",
                "Obtener ficha o concepto oficial aplicable al polígono del predio.", "IDPC" if key=="bic" else "Aerocivil" if key=="aerocivil" else "IDIGER" if key in {"inundacion","movimientos_en_masa"} else "SDA / SDP")))
    # Preserve the legacy coverage codes and expose an additive object contract.
    d["cobertura_restricciones_detalle"] = {}
    for key, coverage in (d.get("cobertura_restricciones") or {}).items():
        if coverage == "sin_fuente_configurada":
            meta = _status("insuficiente", "No hay fuente automatizada configurada para esta verificación.", "Realizar la verificación en la fuente oficial aplicable.", "profesional")
        elif coverage == "consultado":
            meta = _status("resuelto", "La fuente automatizada configurada fue consultada; revise el resultado específico de la capa.")
        else:
            meta = _status("insuficiente", "No hay verificación espacial automatizada completa para esta restricción.", "Realizar la verificación espacial en la fuente oficial aplicable.", "profesional")
        d["cobertura_restricciones_detalle"][key] = {"codigo": coverage, **meta}
    if d.get("metrics") is None:
        heritage = "CONSERVACION" in trat
        figures.append(dict(id="edificabilidad", etiqueta="Edificabilidad", valor=None, unidad="", seccion="volumetria", articulo_id=None,
            **_status("requiere_concepto" if heritage else "insuficiente", "El tratamiento patrimonial requiere revisión del instrumento específico; Ainmo no calcula una cabida." if heritage else "Este tratamiento no está implementado en el motor.", "Consultar ficha del BIC, PEMP e instrumento aplicable." if heritage else "Consultar norma específica con SDP.", "IDPC" if heritage else "SDP")))
    d["figuras"] = figures
    # Trace entries describe the same variables: annotate, but do not double-count.
    for step in d.get("formula_trace", []):
        view = {"valor": step.get("resultado"), "nota": step.get("nota") or step.get("error") or ""}
        meta = _classify("trace", view, trat)
        if _finite(step.get("resultado")):
            meta = _status("derivado", "Resultado de la operación indicada en este paso del cálculo.", "Verificar entradas, expresión y fuente del paso.", "arquitecto")
        step.update(meta)
    counts = Counter(f["estado"] for f in figures)
    d["resumen_estados"] = {"total": len(figures), **{e.value: counts[e.value] for e in Estado}}
    d["version_estados"] = 1
    return d
