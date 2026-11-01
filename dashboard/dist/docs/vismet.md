# VISMET — Visualizador Meteorológico de CR2

## Instituciones y alcance

- Portal consultado: <https://vismet.cr2.cl/>
- Descripción del propio CR2: <https://dataclima.cr2.cl/?platform_geographicDomain=3>
- Fecha del sondeo: 2026-11-01.
- Área: Arica y Parinacota, Tarapacá, Antofagasta y Atacama, determinada por
  coordenadas y los polígonos oficiales del proyecto.

Se conserva la excepción cartográfica de las estaciones marítimas del dashboard:
un punto publicado para SERVIMET o una Capitanía de Puerto puede asignarse a la
costa regional más próxima si está a menos de 20 km del polígono. En este sondeo
incluye Capitanía de Puerto Chañaral, cuya posición cae 33 metros fuera de la
costa generalizada de Atacama. Se mantienen sus coordenadas originales.

VISMET es un distribuidor que reúne varias redes. En el dashboard se identifica
como `VISMET / CR2`; `fuente_institucion` conserva la organización atribuida por
su API: DGA, DMC, AGROMET, CEAZA o REDMETEO. Cuando el nombre publicado identifica
expresamente `(SERVIMET)`, se consigna `SERVIMET` como institución y se conserva
`redmeteo` como organización distribuidora en la tabla de identificadores.
La etiqueta AGROMET no permite atribuir automáticamente todas sus estaciones a
INIA; el enlace institucional de VISMET para ese grupo es `www.agromet.cl`.

## Resultado del corte

Se verificaron 1.409 ventanas semanales consecutivas, además de las series
individuales de inicio y de los últimos siete días completos. El catálogo
contiene **355 estaciones y 687 series**: 346 de precipitación y 341 de
temperatura. De ellas, 621 tienen datos en la ventana reciente y 66 sólo tienen
datos históricos fuera de esa ventana. La primera fecha encontrada en el área
es `2001-11-24T04:00:00+00:00`; cada fila conserva su propia primera fecha.

El inventario geográfico tiene 389 estaciones candidatas, incluidas las
históricas y la excepción costera. Se descartaron 91 combinaciones
estación-variable sin valores válidos comprobados. El inventario nacional de
consultas históricas contiene además 25 registros sin coordenadas, que no se
asignan al área del proyecto sin metadatos geográficos verificables.

## Identificadores

El campo ID conserva el código nacional y añade entre corchetes el ID interno
del portal: por ejemplo, `01000005-K [VISMET-1]` para Visviri. Así se puede buscar
por el código nacional y distinguir el registro distribuido por VISMET. Ambos
identificadores y los nombres oficiales se conservan también en
`identificadores_estaciones.csv`. No se fusionan automáticamente con estaciones
del mismo nombre o código en otros catálogos: las coberturas, ubicaciones
publicadas y productos pueden diferir. El total del dashboard cuenta registros
por portal/red y puede incluir un sitio físico en más de una red.

El inventario incluye la unión de las estaciones publicadas en consultas
actuales y en consultas históricas. Algunas estaciones retiradas desaparecen
del mapa actual pero conservan series históricas. Se mantienen como registros
independientes si el portal les asigna un ID diferente, aunque coincida su
código nacional. Si VISMET no publica coordenadas, el caso queda documentado
en la evidencia local y no se le asigna una región por su nombre o código.

## Variables, alturas y resolución

| ID de variable VISMET | Nombre publicado | Nombre normalizado | Unidad |
|---|---|---|---|
| 1 | Precipitacion Horaria | Precipitación | mm |
| 2 | Temperatura | Temperatura | °C |

El producto de serie temporal está en una grilla de **60 minutos**. Esta es la
resolución publicada por VISMET, que puede agregar datos originales más finos;
no acredita una frecuencia instrumental de 60 minutos. No se crean filas de
otras variables a partir de las capacidades de las estaciones originales.

La vista `Actual` usa `api/raw-measure/by-measure-type/{variable}/last`, que
publica el último valor por estación. Un último valor aislado no permite
catalogar una serie histórica ni determinar su frecuencia. La consulta de
prueba a una ruta de serie cruda por estación devolvió HTTP 401 con el acceso
público del cliente; no se da por inexistente esa información ni se incluyen
frecuencias finas sin comprobarlas. El catálogo de este sondeo corresponde al
producto horario abierto.

La altura del sensor no está publicada en los metadatos usados. Se registra
`No informado`. La altitud sobre el nivel del mar es la de `station.altitude`;
no se utiliza como altura del sensor.

Los campos `timestamp` y `lastUsedRawTimestamp` son épocas Unix. Se representan
en ISO 8601 con `+00:00` (UTC), sin atribuir a la estación el huso local del
navegador ni reinterpretar el horario de la institución original.

## Consultas reproducibles

Se consulta el mismo servicio público usado por el cliente JavaScript del
portal. No requiere una cuenta ni una clave personal. El encabezado `ckey`
se calcula mediante el algoritmo `hashUrl` publicado en ese cliente: es una
suma de comprobación de la ruta, no una credencial del usuario.

Rutas del cliente oficial:

```text
/api/configuration/
api/measure/by-measure-type/{variable}/by-timestamp/{fin_unix}/by-interval/{horas}/sum
api/measure/by-measure-type/{variable}/by-timestamp/{fin_unix}/by-interval/{horas}/mean
api/measure/by-station/{id_interno}/by-measure-type/{variable}/by-timestamp/{fin_unix}/by-interval/{horas}
```

La API rechaza intervalos mayores de **168 horas**. El descargador respeta ese
límite, conserva las respuestas JSON comprimidas y admite reanudación:

```bash
/home/alagos/.conda/envs/env_leftraru/bin/python \
  scripts/vismet_sondear_disponibilidad.py \
  --as-of 2026-11-01 --start 2000-01-01 --workers 2 --pause 0.3 --resume

python scripts/construir_catalogo_vismet.py
python scripts/construir_catalogo_general.py
```

Dependencias del sondeo: `requests`, `geopandas` y `shapely`, además de Python
con `zoneinfo`. Se usa el shapefile de regiones ya definido en el proyecto.

Para descargar las observaciones horarias de una estación en otro momento,
usar el ID interno de la tabla de identificadores. Por ejemplo, Visviri tiene
ID interno `1` y precipitación tiene ID de variable `1`:

```bash
/home/alagos/.conda/envs/env_leftraru/bin/python \
  scripts/vismet_descargar_serie.py --station 1 --variable 1 \
  --from 2026-10-25T03:00:00+00:00 --to 2026-11-01T03:00:00+00:00 \
  --output datos_originales/vismet/visviri_precipitacion_20261025_20261031.json
```

El final es exclusivo. Las fechas sin zona se interpretan UTC. El archivo
conserva los valores y las banderas originales, incluidos los registros sin
respaldo; las reglas de validación se explican más abajo. El descargador divide
automáticamente los períodos largos, reusa la caché y no sobrescribe una salida
existente. Estas observaciones son una descarga opcional, separada del catálogo.

Las respuestas y el estado se conservan en `datos_originales/vismet/`, excluido
de Git. Para un nuevo corte de disponibilidad, se debe usar otro directorio
`--raw`, porque el estado reanudado conserva el mismo `--as-of`.
El constructor también admite `--raw` para seleccionar ese nuevo sondeo; las
fechas y el resumen de esta guía deben actualizarse junto con el nuevo corte.

## Primera fecha comprobada

CR2 describe la cobertura de VISMET desde **2000 al presente**. Se usa ese
inicio documentado y se comprueba también una ventana inmediatamente anterior;
si ésta contiene datos de estaciones del área seleccionada, el script exige
ampliar el sondeo. Se encontraron temperaturas anteriores a 2000 en otras
regiones del país; no se interpreta ese inicio documentado como un límite
universal de todas las series de VISMET.

Se recorren semanas consecutivas desde ese inicio hasta el corte, con una hora
de solapamiento para no dejar huecos entre ventanas. Cada primera detección en
un mapa semanal se verifica mediante la serie horaria individual de esa misma
estación y variable. Se guarda la primera fecha con un valor finito, dentro de
los rangos básicos y con respaldo de observaciones originales. La búsqueda no
presupone que la serie sea continua y no usa búsquedas binarias sobre la mera
presencia de valores en una fecha aislada.

Las fechas describen el primer producto horario válido encontrado en VISMET,
no la instalación de la estación ni el inicio de su archivo institucional.

## Disponibilidad actual y validación

La ventana de este corte comprende **2026-10-25 a 2026-10-31**, días calendario
completos de `America/Santiago`; en UTC corresponde al intervalo desde
`2026-10-25T03:00:00+00:00` hasta `2026-11-01T03:00:00+00:00`, con extremo final
excluido. Cada combinación se consulta en su serie individual.

Para aceptar un registro se exige:

- `value` numérico y finito;
- `confidence > 0`;
- `lastUsedRawTimestamp > 0`;
- precipitación entre 0 y 500 mm por hora, o temperatura entre -80 y 60 °C.

VISMET puede devolver **ceros con `confidence = 0` y
`lastUsedRawTimestamp = 0`**. No se cuentan como mediciones ni sirven para
marcar disponibilidad. Un cero con respaldo sí es válido, especialmente en
precipitación. La confianza se usa como indicador de respaldo/cobertura y no
como garantía de calidad climatológica. Los rangos básicos tampoco sustituyen
una validación científica.

`Sí` requiere al menos un registro válido en la ventana; `No` significa que la
respuesta fue correcta pero no se encontró ninguno. Si falla una solicitud, el
sondeo se detiene conservando lo completado y el constructor no publica un
catálogo parcial.

El resumen de resultados, conteos y combinaciones sin valores históricos está
en `resumen_verificacion.json`.

## Aviso DGA del portal

La configuración pública contiene un texto antiguo sobre falta de acceso a
DGA. Durante esta consulta, `modal_alert_active` estaba en `false` y los
servicios devolvieron series DGA. La presencia de ese texto no se usa para
descartar estaciones: cada serie se verifica en las respuestas de datos.
