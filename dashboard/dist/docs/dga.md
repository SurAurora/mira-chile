# DGA — Red Hidrométrica Nacional

## Fuentes oficiales

- Portal indicado originalmente: <https://snia.mop.gob.cl/dgasat/pages/dgasat_param/dgasat_param.jsp?param=1>
- Visualizador Hidrométrico Nacional (VIPNET): <https://vipnet.mop.gob.cl/>
- Hidrolínea: <https://snia.mop.gob.cl/sat/site/informes/mapas/mapas.xhtml>
- Catastro de estaciones activas y estadísticas: <https://dga.mop.gob.cl/estadisticas-estaciones-dga/>

La institución responsable se registra como `DGA`.

## Alcance incorporado

Se incluyen las series de VIPNET clasificadas como precipitación,
temperatura, humedad e intensidad del viento para las regiones de Arica y
Parinacota, Tarapacá, Antofagasta y Atacama. Nieve y volumen de embalses no se
incorporan porque no forman parte de las variables solicitadas.

VIPNET no separa dirección ni ráfaga dentro de su capa de viento, por lo que no
se crean esas filas. Tampoco publica la altura del sensor sobre el suelo; se
consigna `No informado` sin inferirla.

## Verificación de las series

El script `scripts/dga_sondear_vipnet.py` consulta los servicios JSON que usa el
visualizador. Para cada combinación estación-variable:

1. comprueba observaciones numéricas reales y aplica rangos físicos básicos;
2. toma como primera fecha la del primer valor válido devuelto por VIPNET, no la
   fecha de instalación de la estación;
3. estima la frecuencia a partir de diferencias entre marcas de tiempo;
4. marca disponibilidad actual solamente si existe un valor válido en los
   siete días calendario completos anteriores al sondeo.

Los rangos usados para descartar valores imposibles son: precipitación entre 0
y 500 mm por registro, temperatura entre -80 y 60 °C, humedad entre 0 y 100 %,
y viento entre 0 y 100 m/s. Estos controles no constituyen una validación
climatológica.

Las respuestas crudas o resúmenes de sondeo se guardan bajo
`datos_originales/dga/`, directorio ignorado por Git. El archivo publicable se
genera con:

```bash
python scripts/dga_sondear_vipnet.py --resume
python scripts/construir_catalogo_dga.py
python scripts/construir_catalogo_general.py
```

Los datos en línea DGA son provisorios y pueden ser revisados por la
institución.
