# MIRA Chile: catálogo de instrumentos y redes atmosféricas

## 1. Objetivo

Construir un catálogo reproducible de las observaciones meteorológicas y de calidad del aire disponibles para el norte de Chile. El producto principal **no contiene las observaciones**: es un archivo CSV que describe, para cada estación, variable, altura de sensor y frecuencia de muestreo, desde cuándo existen datos, si están disponibles actualmente y cómo descargarlos.

El catálogo debe permitir que otra persona identifique los datos disponibles y reproduzca su descarga siguiendo el `README.md` correspondiente.

## 2. Área geográfica

Incluir estaciones ubicadas en las siguientes regiones de Chile, ambas regiones extremas incluidas:

1. Región de Arica y Parinacota.
2. Región de Tarapacá.
3. Región de Antofagasta.
4. Región de Atacama.

La inclusión se determina por las coordenadas de la estación, no solamente por la región declarada en el portal de origen. Las estaciones móviles requieren una evaluación separada; no se les debe asignar una única ubicación fija sin evidencia.

## 3. Fuentes que se deben investigar

Investigar, como mínimo, las siguientes fuentes o instituciones:

- INIA y la red AGROMET.
- FDF.
- DMC.
- DGA.
- DGAC.
- SERVIMET.
- SINCA.
- MMA.
- Explorador Eólico.
- VISMET.
- VIPNET.

Se pueden incorporar otras fuentes pertinentes si poseen observaciones dentro del área y variables de interés. Se debe distinguir entre la institución responsable de los datos y el portal que los distribuye cuando no sean la misma entidad.

Para la red del Explorador Eólico (`EXP_EOL`), las publicaciones de `http://walker.dgf.uchile.cl/Mediciones/` y `https://eolico.minenergia.cl/mediciones_dmc/` se atribuyen a `MINENERGIA`. Aunque la segunda URL contenga el segmento `mediciones_dmc`, no se debe registrar DMC como fuente/institución. Las dos publicaciones deben compararse y sólo deben deduplicarse cuando la equivalencia de la serie esté comprobada.

La ausencia de resultados en un portal no basta para concluir que una institución no tiene datos: debe quedar documentado qué se buscó, cuándo y con qué limitaciones de acceso.

## 4. Variables de interés

### 4.1. Variables meteorológicas

- Radiación solar.
- Precipitación.
- Temperatura.
- Humedad.
- Presión.
- Intensidad o velocidad del viento.
- Dirección del viento.
- Ráfaga del viento.

### 4.2. Variables de calidad del aire

- MP2.5.
- MP10.
- SO2.
- NOX.
- NO.
- NO2.
- O3.
- CO.
- COV.
- HCT.
- CH4.
- Pb.

Los nombres deben normalizarse a esta lista, conservando en la documentación de cada fuente el nombre y código originales, las unidades y cualquier definición técnica necesaria. No se debe equiparar una variable medida con otra calculada o agregada sin documentarlo.

## 5. Alturas y frecuencias

Se deben catalogar todas las alturas de sensor publicadas para una estación y variable. Si una misma variable se mide a varias alturas, cada altura debe ocupar una fila independiente.

La prioridad de frecuencia de muestreo es:

1. 1 minuto.
2. 5 minutos.
3. 15 minutos.
4. 30 minutos.
5. 60 minutos.

Se deben incluir todas las frecuencias disponibles de interés, no solamente la de mayor resolución. Si una fuente publica tanto datos originales como agregaciones, cada combinación debe registrarse por separado y explicarse en el `README.md`.

Si una fuente oficial publica otro intervalo (por ejemplo, 10 minutos), se conserva el intervalo real; nunca se redondea a una frecuencia de la lista prioritaria.

No se debe confundir la frecuencia de muestreo del instrumento con la frecuencia de publicación, transmisión o agregación del producto.

## 6. Producto principal

El producto final será:

`catalogo_estaciones.csv`

La unidad de cada fila es una combinación única de:

`estación + ID + fuente/institución + variable + altura del sensor + frecuencia de muestreo`

El archivo tendrá exactamente las siguientes 13 columnas, en este orden:

| Nº | Nombre de columna | Contenido |
|---:|---|---|
| 1 | `nombre_estacion` | Nombre oficial publicado por la fuente. |
| 2 | `id` | Identificador oficial de la estación en la fuente. |
| 3 | `fuente_institucion` | Fuente o institución responsable, por ejemplo DMC, DGA, MMA o SINCA. |
| 4 | `latitud` | Latitud en grados decimales, datum WGS84. Sur se expresa con signo negativo. |
| 5 | `longitud` | Longitud en grados decimales, datum WGS84. Oeste se expresa con signo negativo. |
| 6 | `altitud_snm_m` | Altitud de la estación sobre el nivel del mar, en metros. |
| 7 | `variable` | Nombre normalizado de la variable de interés. |
| 8 | `tipo_variable` | Uno de dos valores: `Meteorológica` o `Calidad del aire`. |
| 9 | `altura_sensor_suelo_m` | Altura del sensor sobre el suelo, en metros. |
| 10 | `frecuencia_muestreo` | Intervalo nominal publicado o comprobado (por ejemplo, `1 min`, `5 min`, `10 min`, `15 min`, `30 min` o `60 min`). |
| 11 | `primera_fecha_disponible` | Primera fecha/hora comprobada con datos disponibles. |
| 12 | `datos_disponibles_actualmente` | Estado comprobado: `Sí`, `No` o `No determinado`. |
| 13 | `readme_descarga` | Nombre o ruta relativa del `README.md` que permite reproducir la descarga. |

El CSV debe guardarse en UTF-8, con encabezado y separador coma. Los campos que contienen comas deben seguir el escapado CSV estándar.

## 7. Convenciones de representación

- Usar fechas y horas en formato ISO 8601. Si la fuente sólo informa una fecha, usar `AAAA-MM-DD`. Si informa hora, conservar también el huso horario cuando sea conocido.
- `primera_fecha_disponible` describe la primera fecha/hora realmente comprobada con un valor, no sólo una fecha declarada por un portal ni el inicio del período solicitado a una API.
- `datos_disponibles_actualmente` se evalúa por estación y variable. Se escribe `Sí` si existe al menos un valor durante los últimos siete días completos anteriores a la consulta; `No` si la fuente responde correctamente pero no contiene valores durante esa ventana; y `No determinado` si la fuente no pudo consultarse o la evidencia es insuficiente.
- El objetivo es determinar toda la cobertura pública disponible. El período 2010-01-01 a 2025-12-31 de la guía preexistente sirve como antecedente de una descarga anterior, pero no limita el catálogo.
- No estimar coordenadas, altitud, altura del sensor, frecuencia ni fechas a partir de supuestos.
- Cuando un metadato no esté publicado o no pueda comprobarse, escribir `No informado`.
- Cuando un campo no corresponda conceptualmente, escribir `No aplica`. No usar este valor sólo porque el dato sea difícil de encontrar.
- Conservar la precisión publicada por la fuente. Las conversiones de coordenadas o unidades deben documentarse.
- Si una estación cambió de ubicación, ID, instrumentación o altura de sensor, no fusionar períodos incompatibles sin dejar constancia y, cuando corresponda, separar los registros.
- Documentar la fecha de consulta y la ventana utilizada para evaluar disponibilidad actual. Un valor `No` no demuestra el cierre definitivo de una estación.

## 8. Documentación de descarga

Cada fuente debe contar con un `README.md` propio o inequívocamente identificable. El campo `readme_descarga` del CSV debe apuntar a ese documento mediante una ruta relativa al directorio del proyecto.

Cada guía de descarga debe incluir, cuando corresponda:

- Institución y portal de origen.
- URL oficial y fecha de consulta.
- Condiciones de acceso, cuenta o credenciales, sin guardar secretos.
- Método manual, API o script utilizado.
- Comandos reproducibles y dependencias.
- Identificadores de estaciones y códigos originales de variables.
- Frecuencias, unidades, alturas y husos horarios publicados.
- Método usado para determinar la primera fecha disponible y la disponibilidad actual.
- Formato de los archivos descargados y códigos de datos faltantes.
- Límites, errores conocidos y cambios observados en el portal.
- Evidencia o referencia que respalda los metadatos.

No se deben incluir contraseñas, tokens, claves API ni datos personales de acceso en el repositorio.

## 9. Trazabilidad y control de calidad

- Cada valor del catálogo debe poder rastrearse hasta una fuente oficial, una respuesta descargada o una inspección reproducible.
- Conservar los archivos originales de metadatos y manifiestos cuando sea legal y práctico; los datos derivados deben mantenerse separados.
- Verificar que las coordenadas correspondan al área geográfica del proyecto.
- Comprobar duplicados según la unidad de fila definida en la sección 6.
- Verificar que cada ruta de `readme_descarga` exista.
- Distinguir `sin datos`, `sin acceso`, `no informado` y `fuente temporalmente no disponible` en la documentación.
- No afirmar que una serie es continua sólo porque sus fechas extremas cubren un período amplio.
- Las páginas y catálogos pueden cambiar. Registrar la fecha de consulta y volver a validar la información antes de cerrar el producto.

## 10. Entregables mínimos

1. `catalogo_estaciones.csv` con las 13 columnas definidas.
2. Un `README.md` de descarga por fuente o método de acceso.
3. Archivos o notas de evidencia suficientes para auditar los metadatos.
4. Scripts reproducibles necesarios para consultar o validar el catálogo, si corresponde.
5. Un resumen de cobertura, vacíos de información y fuentes que no pudieron consultarse.

## 11. Antecedente disponible

Usar como punto de partida, sin asumir que esté completo ni vigente:

`/home/alagos/fondef_polvo/data/observations/surface/meteo_chem/GUIA_DESCARGA_INIA_DMC.md`

Esa guía documenta procedimientos previos para AGROMET/INIA y DMC, incluido un período de estudio anterior, scripts locales, productos DMC y precauciones de validación. Sus resultados deben verificarse nuevamente al incorporarlos al catálogo.

## 12. Criterio de término

El trabajo estará completo cuando se hayan investigado todas las fuentes mínimas, cada fila del CSV tenga respaldo verificable o una ausencia explícitamente documentada, los `README.md` permitan reproducir las consultas o descargas y las validaciones de estructura, duplicados, cobertura geográfica y rutas hayan sido superadas.
