import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Ruta(BaseModel):
    id_ruta: int
    nombre: str | None
    primera_franja: datetime
    ultima_franja: datetime
    franjas_con_servicio: int


class Intervalo(BaseModel):
    lower: float
    upper: float
    level: float


class RecomendacionResumida(BaseModel):
    headwayMinutes: float
    busesPerHour: int
    vehiclesRequired: int
    viable: bool


class PrediccionRespuesta(BaseModel):
    predictionId: str | None = Field(description="Identificador de la prediccion guardada en ai_frecuencias")
    routeId: int
    generatedAt: datetime
    referenceTime: datetime = Field(description="Ultima franja de 15 min cuyo dato se da por conocido (hora local)")
    targetSlot: datetime = Field(description="Inicio de la franja de 15 min que se predice (hora local)")
    horizonMinutes: int
    modelHorizonMinutes: int
    predictedPassengers: float
    confidenceInterval: Intervalo
    intervalCalibrated: bool = Field(True, description="False en franjas sin servicio habitual: el intervalo es "
                                                       "la prediccion puntual mas o menos el ajuste conforme")
    actualPassengers: float | None = Field(description="Valor observado, solo si la franja ya esta en el historico")
    baselinePassengers: float | None = Field(description="Promedio de la misma franja en las 4 semanas previas")
    usualService: bool
    modelVersion: str
    featureVersion: str
    latencyMs: float
    recommendation: RecomendacionResumida | None
    explanation: list[str]
    explanationDetail: dict


class RespuestaEjecucionPrediccion(BaseModel):
    routeId: int
    routeName: str | None
    referenceTime: datetime
    executionId: int | None
    saved: bool
    predictions: list[PrediccionRespuesta]
    recommendations: list[dict]
    alerts: dict


class Pagina(BaseModel):
    items: list[dict]
    page: int
    size: int
    total: int


class PeticionPrediccion(BaseModel):
    id_ruta: int
    referencia: datetime | None = Field(None, description="Hora local de referencia; por defecto la ultima franja con datos")
    guardar: bool = Field(True, description="Guarda predicciones, recomendaciones y alertas en ai_frecuencias")


Usuario = Annotated[str | None, Field(min_length=1, description="Solo si la autenticacion esta desactivada; "
                                                                 "si no, se registra el nombre de la clave")]


class PeticionEstadoAlerta(BaseModel):
    estado: Literal["reconocida", "asignada", "cerrada", "descartada"] | None = None
    usuario: Usuario = None
    comentario: str | None = None
    usuario_asignado: str | None = None


class PeticionEstadoAlertas(PeticionEstadoAlerta):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class PeticionEjecucion(BaseModel):
    origen: Literal["base", "archivos"] = "base"
    dias: int = Field(1, ge=1, le=31)
    reentrenar: Literal["auto", "siempre", "nunca"] = "auto"


class PeticionActivacion(BaseModel):
    usuario: Usuario = None


class PeticionParametro(BaseModel):
    valor: float | None
    usuario: Usuario = None
    id_ruta: int | None = None
    unidad: str | None = None
    descripcion: str | None = None
    validado: bool = False
