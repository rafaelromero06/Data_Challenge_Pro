from dataclasses import asdict, dataclass

FRANJAS = [(4, 6, "04-06"), (6, 9, "06-09"), (9, 12, "09-12"), (12, 15, "12-15"), (15, 18, "15-18"), (18, 21, "18-21")]
TIPOS_DIA = {0: "habil", 1: "sabado", 2: "dom_fest"}
HORA_INICIO_DIURNO = 5
HORA_FIN_DIURNO = 20
GENERALES = {
    "ocupacion_objetivo": 80.0,
    "ocupacion_objetivo_min": 65.0,
    "ocupacion_objetivo_max": 85.0,
    "ocupacion_critica": 90.0,
    "baja_utilizacion": 30.0,
    "espera_maxima": 5.0,
    "error_pronostico": 15.0,
    "datos_atrasados": 2.0,
    "capacidad_nominal": 50.0,
    "factor_carga": 0.4,
    "headway_minimo": 2.0,
    "headway_maximo:diurno": 10.0,
    "headway_maximo:nocturno": 15.0,
    "tiempo_ciclo": 270.0,
    "umbral_alta_demanda": 100.0,
    "tolerancia_frecuencia_buses": 2.0,
    "tolerancia_frecuencia_pct": 15.0,
    "peso_costo_operativo": 1.0,
    "peso_tiempo_espera": 0.1,
    "peso_sobreocupacion": 1.0,
    "peso_desviacion_frecuencia": 0.5,
    "despachos_fallidos_min": 2.0,
    "despachos_fallidos_pct": 25.0,
    "deriva_psi_max": 0.25,
    "cobertura_intervalo_min": 65.0,
    "volumen_predicciones_min": 50.0,
    "alertas_descartadas_max": 30.0,
}


def franja_de(hora: int) -> str:
    for inicio, fin, nombre in FRANJAS:
        if inicio <= hora < fin:
            return nombre
    return FRANJAS[0][2] if hora < FRANJAS[0][0] else FRANJAS[-1][2]


def tipo_dia_de(codigo: int) -> str:
    return TIPOS_DIA.get(int(codigo), "habil")


@dataclass(frozen=True)
class ParametrosHora:
    capacidad: float
    ocupacion_objetivo: float
    ocupacion_maxima: float
    ocupacion_critica: float
    baja_utilizacion: float
    factor_carga: float
    tiempo_ciclo_min: float
    flota_disponible: int | None
    headway_minimo: float
    headway_maximo: float
    franja: str
    tipo_dia: str
    umbral_alta_demanda: float = 1.0
    tolerancia_buses: float = 2.0
    tolerancia_pct: float = 0.15
    peso_costo: float = 1.0
    peso_espera: float = 0.1
    peso_sobreocupacion: float = 1.0
    peso_desviacion: float = 0.5

    def como_dict(self) -> dict:
        return asdict(self)


def _valor(valores: dict, *claves: str):
    for clave in claves:
        if valores.get(clave) is not None:
            return float(valores[clave])
    for clave in claves:
        if GENERALES.get(clave) is not None:
            return float(GENERALES[clave])
    return None


def valor(valores: dict, clave: str) -> float | None:
    return _valor(valores, clave)


def parametros_hora(valores: dict, hora: int, tipo_dia: int) -> ParametrosHora:
    franja = franja_de(hora)
    tipo = tipo_dia_de(tipo_dia)
    nocturno = tipo == "dom_fest" or hora >= HORA_FIN_DIURNO or hora < HORA_INICIO_DIURNO
    flota = _valor(valores, "flota_disponible")
    return ParametrosHora(
        capacidad=_valor(valores, "capacidad_nominal"),
        ocupacion_objetivo=_valor(valores, "ocupacion_objetivo") / 100,
        ocupacion_maxima=_valor(valores, "ocupacion_objetivo_max") / 100,
        ocupacion_critica=_valor(valores, "ocupacion_critica") / 100,
        baja_utilizacion=_valor(valores, "baja_utilizacion") / 100,
        factor_carga=_valor(valores, f"factor_carga:{franja}", "factor_carga"),
        tiempo_ciclo_min=_valor(valores, f"tiempo_ciclo:{tipo}:{franja}", "tiempo_ciclo"),
        flota_disponible=None if flota is None else int(flota),
        headway_minimo=_valor(valores, "headway_minimo"),
        headway_maximo=_valor(valores, "headway_maximo:nocturno" if nocturno else "headway_maximo:diurno"),
        franja=franja,
        tipo_dia=tipo,
        umbral_alta_demanda=_valor(valores, "umbral_alta_demanda") / 100,
        tolerancia_buses=_valor(valores, "tolerancia_frecuencia_buses"),
        tolerancia_pct=_valor(valores, "tolerancia_frecuencia_pct") / 100,
        peso_costo=_valor(valores, "peso_costo_operativo"),
        peso_espera=_valor(valores, "peso_tiempo_espera"),
        peso_sobreocupacion=_valor(valores, "peso_sobreocupacion"),
        peso_desviacion=_valor(valores, "peso_desviacion_frecuencia"),
    )
