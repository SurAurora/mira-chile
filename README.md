# MIRA Chile

**Mapa de Instrumentos y Redes Atmosféricas de Chile.** Catálogo para explorar
qué mediciones atmosféricas existen, dónde se realizan y qué redes e
instrumentos las sustentan. La primera etapa cubre Arica y Parinacota, Tarapacá,
Antofagasta y Atacama.

## Dashboard

El sitio estático se encuentra en [`dashboard/dist`](dashboard/dist). Permite
filtrar estaciones, variables, alturas, frecuencias, redes e instituciones, y
mostrar opcionalmente comunas, regiones, red vial y relieve derivado de ASTER.

Para verlo localmente:

```bash
python -m http.server 8000
```

Abrir <http://127.0.0.1:8000/dashboard/dist/index.html>.

La publicación en GitHub Pages se realiza automáticamente mediante
`.github/workflows/deploy-pages.yml` cuando se actualiza la rama `main`.

## Datos fuente

Los CSV principales están en la raíz. La evidencia reproducible se conserva
localmente en `datos_originales/` y las guías de descarga en `fuentes/`. La
carpeta `datos_originales/` está excluida de Git para no publicar descargas
brutas. El catastro SACLIM original está en:

`datos_originales/dmc/CatastrodeEstacionesdelSistemaSACLIM_2026-10-31_15_25.xls`

Los insumos geoespaciales grandes y el TIFF ASTER permanecen fuera del
repositorio, en el proyecto FONDEF. El constructor genera para el dashboard
copias web simplificadas y livianas.
