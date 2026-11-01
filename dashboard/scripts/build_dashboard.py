#!/usr/bin/env python3
"""Construye los datos estáticos del dashboard del catálogo de estaciones.

No modifica los catálogos fuente. Convierte el catálogo general y las capas SIG
del proyecto FONDEF a archivos JavaScript que se pueden abrir incluso mediante
``file://``. También crea un relieve web liviano a partir del ASTER GDEM.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds
from shapely.geometry import mapping


REGION_CODES = {15, 1, 2, 3}
NETWORK_BY_FILE = {
    "catalogo_agromet.csv": "AGROMET",
    "catalogo_dga.csv": "DGA / Red Hidrométrica Nacional",
    "catalogo_dmc.csv": "DMC / SACLIM",
    "catalogo_exp_eol.csv": "Explorador Eólico",
    "catalogo_sinca.csv": "SINCA",
}
REGION_SHORT = {
    15: "Arica y Parinacota",
    1: "Tarapacá",
    2: "Antofagasta",
    3: "Atacama",
}


def clean_value(value):
    if pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def feature_collection(frame: gpd.GeoDataFrame, properties: list[str]) -> dict:
    features = []
    for _, row in frame.iterrows():
        props = {name: clean_value(row.get(name)) for name in properties}
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": mapping(row.geometry),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def write_js(path: Path, variable: str, payload) -> None:
    path.write_text(
        f"window.{variable} = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )


def build_readme_pages(project: Path, dist: Path, readme_paths: list[str]) -> dict[str, str]:
    """Incluye las guías dentro del artefacto publicable de GitHub Pages."""
    docs = dist / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for relative in sorted(set(readme_paths)):
        source = project / relative
        source_name = source.parent.name
        target_name = f"{slug(source_name)}.html"
        raw_name = f"{slug(source_name)}.md"
        text = source.read_text(encoding="utf-8")
        (docs / raw_name).write_text(text, encoding="utf-8")
        title = f"Guía de descarga: {source_name.upper()}"
        page = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{max-width:980px;margin:0 auto;padding:32px 22px;background:#f6f3eb;color:#18302d;font:16px/1.55 system-ui,sans-serif}}
a{{color:#18736f}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#fffdf8;border:1px solid #d9ddd4;border-radius:12px;padding:22px}}
</style></head><body><p><a href="../index.html">← Volver al dashboard</a></p><h1>{html.escape(title)}</h1>
<pre>{html.escape(text)}</pre></body></html>"""
        (docs / target_name).write_text(page, encoding="utf-8")
        mapping[relative] = f"docs/{target_name}"
    return mapping


def build_boundaries(geospatial: Path, output: Path):
    regions = gpd.read_file(geospatial / "Regiones" / "Regional.shp")
    regions = regions[regions["codregion"].isin(REGION_CODES)].to_crs(4326)
    regions["region"] = regions["codregion"].map(REGION_SHORT)
    regions.geometry = regions.geometry.simplify(0.002, preserve_topology=True)

    communes = gpd.read_file(geospatial / "Comunas" / "comunas.shp")
    communes = communes[communes["codregion"].isin(REGION_CODES)].to_crs(4326)
    communes = communes.rename(
        columns={"Comuna": "comuna", "Provincia": "provincia", "codregion": "codigo_region"}
    )
    communes["region"] = communes["codigo_region"].map(REGION_SHORT)
    communes.geometry = communes.geometry.simplify(0.002, preserve_topology=True)

    region_geojson = feature_collection(regions, ["codregion", "region"])
    commune_geojson = feature_collection(
        communes, ["cod_comuna", "codigo_region", "comuna", "provincia", "region"]
    )
    write_js(output / "regions.js", "CATALOG_REGIONS", region_geojson)
    write_js(output / "communes.js", "CATALOG_COMMUNES", commune_geojson)
    return regions, communes


def build_roads(geospatial: Path, output: Path) -> None:
    roads = gpd.read_file(
        geospatial / "Red_Vial" / "redvial2019.shp",
        columns=["Cod_Region", "Clase_Ruta", "Tipo_Carpe", "geometry"],
    )
    roads = roads[roads["Cod_Region"].isin(REGION_CODES)].copy()
    roads = roads[roads.geometry.notna() & ~roads.geometry.is_empty]
    roads["clase"] = roads["Clase_Ruta"].fillna(9).astype(int)
    roads["region"] = roads["Cod_Region"].map(REGION_SHORT)
    # Disolver reduce decenas de miles de segmentos a unas pocas geometrías.
    roads.geometry = roads.geometry.simplify(100, preserve_topology=True)
    roads = roads.dissolve(by=["Cod_Region", "region", "clase"], as_index=False)
    roads = roads.to_crs(4326)
    roads.geometry = roads.geometry.simplify(0.001, preserve_topology=True)
    roads_geojson = feature_collection(roads, ["Cod_Region", "region", "clase"])
    write_js(output / "roads.js", "CATALOG_ROADS", roads_geojson)


def sample_relief(dem_path: Path, width: int = 1100):
    with rasterio.open(dem_path) as src:
        # Leaflet representa el mapa en Web Mercator. Una imagen con filas
        # equidistantes en latitud (EPSG:4326) se desplaza respecto de los
        # vectores al estirarla sobre una extensión norte-sur grande. Por eso
        # el relieve se remuestrea primero en el mismo CRS del mapa.
        mercator_bounds = transform_bounds(
            src.crs, "EPSG:3857", *src.bounds, densify_pts=41
        )
        left, bottom, right, top = mercator_bounds
        height = max(1, round(width * (top - bottom) / (right - left)))
        dst_transform = from_bounds(left, bottom, right, top, width, height)
        values = np.full((height, width), np.nan, dtype="float32")
        reproject(
            source=rasterio.band(src, 1),
            destination=values,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=dst_transform,
            dst_crs="EPSG:3857",
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        geographic_bounds = transform_bounds(
            "EPSG:3857", "EPSG:4326", *mercator_bounds, densify_pts=41
        )
        pixel_size = (abs(dst_transform.e), abs(dst_transform.a))
    valid = np.isfinite(values) & (values > -500)
    values[~valid] = np.nan
    return values, valid, geographic_bounds, pixel_size


def build_relief(dem_path: Path, output: Path) -> dict:
    elevation, valid, bounds, pixel_size = sample_relief(dem_path)
    filled = np.nan_to_num(elevation, nan=0.0)

    # Sombreado direccional simple, suficiente para visualización cartográfica.
    dy, dx = np.gradient(filled, *pixel_size)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    azimuth = np.deg2rad(315)
    altitude = np.deg2rad(45)
    shade = (
        np.sin(altitude) * np.cos(slope)
        + np.cos(altitude) * np.sin(slope) * np.cos(azimuth - aspect)
    )
    shade = np.clip((shade + 1) / 2, 0.25, 1.0)

    stops = np.array([-100, 0, 500, 1500, 3000, 4500, 7000], dtype=float)
    colors = np.array(
        [
            [55, 116, 145],
            [225, 215, 176],
            [216, 194, 131],
            [191, 154, 94],
            [150, 115, 85],
            [151, 143, 132],
            [239, 239, 239],
        ],
        dtype=float,
    )
    rgb = np.empty((*elevation.shape, 3), dtype=float)
    for channel in range(3):
        rgb[..., channel] = np.interp(filled, stops, colors[:, channel])
    rgb = np.clip(rgb * (0.62 + 0.55 * shade[..., None]), 0, 255).astype("uint8")
    alpha = np.where(valid, 230, 0).astype("uint8")
    rgba = np.dstack([rgb, alpha])
    Image.fromarray(rgba, "RGBA").save(output / "aster_relief.webp", "WEBP", quality=84, method=6)
    left, bottom, right, top = bounds
    metadata = {
        "bounds": [[bottom, left], [top, right]],
        "source": dem_path.name,
        "projection": "EPSG:3857",
        "description": "Relieve derivado de ASTER GDEM y reproyectado a Web Mercator; sólo para contexto visual.",
    }
    write_js(output / "relief.js", "CATALOG_RELIEF", metadata)
    return metadata


def assign_geography(stations: pd.DataFrame, communes: gpd.GeoDataFrame) -> pd.DataFrame:
    points = gpd.GeoDataFrame(
        stations.copy(),
        geometry=gpd.points_from_xy(stations["longitud"], stations["latitud"]),
        crs=4326,
    )
    commune_fields = communes[
        ["comuna", "provincia", "region", "codigo_region", "geometry"]
    ].copy()
    joined = gpd.sjoin(points, commune_fields, how="left", predicate="within")
    # Una estación sobre un límite puede aparecer más de una vez: preferir la primera.
    joined = joined[~joined.index.duplicated(keep="first")]
    # Algunas estaciones marítimas están apenas fuera del polígono costero. Para ellas,
    # asignar la comuna más cercana sólo si la costa queda a menos de 20 km.
    unmatched = joined["region"].isna()
    if unmatched.any():
        nearby = gpd.sjoin_nearest(
            points.loc[unmatched].to_crs(32719),
            commune_fields.to_crs(32719),
            how="left",
            max_distance=20_000,
            distance_col="distancia_costa_m",
        )
        nearby = nearby[~nearby.index.duplicated(keep="first")]
        for column in ["comuna", "provincia", "region", "codigo_region"]:
            joined.loc[unmatched, column] = nearby[column]
    joined["region"] = joined["region"].fillna("Fuera de cobertura cartográfica")
    joined["comuna"] = joined["comuna"].fillna("No determinada")
    joined["provincia"] = joined["provincia"].fillna("No determinada")
    return pd.DataFrame(joined.drop(columns=["geometry", "index_right"], errors="ignore"))


def build_catalog(project: Path, output: Path, communes: gpd.GeoDataFrame) -> None:
    frames = []
    for filename, network in NETWORK_BY_FILE.items():
        frame = pd.read_csv(project / filename, dtype={"id": str})
        frame["red"] = network
        frames.append(frame)
    catalog = pd.concat(frames, ignore_index=True)
    catalog["latitud"] = pd.to_numeric(catalog["latitud"], errors="coerce")
    catalog["longitud"] = pd.to_numeric(catalog["longitud"], errors="coerce")
    catalog["altitud_snm_m"] = pd.to_numeric(catalog["altitud_snm_m"], errors="coerce")
    readme_urls = build_readme_pages(
        project,
        output.parent,
        catalog["readme_descarga"].dropna().unique().tolist(),
    )
    catalog["readme_url"] = catalog["readme_descarga"].map(readme_urls)

    identity = ["red", "fuente_institucion", "id", "nombre_estacion", "latitud", "longitud"]
    stations = catalog[identity + ["altitud_snm_m"]].drop_duplicates(identity).reset_index(drop=True)
    stations["station_key"] = [
        f"s{i + 1:04d}-{slug(str(row.nombre_estacion))}" for i, row in stations.iterrows()
    ]
    stations = assign_geography(stations, communes)
    catalog = catalog.merge(stations[identity + ["station_key"]], on=identity, how="left")

    series_fields = [
        "station_key",
        "variable",
        "tipo_variable",
        "altura_sensor_suelo_m",
        "frecuencia_muestreo",
        "primera_fecha_disponible",
        "datos_disponibles_actualmente",
        "readme_descarga",
        "readme_url",
    ]
    series = catalog[series_fields].copy()
    series.insert(0, "series_key", [f"v{i + 1:05d}" for i in range(len(series))])

    by_station = series.groupby("station_key", sort=False)
    station_records = []
    for _, row in stations.iterrows():
        subset = by_station.get_group(row.station_key)
        rec = {key: clean_value(value) for key, value in row.items()}
        rec.update(
            {
                "series_count": int(len(subset)),
                "variables": sorted(subset["variable"].dropna().unique().tolist()),
                "types": sorted(subset["tipo_variable"].dropna().unique().tolist()),
                "heights": sorted(subset["altura_sensor_suelo_m"].dropna().unique().tolist()),
                "frequencies": sorted(subset["frecuencia_muestreo"].dropna().unique().tolist()),
                "current_states": sorted(
                    subset["datos_disponibles_actualmente"].dropna().unique().tolist()
                ),
            }
        )
        station_records.append(rec)

    series_records = [
        {key: clean_value(value) for key, value in row.items()}
        for row in series.to_dict("records")
    ]
    metadata = {
        "generated_at": pd.Timestamp.now(tz="America/Santiago").isoformat(),
        "station_count": len(station_records),
        "series_count": len(series_records),
        "networks": sorted(catalog["red"].unique().tolist()),
        "institutions": sorted(catalog["fuente_institucion"].dropna().unique().tolist()),
        "variables": sorted(catalog["variable"].dropna().unique().tolist()),
        "heights": sorted(catalog["altura_sensor_suelo_m"].dropna().unique().tolist()),
        "frequencies": sorted(catalog["frecuencia_muestreo"].dropna().unique().tolist()),
        "regions": sorted(stations["region"].dropna().unique().tolist()),
    }
    write_js(
        output / "catalog.js",
        "CATALOG_DATA",
        {"metadata": metadata, "stations": station_records, "series": series_records},
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--geospatial",
        type=Path,
        default=Path("/home/alagos/fondef_polvo/data/geospatial"),
    )
    parser.add_argument(
        "--aster",
        type=Path,
        default=Path(
            "/home/alagos/fondef_polvo/data/observations/satellite/ASTER_GDEM/"
            "ASTGTM_NC.003_ASTER_GDEM_DEM_doy2000061000000_aid0001.tif"
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output = args.project / "dashboard" / "dist" / "data"
    output.mkdir(parents=True, exist_ok=True)
    _, communes = build_boundaries(args.geospatial, output)
    build_roads(args.geospatial, output)
    build_relief(args.aster, output)
    build_catalog(args.project, output, communes)
    print(f"Datos del dashboard creados en {output}")


if __name__ == "__main__":
    main()
