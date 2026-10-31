# AGROMET — descarga mediante la vista pública de tabla

## Fuente y alcance

- Portal oficial: <https://agrometeorologia.cl/>
- Red distribuidora: Red Agrometeorológica de INIA (AGROMET).
- Regiones de este proyecto: Arica y Parinacota, Tarapacá, Antofagasta y Atacama.
- Primera revisión de este procedimiento: 2026-10-28.

AGROMET distribuye estaciones de INIA y de instituciones externas. El prefijo
`EXT-` no identifica una sola institución; la institución publicada en el nombre
y los metadatos de la estación debe conservarse al construir el catálogo.

## Cambio observado en el portal

El exportador CSV/Excel exige actualmente una verificación visual de seguridad y
un token temporal. El descargador histórico
`/home/alagos/fondef_polvo/analysis/project_scripts/agromet_downloader.py` ya no
puede exportar archivos automáticamente porque no envía esa verificación.

No se intenta resolver ni eludir el desafío. El script de este proyecto solicita
la opción pública `vista[]=tabla`, que no es una exportación CSV/Excel, conserva
el HTML original comprimido y produce un CSV derivado de la tabla.

## Variables de interés disponibles

| Código AGROMET | Variable normalizada |
|---|---|
| `TA_AVG` | Temperatura |
| `HR_AVG` | Humedad |
| `PP_SUM` | Precipitación |
| `PS_AVG` | Presión |
| `RD_AVG` | Radiación solar |
| `VV_AVG` | Intensidad o velocidad del viento |
| `VV_MAX` | Ráfaga del viento |
| `DV_AVG` | Dirección del viento |

La interfaz permite un máximo de seis variables por consulta. Por ello las ocho
variables se consultan en dos grupos. El intervalo más fino expuesto por este
formulario es horario (`hour`), equivalente a `60 min`; también ofrece productos
diarios y agregaciones más gruesas. La tabla observada el 2026-10-28 indicó
`UTC-4`, pero el huso se extrae y registra en cada consulta porque puede cambiar.

## Script

El script utilizado es:

`scripts/agromet_table_downloader.py`

Requiere únicamente Python 3 y su biblioteca estándar. Hace consultas
secuenciales, admite una pausa entre ellas, conserva el HTML original en
`html_gz/`, escribe la tabla derivada en `csv/` y registra cada intento en
`manifest.csv`.

### Guardar el catálogo vigente

```bash
python scripts/agromet_table_downloader.py \
  --snapshot datos_originales/agromet/catalogo_fuente
```

Las coordenadas, elevaciones e instituciones se obtienen del JSON público que
alimenta el mapa. Su ruta contiene un directorio temporal y se resuelve desde la
portada con:

```bash
python scripts/agromet_descargar_metadatos.py \
  --output datos_originales/agromet/metadatos_estaciones.csv \
  --raw-gz datos_originales/agromet/metadatos_mapa.json.gz
```

### Prueba de una estación

```bash
python scripts/agromet_table_downloader.py \
  --station INIA-81 \
  --variables TA_AVG HR_AVG PP_SUM PS_AVG RD_AVG VV_AVG \
  --interval hour --from 2025-01-01 --to 2025-01-02 \
  --chunk-days 2 --pause 2 \
  --output datos_originales/agromet/observaciones
```

El segundo grupo se consulta sustituyendo las variables por `VV_MAX DV_AVG`.

## Sondeo compacto para el catálogo

Como el producto final sólo requiere la primera fecha y si existen datos
actualmente, se utiliza preferentemente:

`scripts/agromet_sondear_disponibilidad.py`

Este script procesa la tabla diaria histórica en memoria y no conserva las
observaciones. Sólo escribe:

- `datos_originales/agromet/sondeo_disponibilidad.csv`, con una fila por
  estación y variable;
- archivos JSON de evidencia compacta en
  `datos_originales/agromet/evidencia_sondeo/`.

Ejemplo para temperatura:

```bash
python scripts/agromet_sondear_disponibilidad.py \
  --variable TA_AVG --stations-per-query 10 \
  --from 2009-01-01 --as-of 2026-10-29 --window-days 7 \
  --pause 2 --skip-completed \
  --output datos_originales/agromet/sondeo_disponibilidad.csv \
  --evidence-dir datos_originales/agromet/evidencia_sondeo
```

Se repite para los ocho códigos de variables de interés. El inicio 2009-01-01
se basa en el período declarado por el propio portal; si una primera fecha
coincidiera exactamente con ese límite, el resumen la marca para extender la
consulta hacia atrás. La disponibilidad actual del sondeo del 2026-10-29 se
evaluó entre 2026-10-22 y 2026-10-28.

### Verificación horaria y numérica

El sondeo diario se usa sólo para localizar eficientemente el primer día. Cada
resultado fue verificado después contra la tabla con `intervalo=hour` mediante:

```bash
python scripts/agromet_validar_horario.py \
  --input datos_originales/agromet/sondeo_disponibilidad.csv \
  --output datos_originales/agromet/sondeo_disponibilidad.csv \
  --evidence datos_originales/agromet/auditoria_horaria.json \
  --as-of 2026-10-31 --window-days 7 --workers 2 --reuse-first
```

La auditoría exige un número finito y aplica restricciones físicas básicas:
dirección entre 0° y 360°, humedad entre 0 % y 100 %, presión positiva, y
precipitación, velocidad y ráfaga no negativas, temperatura entre -80 °C y
80 °C, y radiación entre -50 y 2000 W/m². Para cada serie se guarda
`primer_valor_verificado`, además de la primera fecha y hora. Las unidades se
leen directamente del encabezado de la tabla HTML: temperatura en °C, humedad
en %, precipitación en mm, presión en mbar, radiación en W/m², velocidad y
ráfaga en km/h, y dirección en grados.

Se comprobaron las 624 combinaciones estación-variable: 602
tenían al menos una observación horaria numérica y 22 no presentaban datos.
Ninguna de las 602 primeras fechas diarias quedó sin respaldo horario. La
disponibilidad actual se recalculó el 2026-10-31 con observaciones horarias
entre 2026-10-24 y 2026-10-30: 570 series tuvieron datos y 32 series históricas no los tuvieron
en esa ventana.

## Validaciones y limitaciones

- El HTML original se conserva comprimido y su SHA-256 queda en el manifiesto.
- El CSV derivado utiliza fechas ISO 8601, pero el huso se conserva aparte en el
  manifiesto; no se convierten horas.
- Una tabla vacía se registra como `sin_datos`; no demuestra que la estación no
  tenga observaciones en otros períodos.
- La presencia de filas no implica que todas las variables tengan valores. Las
  fechas del catálogo exigen ahora un valor horario numérico por variable; no se
  aceptan textos no vacíos como evidencia.
- La fecha solicitada no debe usarse como primera fecha disponible sin comprobar
  el contenido devuelto.
- La disponibilidad actual se evalúa por variable buscando al menos un valor en
  los siete días completos anteriores a la fecha de consulta. Una ausencia en
  esa ventana se registra como `No`, sin afirmar que la estación esté cerrada.
- AGROMET no publica en este formulario alturas de sensor. Deben buscarse en
  fichas técnicas oficiales; mientras no se comprueben se registrarán como
  `No informado`.
- Las coordenadas, altitudes e instituciones requieren una consulta de metadatos
  adicional; no deben inferirse sólo del nombre mostrado en el selector.
- Antes de una descarga extensa se debe validar una muestra, usar pausas y evitar
  consultas simultáneas agresivas.
- Algunas respuestas contienen marcas temporales imposibles, como meses `00`.
  El sondeo las omite, las conserva en la evidencia JSON y procesa el resto de
  la respuesta. No se corrigen ni se les asigna una fecha inferida.
