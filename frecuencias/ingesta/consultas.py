QUERY_VIAJES = """
    SELECT idprogramacion, idruta AS id_ruta, idvehiculo, fechasalida, fecharegistro,
           fechainicio, fechafin, cancelada, finalizado, numeropasajeros, numerobajadas,
           distancia_recorrida, excedeplazosalida
    FROM public.progvehiculos
"""

QUERY_CONTEO = """
    SELECT p.idruta                                        AS id_ruta,
           date_bin('15 minutes', c.fecha, TIMESTAMP '2000-01-01') AS ts,
           SUM(GREATEST(c.cantidad_subida, 0))             AS subidas,
           SUM(GREATEST(c.cantidad_bajada, 0))             AS bajadas,
           COUNT(DISTINCT c.idprogramacion)                AS nro_viajes,
           COUNT(DISTINCT c.idvehiculo)                    AS nro_vehiculos,
           COUNT(*)                                        AS lecturas,
           SUM(CASE WHEN c.cantidad_subida < 0 THEN 1 ELSE 0 END) AS lecturas_negativas,
           MAX(c.cantidad_subida)                          AS max_subida_lectura
    FROM public.conteo_pasajeros_destino c
    JOIN public.progvehiculos p ON p.idprogramacion = c.idprogramacion
    WHERE c.idprogramacion = ANY(:ids) AND p.idprogramacion = ANY(:ids)
    GROUP BY 1, 2
"""

QUERY_ESTACIONALIDADES = """
    SELECT e.estacionalidad, ea.id_estacionalidad, ea.fecha_inicio, ea.fecha_fin
    FROM public.estacionalidades_anio ea
    JOIN public.estacionalidades e USING (id_estacionalidad)
    WHERE e.estacionalidad <> 'DIAS_CORRIENTES'
"""

QUERY_NOVEDADES = """
    SELECT nf.fecha, nf.hora_inicio, nf.hora_fin, nf.porcentaje_impacto, n.nombre AS evento
    FROM public.novedades_viales_fecha nf
    JOIN public.novedades_viales n USING (id_novedad_vial)
"""

QUERY_RUTAS = "SELECT idruta AS id_ruta, nombre FROM public.rutas"

QUERY_VIAJES_RANGO = QUERY_VIAJES + """
    WHERE fechasalida >= :desde AND fechasalida < :hasta
"""

QUERY_SEGMENTOS = """
    SELECT rs.id AS id_segmento, rs.id_config, rs.idruta AS id_ruta, rs.nro_segmento, rs.longitud_m,
           ST_AsText(rs.geom_segmento) AS wkt
    FROM public.rutas_segmentos rs
    JOIN public.rutas_segmentos_config c ON c.id = rs.id_config AND c.activo
    WHERE rs.activo AND rs.idruta = ANY(:rutas)
    ORDER BY rs.idruta, rs.nro_segmento
"""

QUERY_LECTURAS_SEGMENTO = """
    SELECT c.id, c.idprogramacion, c.fecha, c.latitud, c.longitud,
           GREATEST(c.cantidad_subida, 0) AS subidas, GREATEST(c.cantidad_bajada, 0) AS bajadas,
           c.acumulada_subida, c.acumulada_bajada
    FROM public.conteo_pasajeros_destino c
    WHERE c.idprogramacion = ANY(:ids) AND (c.cantidad_subida > 0 OR c.cantidad_bajada > 0)
"""

QUERY_TIMBRADAS_SEGMENTOS = """
    SELECT ts.id_viaje AS idprogramacion, ts.id_segmento, SUM(ts.subidas) AS subidas, SUM(ts.bajadas) AS bajadas
    FROM public.timbradas_segmentos ts
    WHERE ts.id_viaje = ANY(:ids)
    GROUP BY 1, 2
"""
