import math

from frecuencias.optimizacion.parametros import ParametrosHora

Z_P90 = 1.2815515655446004
RAIZ_DOS_PI = math.sqrt(2 * math.pi)
VARIACIONES_PESOS = {"costo_operativo": "peso_costo", "tiempo_espera": "peso_espera",
                     "sobreocupacion": "peso_sobreocupacion", "desviacion_frecuencia": "peso_desviacion"}
FACTORES_SENSIBILIDAD = (0.5, 2.0)
ALTERNATIVAS = 3


def _densidad(z: float) -> float:
    return math.exp(-0.5 * z * z) / RAIZ_DOS_PI


def _acumulada(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def exceso_esperado(media: float, desvio: float, capacidad: float) -> float:
    if desvio <= 0:
        return max(media - capacidad, 0.0)
    z = (capacidad - media) / desvio
    return desvio * _densidad(z) + (media - capacidad) * (1 - _acumulada(z))


def desvio_demanda(demanda: float, demanda_alta: float) -> float:
    return max(demanda_alta - demanda, 0.0) / Z_P90


def componentes(buses: int, demanda: float, desvio: float, p: ParametrosHora, buses_actual: float | None) -> dict:
    capacidad_objetivo = buses * p.capacidad * p.ocupacion_objetivo
    return {
        "costo_operativo": round(buses * p.tiempo_ciclo_min / 60, 4),
        "tiempo_espera": round(demanda * 0.5 / buses, 4),
        "sobreocupacion": round(exceso_esperado(demanda * p.factor_carga, desvio * p.factor_carga,
                                                capacidad_objetivo), 4),
        "desviacion_frecuencia": round(abs(buses - buses_actual), 4) if buses_actual is not None else 0.0,
    }


def costo(c: dict, p: ParametrosHora) -> float:
    return round(sum(getattr(p, peso) * c[nombre] for nombre, peso in VARIACIONES_PESOS.items()), 4)


def _mejor(candidatos, demanda: float, desvio: float, p: ParametrosHora, buses_actual: float | None) -> int:
    return min(candidatos, key=lambda b: (costo(componentes(b, demanda, desvio, p, buses_actual), p), b))


def _con_peso(p: ParametrosHora, peso: str, factor: float) -> ParametrosHora:
    return ParametrosHora(**{**p.como_dict(), peso: getattr(p, peso) * factor})


def optimizar(demanda: float, demanda_alta: float, p: ParametrosHora, buses_actual: float | None = None,
              tope_flota: int | None = None) -> dict:
    desvio = desvio_demanda(demanda, demanda_alta)
    minimo_servicio = math.ceil(60 / p.headway_maximo)
    minimo_capacidad = math.ceil(demanda * p.factor_carga / (p.capacidad * p.ocupacion_critica)) if demanda > 0 else 0
    maximo = max(math.floor(60 / p.headway_minimo), 1)
    piso = max(minimo_servicio, minimo_capacidad, 1)
    motivos = []
    if piso > maximo:
        motivos.append(f"La demanda exige {piso} buses por hora y el headway minimo de {p.headway_minimo:g} min "
                       f"solo permite {maximo}")
        candidatos = [maximo]
    else:
        candidatos = list(range(piso, maximo + 1))
    factibles = [b for b in candidatos if tope_flota is None or b <= tope_flota]
    flota_insuficiente = not factibles
    if flota_insuficiente:
        factibles = candidatos[:1]
    buses = _mejor(factibles, demanda, desvio, p, buses_actual)
    libre = _mejor(range(1, maximo + 1), demanda, desvio, p, buses_actual)
    if piso > maximo:
        limitado_por = "headway_minimo"
    elif buses > libre:
        limitado_por = "servicio_minimo" if buses == minimo_servicio else "capacidad_critica"
    elif buses < libre:
        limitado_por = "flota"
    else:
        limitado_por = "funcion_objetivo"
    evaluados = sorted(factibles, key=lambda b: (costo(componentes(b, demanda, desvio, p, buses_actual), p), b))
    sensibilidad = {
        f"{nombre}_x{factor:g}": _mejor(factibles, demanda, desvio, _con_peso(p, peso, factor), buses_actual)
        for nombre, peso in VARIACIONES_PESOS.items() for factor in FACTORES_SENSIBILIDAD
    }
    elegido = componentes(buses, demanda, desvio, p, buses_actual)
    return {
        "buses": int(buses),
        "limitado_por": limitado_por,
        "motivos": motivos,
        "flota_insuficiente": flota_insuficiente,
        "metodo": "busqueda_exhaustiva_entera",
        "componentes": elegido,
        "costo_total": costo(elegido, p),
        "pesos": {nombre: getattr(p, peso) for nombre, peso in VARIACIONES_PESOS.items()},
        "restricciones": {"minimo_servicio": minimo_servicio, "minimo_capacidad_critica": minimo_capacidad,
                          "maximo_por_headway": maximo, "tope_flota": tope_flota},
        "demanda": {"media": round(demanda, 2), "desvio": round(desvio, 2)},
        "alternativas": [{"buses": int(b), "costo_total": costo(componentes(b, demanda, desvio, p, buses_actual), p)}
                         for b in evaluados[:ALTERNATIVAS]],
        "sensibilidad_pesos": sensibilidad,
    }
