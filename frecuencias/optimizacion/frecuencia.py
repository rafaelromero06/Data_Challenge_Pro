import math

from frecuencias.optimizacion.objetivo import optimizar
from frecuencias.optimizacion.parametros import ParametrosHora

OCUPACIONES_SENSIBILIDAD = (0.65, 0.75, 0.85)


def buses_necesarios(carga: float, capacidad: float, ocupacion: float) -> int:
    return math.ceil(carga / (capacidad * ocupacion)) if carga > 0 else 0


def vehiculos_requeridos(buses: int, p: ParametrosHora, despachos_en_ruta: int | None) -> tuple[int, str]:
    if despachos_en_ruta is None:
        return math.ceil(p.tiempo_ciclo_min * buses / 60), "ciclo_sobre_headway"
    return despachos_en_ruta + buses, "despachos_en_ventana_de_ciclo"


def tope_por_flota(p: ParametrosHora, despachos_en_ruta: int | None) -> int | None:
    if p.flota_disponible is None:
        return None
    if despachos_en_ruta is None:
        return math.floor(p.flota_disponible * 60 / p.tiempo_ciclo_min)
    return p.flota_disponible - despachos_en_ruta


def recomendar(demanda_hora: float, demanda_hora_alta: float | None, p: ParametrosHora,
               buses_actual: float | None = None, despachos_en_ruta: int | None = None) -> dict:
    demanda_alta = max(demanda_hora, demanda_hora_alta if demanda_hora_alta is not None else demanda_hora)
    carga = demanda_hora * p.factor_carga
    carga_alta = demanda_alta * p.factor_carga
    capacidad_efectiva = p.capacidad * p.ocupacion_objetivo
    linea_base = max(buses_necesarios(carga_alta, p.capacidad, p.ocupacion_objetivo),
                     math.ceil(60 / p.headway_maximo), 1)
    objetivo = optimizar(demanda_hora, demanda_alta, p, buses_actual, tope_por_flota(p, despachos_en_ruta))
    buses, limitado_por = objetivo["buses"], objetivo["limitado_por"]
    motivos = list(objetivo["motivos"])
    headway = 60 / buses
    vehiculos, metodo_vehiculos = vehiculos_requeridos(buses, p, despachos_en_ruta)
    if p.flota_disponible is not None and vehiculos > p.flota_disponible:
        motivos.append(f"Se requieren {vehiculos} vehiculos y la flota disponible es {p.flota_disponible}")
    ocupacion_actual = None
    if buses_actual:
        ocupacion_actual = round(carga / (buses_actual * p.capacidad), 4)
    return {
        "demanda_esperada": round(demanda_hora, 2),
        "carga_maxima_esperada": round(carga_alta, 2),
        "capacidad_efectiva": round(capacidad_efectiva, 2),
        "buses_por_hora": int(buses),
        "headway_min": round(headway, 2),
        "vehiculos_requeridos": int(vehiculos),
        "ocupacion_esperada": round(carga / (buses * p.capacidad), 4),
        "buses_por_hora_actual": buses_actual,
        "headway_actual_min": round(60 / buses_actual, 2) if buses_actual else None,
        "ocupacion_actual": ocupacion_actual,
        "flota_disponible": p.flota_disponible,
        "tiempo_ciclo_min": p.tiempo_ciclo_min,
        "viable": not motivos,
        "motivo_no_viable": "; ".join(motivos) or None,
        "limitado_por": limitado_por,
        "metodo_vehiculos": metodo_vehiculos,
        "sensibilidad": sensibilidad(demanda_hora, demanda_alta, p),
        "linea_base": {"buses_por_hora": int(min(linea_base, math.floor(60 / p.headway_minimo))),
                       "metodo": "ceil(carga_p90 / capacidad_efectiva), minimo por headway maximo"},
        "objetivo": objetivo,
    }


def sensibilidad(demanda_hora: float, demanda_hora_alta: float, p: ParametrosHora) -> dict:
    minimo = math.ceil(60 / p.headway_maximo)
    resultado = {}
    for ocupacion in OCUPACIONES_SENSIBILIDAD:
        for nombre, demanda in (("puntual", demanda_hora), ("alta", demanda_hora_alta)):
            buses = max(buses_necesarios(demanda * p.factor_carga, p.capacidad, ocupacion), minimo, 1)
            resultado[f"ocupacion_{int(ocupacion * 100)}_{nombre}"] = {
                "buses_por_hora": buses, "headway_min": round(60 / buses, 2),
                "vehiculos": math.ceil(p.tiempo_ciclo_min * buses / 60)}
    return resultado
