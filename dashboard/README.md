# MIRA Chile

**Mapa de Instrumentos y Redes Atmosféricas de Chile.** Dashboard estático e
interactivo para explorar qué mediciones existen, dónde se realizan y qué redes
e instrumentos las sustentan. La primera etapa cubre desde Arica y Parinacota
hasta Atacama.

## Abrir

La opción más simple es abrir `dist/index.html` en un navegador. Para evitar las
restricciones que algunos navegadores aplican a los archivos locales, también se
puede servir desde la raíz del proyecto:

```bash
cd /home/alagos/catalogo_estaciones
python -m http.server 8000
```

Luego abrir:

<http://127.0.0.1:8000/dashboard/dist/index.html>

No requiere base de datos ni un servidor de aplicación. La capa de red vial se
carga sólo cuando se activa su casilla.
Los mapas base satelital y OpenStreetMap se consultan en línea y mantienen la
atribución exigida por sus proveedores; el relieve ASTER permanece alojado
dentro del sitio.

## Publicación en GitHub Pages

El repositorio incluye `.github/workflows/deploy-pages.yml`. Al subir la rama
`main`, el flujo publica automáticamente `dashboard/dist`. En la configuración
del repositorio se debe seleccionar **Settings → Pages → Source: GitHub
Actions**.

Las guías de descarga se copian dentro del artefacto web al reconstruir los
datos. Por eso sus enlaces funcionan tanto localmente como bajo una ruta del
tipo `https://usuario.github.io/repositorio/`.

## Contenido

- mapa de estaciones por red;
- mapas base seleccionables: imagen satelital de Esri, OpenStreetMap o ninguno;
- filtros de selección múltiple por región, comuna, red, institución, variable,
  tipo, altura, frecuencia y disponibilidad actual, con casillas y búsqueda de
  opciones en las listas extensas;
- filtro «Desde qué año»: muestra las series cuya primera observación
  comprobada es de ese año o anterior;
- detalle de las series seleccionadas de cada estación;
- tabla de estaciones y matriz red × variable;
- descarga CSV de las series filtradas;
- capas opcionales de regiones, comunas, red vial y relieve ASTER.

En cada filtro se puede elegir una opción, varias o «Todos/Todas». Las opciones
de una misma lista se combinan como alternativas; los distintos filtros se
aplican simultáneamente. Las comunas se ajustan a las regiones seleccionadas.
«Limpiar filtros» restaura el catálogo completo, incluido el criterio de año.

El año de inicio se evalúa por **serie**, no por la fecha más antigua de otra
variable de la misma estación. Afecta al mapa, los contadores, la tabla, la
matriz, el detalle y la selección descargada en CSV. Las series con fecha de
inicio desconocida quedan fuera cuando se aplica ese criterio. El inicio
verificado no garantiza que la serie sea continua ni que tenga datos en todos
los años posteriores; el estado actual se filtra por separado.

El relieve es una representación visual reducida de ASTER GDEM, reproyectada a
Web Mercator para coincidir con las capas vectoriales de Leaflet. No sustituye
el valor de `altitud_snm_m` informado para cada estación.

El TIFF ASTER original y los shapefiles no se publican: el artefacto contiene
únicamente un relieve WebP reducido y geometrías GeoJSON simplificadas. Esto
mantiene el sitio liviano y conserva las comunas, regiones y red vial.

## Reconstruir los datos

```bash
/home/alagos/.conda/envs/env_leftraru/bin/python \
  /home/alagos/catalogo_estaciones/dashboard/scripts/build_dashboard.py
```

El script lee los seis catálogos fuente, asigna región y comuna mediante los
polígonos oficiales y regenera los archivos de `dist/data`. Las estaciones
marítimas situadas apenas fuera de la línea de costa se asignan a la comuna más
cercana únicamente cuando ésta se encuentra a menos de 20 km.

Fuentes cartográficas utilizadas:

- `/home/alagos/fondef_polvo/data/geospatial/Regiones/Regional.shp`
- `/home/alagos/fondef_polvo/data/geospatial/Comunas/comunas.shp`
- `/home/alagos/fondef_polvo/data/geospatial/Red_Vial/redvial2019.shp`
- `/home/alagos/fondef_polvo/data/observations/satellite/ASTER_GDEM/ASTGTM_NC.003_ASTER_GDEM_DEM_doy2000061000000_aid0001.tif`

## Verificar los filtros

Las pruebas de interfaz usan Chromium y Playwright en un entorno de pruebas:

```bash
python -m pip install -r dashboard/tests/requirements.txt
python -m playwright install chromium --only-shell
python -m unittest discover -s dashboard/tests -v
```

Comprueban selección simple y múltiple en los nueve filtros categóricos,
combinaciones entre filtros, selección CSV, restablecimiento, comunas por
región, controles de teclado y pantalla pequeña. Un caso de prueba específico
verifica el año inclusivo, las fechas desconocidas y que una variable reciente
no herede la antigüedad de otra variable de la misma estación.
