from copy import deepcopy
import json
from pathlib import Path

import pytest
import calc
from figure_status import annotate_result, Estado
from test_consolidacion_estimate import _lookup, _metrics, _derive, KENNEDY_RING, SUBA_RING, USAQUEN_RING


def result(metrics, treatment="CONSOLIDACION", **kwargs):
    return dict(tratamiento=treatment, metrics=metrics, consulta={"fecha":"2026-09-14"},
                lote={"area_m2":{"valor":91.6}}, antejardin={"dimension_m":None,"confianza":"requiere_input","error":"Capa 22 sin dato"},
                parking={"min_pct":0,"max_pct":15,"adicional_pct":10}, **kwargs)


@pytest.mark.parametrize('ring,code,area,lng,lat,floors,posterior,ant,snap',[
    (KENNEDY_RING,'004514061017',91.6,-74.1499994,4.628,5,5,3.5,False),
    (SUBA_RING,'009212014064',685.1,-74.08303,4.74499,3,5,None,False),
    (USAQUEN_RING,'008403014001',450.8,-74.03618,4.70476,3,4,0,True),
])
def test_real_lot_estimates_keep_values_and_legacy_null(ring,code,area,lng,lat,floors,posterior,ant,snap):
    lu=_lookup(code,area,ring,lng,lat,snap=snap)
    m=_metrics(floors,posterior)
    m['area_construible_estimada']=_derive(lu,m,{'dimension_m':ant,'confianza':'alta' if ant is not None else 'requiere_input'})
    old=deepcopy(m)
    d=annotate_result(result(m))
    assert m==old  # no mutation of cached input
    for key,obj in old.items():
        for k,v in obj.items(): assert d['metrics'][key][k]==v
    assert d['metrics']['area_construible_max_m2']['valor'] is None
    assert d['metrics']['area_construible_max_m2']['estado']=='no_aplica'
    assert d['metrics']['area_construible_estimada']['estado']=='derivado'
    if snap: assert d['metrics']['area_construible_estimada']['confianza']=='baja'


def test_renovacion_and_heritage_unchanged():
    m,_=calc._calc_renovacion_urbana({'lote':{'area_m2':{'valor':87.7}}},87.7,[],lambda:1)
    d=annotate_result(result(m,'RENOVACION'))
    assert d['metrics']['area_construible_max_sin_manzana_completa_m2']['valor']==438.5
    assert d['metrics']['altura_maxima_pisos']['estado']=='no_aplica'
    assert d['metrics']['aislamiento_posterior_tabla']['estado']=='insuficiente'
    h=annotate_result(result(None,'CONSERVACION',binding_constraint='conservacion_no_soportado'))
    assert h['metrics'] is None
    assert next(f for f in h['figuras'] if f['id']=='edificabilidad')['estado']=='requiere_concepto'


def test_contract_zero_missing_error_and_no_fabricated_estimate():
    d=annotate_result(result({'area_construible_estimada':{'valor_m2':None,'rango_m2':None},'altura_base_pisos':{'valor':float('nan')}}))
    assert d['antejardin']['estado']=='insuficiente'  # InputRequired isn't an engine error
    assert d['metrics']['area_construible_estimada']['estado']=='insuficiente'
    assert d['metrics']['altura_base_pisos']['estado']=='error'
    f=next(f for f in d['figuras'] if f['id']=='parking.min_pct')
    assert f['valor']==0 and f['estado']=='resuelto'
    states={e.value for e in Estado}
    assert len(d['figuras'])==len({f['id'] for f in d['figuras']})
    assert sum(d['resumen_estados'][s] for s in states)==d['resumen_estados']['total']
    for f in d['figuras']:
        assert f['estado'] in states
        assert f['motivo']
        assert 'que_se_necesita' in f and 'quien_lo_resuelve' in f


def test_corpus_uses_verified_originals_not_sample_prompt():
    corpus=json.loads(Path('static/article-corpus.json').read_text())['articulos']
    assert 'índices de ocupación y construcción son resultantes' in corpus['555:310']['texto']
    assert 'aislamiento posterior mínimo será de 3,00 m' not in json.dumps(corpus)
    for a in corpus.values():
        assert a['texto'] and a['url'].startswith('https://www.alcaldiabogota.gov.co/')
        assert a['fecha_cotejo']=='2026-09-14'


def test_indices_reference_and_formula_states_preserve_legacy_fields():
    raw=result({'aislamiento_entre_edificaciones':{'formula':'max(altura / 3, 4)'}})
    raw['referencia_obligaciones_urbanisticas_2026']={'estado':'referencia_no_liquidacion','valor_tope_cop_m2':571000}
    lookup={'edificabilidad':{'indice_construccion':{'valor':None,'nota':'IC resultante'},
                            'indice_ocupacion':{'valor':None,'nota':'IO resultante'},
                            'subdivision_permitida':{'valor':True}}}
    d=annotate_result(raw,lookup)
    assert d['edificabilidad']['indice_construccion']['estado']=='no_aplica'
    assert d['metrics']['aislamiento_entre_edificaciones']['estado']=='insuficiente'
    assert d['referencia_obligaciones_urbanisticas_2026']['estado']=='referencia_no_liquidacion'
    assert d['edificabilidad']['subdivision_permitida']['valor'] is True
    assert d['edificabilidad']['subdivision_permitida']['estado']=='insuficiente'
    assert 'estado' not in lookup['edificabilidad']['indice_construccion']


def test_threshold_is_not_mistaken_for_unconditional_exemption():
    d=annotate_result(result({'aislamiento_lateral_umbral_m':{'valor':11.4,'nota':'No exigido si altura ≤ 11,40 m.'}},'RENOVACION'))
    assert d['metrics']['aislamiento_lateral_umbral_m']['estado']=='resuelto'
    f=next(f for f in d['figuras'] if f['id']=='metrics.aislamiento_lateral_umbral_m')
    assert f['articulo_id']=='466:3.2.a'


def test_special_decree_does_not_receive_article310_quote():
    d=annotate_result(result({'altura_base_pisos':{'valor':5,'articulo':'D.253/2026 Anexo 36.1'}}))
    f=next(f for f in d['figuras'] if f['id']=='metrics.altura_base_pisos')
    assert f['articulo_id'] is None
    assert f['valor']==5


def test_exact_consolidacion_contract_and_allowed_resolvers():
    metrics={
        'area_construible_max_m2':{'valor':None}, 'planta_maxima_m2':{'valor':None},
        'altura_base_pisos':{'valor':5,'confianza':'alta'},
        'altura_con_bonus_pisos':{'valor':None,'nota':'No aplica para este lote.'},
        'retroceso_fachada_A_m':{'valor':42.1,'confianza':'media'},
        'area_construible_estimada':{'valor_m2':204,'metodo':'huella_x_pisos'},
    }
    raw=result(metrics, cobertura_restricciones={'bic':'consultado','cerros_orientales':'sin_fuente_configurada'})
    d=annotate_result(raw)
    assert all('estado' in obj for obj in d['metrics'].values())
    reason='El Decreto 555 no fija índice de construcción ni de ocupación numéricamente en tratamiento de Consolidación.'
    assert d['metrics']['area_construible_max_m2']['motivo']==reason
    assert d['metrics']['planta_maxima_m2']['motivo']==reason
    assert d['metrics']['altura_base_pisos']['estado']=='resuelto'
    assert d['metrics']['retroceso_fachada_A_m']['estado']=='insuficiente'
    assert d['metrics']['area_construible_estimada']['estado']=='derivado'
    hazard=d['cobertura_restricciones_detalle']['cerros_orientales']
    assert hazard['motivo']=='No hay fuente automatizada configurada para esta verificación.'
    allowed={'SDP','curaduría','IDPC','topógrafo','profesional',None}
    for obj in list(d['metrics'].values())+list(d['cobertura_restricciones_detalle'].values()):
        assert obj['estado'] in {e.value for e in Estado}
        assert obj['quien_lo_resuelve'] in allowed
