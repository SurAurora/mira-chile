# DMC: Servicios Climáticos y SACLIM

## Alcance y fecha de consulta

Portal distribuidor: **Dirección Meteorológica de Chile (DMC)**. Portal:
<https://climatologia.meteochile.gob.cl/>. Consultas efectuadas el 31 de
octubre de 2026; revisión de las estaciones ESO el **1 de noviembre de 2026**.

Este catálogo incluye estaciones publicadas por DMC/SACLIM con valores
comprobados, conservando su `Propietario` como `fuente_institucion`. La red del
dashboard es **DMC / SACLIM**, incluso cuando la institución sea ESO. El
archivo aportado para el inventario se conserva en
`datos_originales/dmc/CatastrodeEstacionesdelSistemaSACLIM_2026-10-31_15_25.xls`.
Aunque termina en `.xls`, su contenido es HTML UTF-8. En las cuatro regiones
hay 143 estaciones SACLIM: 29 de propiedad DMC y otras de DGA, INIA, FDF, ESO,
DIRECTEMAR o USACH. La primera revisión se limitó a las 29 de propiedad DMC;
se corrigió esa exclusión para las dos estaciones ESO con datos verificados.
Esto no acredita una revisión completa de las 143 estaciones por este portal.

## Productos oficiales revisados

1. Descarga histórica de la estación:
   `https://climatologia.meteochile.gob.cl/application/historico/datosDescarga/{codigo}`.
2. Histórico EMA:
   `https://climatologia.meteochile.gob.cl/application/historico/datosHistoricosEma/{codigo}`.
3. Vista horaria reciente:
   `https://climatologia.meteochile.gob.cl/application/diariob/datosEmaRecienteHorario/{codigo}/{AAAA}/{MM}/{DD}`.
4. Vista diaria automática:
   `https://climatologia.meteochile.gob.cl/application/diario/datosDiariosEstacionAutomatica/{codigo}/{AAAA}/{MM}/{DD}`.
5. Directorio de recursos JSON:
   <https://climatologia.meteochile.gob.cl/application/index/menuTematicoJson>.

Las cuatro vistas de estación redirigen al inicio de sesión cuando se
consultan sin una sesión autenticada. El directorio JSON sí documenta los
servicios. Entre otros, declara datos minutarios de las últimas 12 horas y
datos históricos mensuales cada 15 minutos. La API de datos de una estación
requiere `usuario` y `token`; nunca se guardan esas credenciales en este
proyecto.

El servicio de inventario y algunos GeoJSON funcionan públicamente sin token:

```text
https://climatologia.meteochile.gob.cl/application/servicios/getEstacionesRedEma
https://climatologia.meteochile.gob.cl/application/geoservicios/getEmaResumenDiarioGeo
https://climatologia.meteochile.gob.cl/application/geoservicios/getClimatDiarioRecienteEmas
https://climatologia.meteochile.gob.cl/application/geoservicios/getBoletinTemperaturaDiarioRegional/{region}/{AAAA}/{MM}/{DD}
https://climatologia.meteochile.gob.cl/application/geoservicios/getBoletinPluviometricoDiarioRegional/{region}/{AAAA}/{MM}/{DD}
```

## Descargas históricas comprobadas

### EMA mensual minutaria

Los ZIP mensuales públicos siguen este patrón:

```text
https://climatologia.meteochile.gob.cl/application/datos/getDatosEma/{codigo}/{codigo}_{AAAAMM}_{grupo}.csv.zip
```

Grupos comprobados: `Temperatura`, `PresionHumedad`, `RadiacionGlobal` y
`Viento`. Los CSV contienen registros consecutivos cada minuto cuando la
estación dispone de esa resolución. Un HTTP 200 no basta: un recurso ausente
devuelve una página HTML. Se exige firma ZIP, CRC válido, un CSV y por lo menos
un valor numérico físicamente admisible.

No se encontró un grupo mensual público equivalente para precipitación. La
precipitación minutaria aparece en el servicio JSON autenticado como
`aguaCaidaDelMinuto`.

### API EMA cada 15 minutos

```text
https://climatologia.meteochile.gob.cl/application/servicios/getDatosRecientesEma/{codigo}/{AAAA}/{MM}?usuario=...&token=...
```

El mismo servicio sin año/mes entrega las 12 horas recientes con resolución
minutaria. Las descargas históricas locales preexistentes se volvieron a abrir
y validar; no se confió sólo en sus manifiestos.

La documentación oficial asigna las siguientes alturas a las columnas de
temperatura: `temperatura` = 1,5 m, `temperatura02Mts` = 2 m,
`temperatura10Mts` = 10 m y `temperatura30Mts` = 30 m. Se crea una fila sólo si
la columna contiene valores. Para humedad, presión, radiación, precipitación y
viento el servicio no publica en esa documentación una altura sobre el suelo;
se usa `No informado`.

### SACLIM anual

```text
https://climatologia.meteochile.gob.cl/application/datos/getDatosSaclim/{codigo}_{AAAA}_{variable}_
```

Se comprobaron `Temperatura/Ts`, `Humedad/HR`, `PresionQFE/QFE` y
`Viento/dd,ff`. Las series antiguas no tienen una frecuencia uniforme: hay
intervalos horarios y también observaciones cada 3 o 5 horas. El catálogo
conserva el intervalo observado y no lo llama minutario.

## Primera fecha y disponibilidad actual

La primera fecha es el instante del primer valor que supera los controles
físicos básicos: temperatura entre -80 y 80 °C, humedad entre 0 y 100 %,
presión entre 1 y 1200 hPa, dirección entre 0 y 360°, viento/ráfaga no
negativos y radiación entre -50 y 2000 W m-2. Cero es un valor válido para
precipitación, viento y dirección.

La disponibilidad actual usa los siete días completos anteriores a la
consulta, del **2026-10-24 al 2026-10-30**. Los boletines regionales respaldan
temperatura y precipitación; `intensidadMaxVientoAyer` respalda ráfaga. Para
completar la comprobación se descargó octubre de 2026 mediante la API
autenticada: 25 estaciones entregaron datos y cuatro respondieron `no_data`
(`170006`, `180016`, `220009` y `270015`). Dentro de la ventana se verificaron
valores principales de temperatura, humedad, presión y viento en 24 estaciones,
precipitación en 20 y radiación global en 17.

Cuando dos productos oficiales difieren en su estructura, cualquier valor
válido dentro de la ventana respalda `Sí`. Por ejemplo, la ausencia de campos
de ráfaga en el JSON mensual no anula la máxima diaria verificada en el resumen
EMA. Los campos ausentes se distinguen de los campos presentes sin valores y
`No determinado` no significa que la estación esté inactiva.

Los intervalos horarios, trihorarios y de cinco horas de SACLIM se tratan como
series históricas. Aunque la variable siga midiéndose, los GeoJSON recientes no
demuestran que aquel intervalo antiguo continúe vigente; sus filas conservan
`No determinado` en vez de heredar el estado de otra frecuencia.

## Estaciones ESO publicadas por DMC/SACLIM

Las fichas oficiales de [Cerro Paranal, 240005](https://climatologia.meteochile.gob.cl/application/informacion/fichaDeEstacion/240005)
y [Llano de Chajnantor/APEX, 230021](https://climatologia.meteochile.gob.cl/application/informacion/fichaDeEstacion/230021)
declaran **ESO** como propietario y su pertenencia a las estaciones EMA
publicadas por DMC. Ambas bitácoras registran el cambio de propietario DMC a
ESO el 26 de noviembre de 2019. El catálogo usa la institución actual publicada.

| Código | Frecuencia comprobada | Series | Primera fecha comprobada (UTC) | Estado actual |
|---|---|---:|---|---|
| 240005 | 1 min | 7 | 2019-07-01T00:00:00 | Sí en las siete series |
| 240005 | 15 min | 5 | 2019-01-01T03:00:00 | No determinado para este producto |
| 230021 | 1 min | 6 | 2019-07-01T00:00:00 | Sí en las seis series |
| 230021 | 15 min | 5 | 2019-01-01T03:00:00 | No determinado para este producto |

Las series minutarias de Paranal son temperatura a 1,5 y 30 m, humedad,
presión, dirección, intensidad y ráfaga del viento. APEX tiene las mismas
variables, con temperatura a 1,5 m. Las alturas restantes no se infieren.
Las cinco series de 15 minutos de cada estación son temperatura a 1,5 m,
humedad, presión, dirección e intensidad del viento.

Se reabrieron los JSON locales de enero de 2019 para comprobar cada primer
valor de 15 minutos. Para el producto minutario se consultaron 80 enlaces de
los cuatro grupos desde octubre de 2018 (creación SACLIM) hasta julio de 2019:
no se obtuvieron ZIP anteriores y los primeros valores verificados son de
julio. Las respuestas anteriores se registran como `sin_zip`, que no demuestra
ausencia de observaciones por otros servicios. Las fechas son las primeras
comprobadas con este procedimiento, no las fechas de instalación del sensor.

Para disponibilidad actual se descargaron los ZIP de octubre de 2026 y se
verificaron valores de cada variable y altura dentro de **2026-10-25 a
2026-10-31 UTC**, los siete días completos anteriores a la revisión. Ese
resultado se aplica sólo a las filas de 1 minuto; no se transfiere al producto
de 15 minutos. No se agregan radiación ni precipitación sin valores comprobados
en los productos examinados de estas dos estaciones.

## Reproducción

```bash
cd /home/alagos/catalogo_estaciones
python scripts/dmc_descargar_metadatos.py --as-of 2026-10-31
python scripts/dmc_sondear_saclim.py --end-year 2025
python scripts/dmc_analizar_archivos_existentes.py
python scripts/dmc_sondear_ema_minuto_publico.py --end 2026-01
python scripts/dmc_validar_actual_api.py --as-of 2026-10-31
python scripts/dmc_verificar_eso.py --as-of 2026-11-01
python scripts/construir_catalogo_dmc.py
python scripts/construir_catalogo_general.py
```

Archivos de auditoría principales:

- `datos_originales/dmc/inventario_saclim_regiones.csv`
- `datos_originales/dmc/inventario_dmc.csv`
- `datos_originales/dmc/disponibilidad_actual.csv`
- `datos_originales/dmc/disponibilidad_actual_api_15min.csv`
- `datos_originales/dmc/sondeo_saclim_horario.csv`
- `datos_originales/dmc/sondeo_ema_15min_existente.csv`
- `datos_originales/dmc/sondeo_ema_minuto_existente.csv`
- `datos_originales/dmc/sondeo_ema_minuto_publico_pendientes.csv`
- `datos_originales/dmc/sondeo_ema_minuto_publico_intentos.csv`
- `datos_originales/dmc/sondeo_ema_minuto_publico_eso.csv`
- `datos_originales/dmc/disponibilidad_actual_eso_minuto.csv`
- `datos_originales/dmc/revision_eso_2026-11-01/` (fichas, intentos y ZIP recientes)

## Limitaciones

- La guía anterior `GUIA_DESCARGA_INIA_DMC.md` documentaba los ZIP mensuales,
  la API de 15 minutos y SACLIM anual, pero no describía las cuatro vistas ni
  el directorio JSON indicados arriba. Este README completa esa parte.
- Los archivos EMA preexistentes cubrían principalmente Antofagasta. Para las
  demás estaciones se sondearon 3.906 URLs mensuales públicas desde la fecha de
  creación hasta enero de 2026. Se encontraron 64 series adicionales en 11
  estaciones; 3.805 respuestas fueron páginas HTML sin ZIP y 59 ZIP no
  contenían valores físicamente válidos de las variables buscadas.
- Tres de las 29 estaciones DMC no generan filas: `170006` no entregó valores
  verificables por EMA mensual ni por los cuatro productos horarios SACLIM
  examinados; `180016` y `220009` están clasificadas como radiométricas UV y no
  entregaron radiación solar global ni las demás variables solicitadas. Esto no
  equivale a afirmar que carezcan de cualquier observación.
- La revisión inicial excluía las estaciones ESO `230021` y `240005` por
  propietario. Ahora ambas se incluyen en `catalogo_dmc.csv` y en DMC/SACLIM,
  conservando ESO en la columna de institución. Su presencia por VISMET es
  otro producto, con frecuencia, cobertura y procedencia propias; no se copia
  su fecha inicial al producto DMC.

