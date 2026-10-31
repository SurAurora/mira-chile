# Instrucciones para agentes

## Antes de trabajar

1. Leer por completo `REQUERIMIENTOS.md` antes de investigar, programar o modificar el catálogo.
2. Revisar los archivos existentes del proyecto y conservar el trabajo previo del usuario.
3. Consultar como antecedente la guía indicada en `REQUERIMIENTOS.md`, pero verificar nuevamente toda información que pueda haber cambiado.

## Reglas obligatorias

- Mantener el alcance geográfico, las variables, la granularidad de filas y las 13 columnas definidos en `REQUERIMIENTOS.md`.
- El entregable principal es un catálogo de disponibilidad, no una copia consolidada de las observaciones.
- No inventar ni inferir metadatos ausentes. Usar `No informado` o `No aplica` conforme a las definiciones del proyecto.
- Respaldar cada metadato con una fuente oficial o evidencia reproducible y registrar la fecha de consulta.
- Tratar como información cambiante la disponibilidad de portales, estaciones, variables y fechas; comprobarla antes de incorporarla.
- Diferenciar la institución responsable, la red de estaciones y el portal distribuidor cuando corresponda.
- Para `http://walker.dgf.uchile.cl/Mediciones/` y `https://eolico.minenergia.cl/mediciones_dmc/`, usar `MINENERGIA` en `fuente_institucion`. El texto `mediciones_dmc` es parte de la ruta del portal y no autoriza a atribuir esas series a DMC. Comparar ambas publicaciones antes de deduplicar; no asumir que contienen los mismos datos.
- Crear una fila independiente para cada combinación estación–ID–fuente–variable–altura–frecuencia.
- No fusionar estaciones o períodos si existen cambios de ubicación, identificador, instrumentación o altura que alteren la interpretación.
- No confundir frecuencia de muestreo con frecuencia de transmisión, publicación o agregación.
- Conservar nombres y códigos originales en la documentación, aunque el CSV use nombres normalizados.
- No guardar credenciales, claves API, tokens ni secretos en archivos, comandos registrados o código.
- Respetar las condiciones de uso de cada fuente y evitar consultas agresivas; emplear pausas, reintentos y caché cuando corresponda.
- No declarar que una fuente carece de datos basándose únicamente en un enlace fallido o una búsqueda parcial.

## Forma de trabajo

- Priorizar fuentes oficiales y documentación primaria.
- Documentar cada fuente en un `README.md` reproducible y referenciarlo desde `catalogo_estaciones.csv` mediante una ruta relativa.
- Guardar evidencia y metadatos originales separados de los productos derivados.
- Diseñar scripts reanudables, con validación de respuestas y registro de fallos.
- Hacer cambios pequeños y verificables; no sobrescribir trabajo ajeno sin necesidad.
- Si hay ambigüedad que cambie materialmente el alcance o el formato del producto, detenerse y consultarla con el usuario.

## Validación antes de entregar

- Confirmar que el CSV esté codificado en UTF-8, use coma como separador y contenga exactamente las 13 columnas requeridas en el orden establecido.
- Comprobar coordenadas, tipos de variable, formatos de fecha, valores de frecuencia y duplicados.
- Verificar que toda ruta de `readme_descarga` exista.
- Revisar que cada primera fecha provenga de datos comprobados y que `datos_disponibles_actualmente` se base en la ventana de siete días definida en `REQUERIMIENTOS.md`.
- Informar con claridad las fuentes completadas, los vacíos, los accesos bloqueados y las decisiones pendientes.

Si alguna instrucción de este archivo entra en conflicto con una solicitud explícita posterior del usuario, seguir la solicitud más reciente y actualizar `REQUERIMIENTOS.md` si el cambio redefine el proyecto.
