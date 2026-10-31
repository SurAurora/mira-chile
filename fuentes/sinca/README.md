# Descarga y verificación de datos SINCA

## Alcance

Esta fuente corresponde al **Sistema de Información Nacional de Calidad del
Aire (SINCA), del Ministerio del Medio Ambiente (MMA)**. El campo
`fuente_institucion` se registra como `SINCA / MMA`.

Se consultaron el 2026-10-30 las estaciones publicadas en:

- [Arica y Parinacota (XV)](https://sinca.mma.gob.cl/index.php/region/index/id/XV)
- [Tarapacá (I)](https://sinca.mma.gob.cl/index.php/region/index/id/I)
- [Antofagasta (II)](https://sinca.mma.gob.cl/index.php/region/index/id/II)
- [Atacama (III)](https://sinca.mma.gob.cl/index.php/region/index/id/III)

El resultado derivado está en `catalogo_sinca.csv`. Las fichas HTML
comprimidas y los inventarios de auditoría están en
`datos_originales/sinca/`.

## Reproducción

Requiere Python 3 y el paquete `requests`. Desde la raíz del proyecto:

```bash
python scripts/construir_catalogo_sinca.py --refresh-metadata --refresh-series --workers 4
```

Sin las opciones `--refresh-*`, el script reutiliza las fichas comprimidas y
los sondeos cuya ventana de disponibilidad actual aún coincida. Se limita a
cuatro consultas concurrentes para no sobrecargar el portal.

El script hace lo siguiente:

1. Obtiene el listado de estaciones de las cuatro páginas regionales.
2. Lee de cada ficha el ID, nombre, propietario, operador, coordenadas UTM,
   huso UTM, fechas declaradas, variables y alturas publicadas.
3. Descarga cada producto horario desde el punto oficial
   `https://sinca.mma.gob.cl/cgi-bin/APUB-MMA/apub.tsindico2.cgi`.
4. Busca el primer registro que contenga al menos un valor numérico válido.
5. Comprueba por separado si hubo valores durante los siete días completos
   anteriores a la consulta.

Los parámetros principales del punto de descarga son `macro`, `from`, `to`,
`outtype`, `path=/usr/airviro/data/CONAMA/` y `lang=esp`. `from` y `to` usan
el formato `AAMMDD`. El propio script obtiene `macro` desde cada ficha, por lo
que no es necesario copiarlo manualmente.

## Criterio para la primera fecha

La fecha que muestra la ficha se usa sólo como cota de inicio de la búsqueda.
No se incorpora al CSV hasta comprobar una descarga con un valor. En los CSV
horarios se acepta un número finito en cualquiera de las columnas de estado
publicadas por SINCA (validado, preliminar o no validado); celdas vacías y
códigos menores o iguales a `-900` se consideran faltantes.

La búsqueda usa la fecha específica de la variable y la fecha contenida en su
enlace de descarga; no impone como regla general el `Inicio de operación
reportada` de la estación. Ese campo puede ser posterior a observaciones
históricas válidas. Se amplía la consulta un día hacia atrás porque varias
series comienzan a las 23:00 del día anterior a la fecha mostrada en la tabla.

Se inspeccionaron separadamente las rutas con períodos muy anteriores a la
serie declarada. Se aplicaron estas excepciones explícitas:

- Arica, ID 232, `PM25`: el CGI devuelve en 2000 una secuencia artificial
  1, 2, 3, ... anterior en trece años a la operación; se busca desde el inicio
  de operación de 2013.
- Copiapo Sivica, ID 176, `0001`, `PM10` y `PM25`: las macros devuelven datos
  de 1990 ajenos al período publicado; se busca desde la fecha declarada de
  cada variable.
- Sierra Gorda, ID 255, `PM25`: se excluye un registro aislado de 1990 y se
  busca desde la fecha declarada de 2012.

Además de los faltantes generales, se aplican restricciones físicas
inequívocas: dirección entre 0° y 360°, presión mayor que cero, velocidad y
precipitación no negativas, y humedad entre 0 % y 100 %. No se descartan
automáticamente concentraciones altas o negativas si el portal las publica
como números, porque eso requeriría evaluar sus banderas de calidad y unidades,
no sólo su magnitud.

La frecuencia catalogada es `60 min`, porque se verificó el producto de
registros horarios. No se confunde con la frecuencia de transmisión en línea.

### Dirección del viento

El selector de `WDIR` ofrece dos productos diferentes:

- `horario_...ic`: `Dir. viento - rosa de los vientos`;
- `horario_..._spec.ic`: `Dir. viento - serie de tiempo`.

El catálogo usa el segundo. Su descarga `xcl` contiene fecha, hora y dirección
en grados, por lo que se verifica con el mismo criterio de valores horarios que
las demás variables. La rosa de vientos no se usa para determinar la primera
fecha ni la disponibilidad actual. La opción `_spec.ic` se descubre en el
marco `pageLeft` que carga la página de gráficos y no en la ficha principal de
la estación.

Se comprobó para las 37 combinaciones estación-altura que la macro `_spec.ic`
aparece efectivamente entre las opciones de serie temporal. Sólo se consideran
válidos ángulos entre 0° y 360°; valores negativos, códigos faltantes y
números fuera de ese dominio se descartan. El CGI puede omitir la fila de las
00:00 cuando `from` coincide con el primer día solicitado, por lo que el script
pide desde el día anterior y filtra después el intervalo exacto. Este control
corrigió primeras horas que antes aparecían erróneamente como 01:00.

## Disponibilidad actual

Para la consulta del 2026-10-30 se utilizó la ventana de siete días completos
`2026-10-23` a `2026-10-29`, ambos inclusive:

- `Sí`: existe al menos un valor en esa ventana.
- `No`: el portal respondió, pero no contiene valores en la ventana.
- `No determinado`: la consulta no pudo verificarse.

Un valor `No` no demuestra que la estación haya cerrado; sólo describe la
ventana indicada.

## Coordenadas, altitud y altura del sensor

Las fichas publican coordenadas UTM. Se transformaron a latitud y longitud
WGS84 usando el huso indicado en la ficha, hemisferio sur (en estas regiones,
EPSG:32719 para el huso 19). La implementación reproducible está en
`utm_wgs84_to_latlon()` dentro del script.

La ficha de Tierra Amarilla (ID 224) publica literalmente `6960567 E 375128
N`: las componentes están rotuladas en orden inverso, pues el este UTM no puede
tener siete dígitos y el norte de Atacama sí. Se conservaron esos valores en
`utm_e_publicado` y `utm_n_publicado`, y se intercambiaron explícitamente a
`375128 E 6960567 N` antes de transformar. La columna `utm_correction` del
inventario deja registrada la decisión.

SINCA no publica la altitud sobre el nivel del mar en estas fichas, por lo que
`altitud_snm_m` se registra como `No informado`.

Las alturas de los sensores meteorológicos se toman de la columna `Altura de
medición` de la ficha. Las filas de contaminantes no publican una altura de
sensor en la tabla utilizada, por lo que se registra `No informado`.

## Variables y códigos

| Código SINCA | Variable normalizada |
|---|---|
| `PM25` | MP2.5 |
| `PM10` | MP10 |
| `0001` | SO2 |
| `0NOX` | NOX |
| `0002` | NO |
| `0003` | NO2 |
| `0008` | O3 |
| `0004` | CO |
| `00Pb` | Pb |
| `GLOB` | Radiación solar |
| `RAIN` | Precipitación |
| `TEMP` | Temperatura |
| `RHUM` | Humedad |
| `PRES` | Presión |
| `WSPD` | Intensidad del viento |
| `WDIR` | Dirección del viento |

No se encontró un producto horario descargable para COV, HCT, CH4, ráfaga
de viento ni Pb en las fichas revisadas. Aunque Pb (`00Pb`) aparece como
parámetro en once estaciones, no ofrece el enlace de `registros horarios` y,
por tanto, no se incorporó como si tuviera una frecuencia de 60 minutos.

`PM1D` significa material particulado MP10 **discreto**: corresponde a
mediciones por muestras separadas, en algunos casos identificadas como método
gravimétrico con muestreador de alto volumen. La ficha puede mostrar fechas,
pero no publica para este parámetro una serie horaria. Las estaciones con
`PM1D` fueron 33 y 154. No se incluyeron porque la evidencia disponible no
demuestra una frecuencia de 60 minutos o menor. Esto no significa que las
mediciones sean inválidas; están fuera de la resolución temporal solicitada.

## Resultado y controles

En la consulta del 2026-10-30 se obtuvieron:

- 62 estaciones en las cuatro páginas regionales;
- 289 series horarias de variables pertinentes;
- 287 series con al menos un valor comprobado, pertenecientes a 54 estaciones;
- 125 series con datos durante la ventana actual y 162 sin datos en ella;
- 0 fallos de consulta al finalizar el sondeo.

Dos enlaces horarios no devolvieron ningún valor admisible desde el inicio
de la serie hasta la fecha de consulta y no se incorporaron al catálogo:

| ID | Estación | Código | Altura |
|---:|---|---|---:|
| 68 | Hospital | `PM10` | No informado |
| 172 | Super Site | `PRES` | 2 m |

La ruta de presión de Super Site contiene ceros, pero no se consideraron una
observación atmosférica válida. En cambio, se recuperaron valores verificables
en las rutas de radiación, presión y humedad de Tres Marias y en la velocidad
de Gobernación, que una revisión anterior había omitido por usar el inicio
general de operación como corte.

Este resultado queda trazado en:

- `datos_originales/sinca/inventario_estaciones.csv`: metadatos y coordenadas
  transformadas de las 62 estaciones;
- `datos_originales/sinca/inventario_series.csv`: todas las series horarias
  pertinentes, incluidas las dos sin valores admisibles;
- `datos_originales/sinca/sondeo_series.csv`: fecha comprobada, ventana actual
  y estado de cada sondeo;
- `datos_originales/sinca/fallos.csv`: errores de red o procesamiento;
- `datos_originales/sinca/resumen_ejecucion.json`: conteos de la ejecución.

Las fechas extremas no prueban continuidad de una serie. Para estudios de
completitud deben descargarse y evaluarse las observaciones entre ambas fechas.
