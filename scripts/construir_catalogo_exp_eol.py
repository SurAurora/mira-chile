#!/usr/bin/env python3
"""Construye el catálogo MINENERGIA/EXP_EOL sin guardar observaciones completas.

Consulta dos publicaciones oficiales:

* http://walker.dgf.uchile.cl/Mediciones/
* https://eolico.minenergia.cl/mediciones_dmc/

Los CSV se leen en flujo para obtener fechas extremas y cadencia; no se
conservan localmente. Los metadatos y resúmenes necesarios para auditar el
resultado sí se guardan en ``datos_originales/exp_eol``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urljoin

import requests


PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "datos_originales" / "exp_eol"
OUTPUT = PROJECT / "catalogo_exp_eol.csv"
README_PATH = "fuentes/exp_eol/README.md"

WALKER_ROOT = "http://walker.dgf.uchile.cl/Mediciones/"
WALKER_META = urljoin(WALKER_ROOT, "meta/siteinfo.json")
WALKER_CSV = urljoin(WALKER_ROOT, "datos/csv/")
MINENERGIA_ROOT = "https://eolico.minenergia.cl/"
MINENERGIA_GIZ = urljoin(MINENERGIA_ROOT, "pideDatosEstacionesGiz")
SUPPLEMENT_ROOT = urljoin(MINENERGIA_ROOT, "mediciones_dmc/")
STATION_CATALOG = (
    "https://climatologia.meteochile.gob.cl/"
    "application/requerimiento/producto/RE7001"
)

TARGET_REGION_WORDS = ("arica", "tarapac", "antofagasta", "atacama")
NOW_LOCAL = date.today()
CURRENT_START = NOW_LOCAL - timedelta(days=7)
CURRENT_END = NOW_LOCAL - timedelta(days=1)

COLUMNS = [
    "nombre_estacion",
    "id",
    "fuente_institucion",
    "latitud",
    "longitud",
    "altitud_snm_m",
    "variable",
    "tipo_variable",
    "altura_sensor_suelo_m",
    "frecuencia_muestreo",
    "primera_fecha_disponible",
    "datos_disponibles_actualmente",
    "readme_descarga",
]


@dataclass(frozen=True)
class SupplementProduct:
    key: str
    directory: str
    kind: str
    height: str


PRODUCTS = [
    SupplementProduct("HR", "Humedad_relativa_HR", "single", "No informado"),
    SupplementProduct(
        "RadGInst", "Radiacion_global_instantanea_RadGInst", "single", "No informado"
    ),
    SupplementProduct("Ts", "Temperatura_aire_seco_Ts", "single", "No informado"),
    SupplementProduct(
        "viento_10m", "Viento a 10 m. de Altura unificados", "wind", "10"
    ),
    SupplementProduct(
        "viento_2m", "Viento a 2 metro de altura unificados", "wind", "2"
    ),
    SupplementProduct(
        "viento_prom_2min",
        "Viento promedio 2 minutos unificados",
        "wind",
        "No informado",
    ),
]

WALKER_VARIABLES = {
    ("vels", "mean"): "Intensidad del viento",
    ("vels", "max"): "Ráfaga del viento",
    ("dirv", "mean"): "Dirección del viento",
    ("temp", "mean"): "Temperatura",
    ("hrel", "mean"): "Humedad",
    ("pres", "mean"): "Presión",
    ("rglb", "mean"): "Radiación solar",
}

# La temperatura de san_marcos comienza con una transición de puesta en marcha
# desde el centinela -123,5 °C y atraviesa valores numéricos no ambientales.
WALKER_SERIES_NOT_BEFORE = {
    ("san_marcos", "Temperatura"): datetime(2004, 1, 7, 10, 30),
}


def session() -> requests.Session:
    out = requests.Session()
    out.headers.update(
        {
            "User-Agent": (
                "catalogo-estaciones-minenergia/1.0 "
                "(metadatos de disponibilidad; contacto del proyecto local)"
            )
        }
    )
    return out


def request_bytes(url: str, *, attempts: int = 3, headers: dict | None = None) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with session().get(url, timeout=(20, 180), headers=headers) as response:
                response.raise_for_status()
                return response.content
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def cached_bytes(path: Path, url: str, refresh: bool) -> bytes:
    if path.exists() and not refresh:
        with gzip.open(path, "rb") as handle:
            return handle.read()
    payload = request_bytes(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb", compresslevel=6) as handle:
        handle.write(payload)
    return payload


def clean_number(value: object) -> str:
    if value is None or value == "":
        return "No informado"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def valid_numeric(value: str) -> bool:
    text = value.strip().replace("\ufeff", "")
    if not text or text.lower() in {"nan", "na", "null", "none", "s/i"}:
        return False
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return False
    return math.isfinite(number) and number > -9000


def valid_for_variable(value: str, variable: str) -> bool:
    if not valid_numeric(value):
        return False
    number = float(value.strip().replace(",", "."))
    if variable == "Dirección del viento":
        return 0.0 <= number <= 360.0
    if variable == "Humedad":
        return 0.0 <= number <= 100.0
    if variable == "Presión":
        return number > 0.0
    if variable in {"Intensidad del viento", "Ráfaga del viento"}:
        return number >= 0.0
    if variable == "Temperatura":
        return -80.0 <= number <= 80.0
    if variable == "Radiación solar":
        # Se admiten pequeños negativos nocturnos del sensor, pero no valores
        # incompatibles con irradiancia global horizontal en superficie.
        return -50.0 <= number <= 2000.0
    return True


def iso_stamp(stamp: datetime, offset: str | None = None) -> str:
    if offset == "UTC":
        stamp = stamp.replace(tzinfo=timezone.utc)
    elif offset and re.fullmatch(r"UTC[+-]\d{1,2}", offset):
        hours = int(offset[3:])
        stamp = stamp.replace(tzinfo=timezone(timedelta(hours=hours)))
    return stamp.isoformat(timespec="minutes")


def current_state(last_stamp: datetime | None) -> str:
    if last_stamp is None:
        return "No determinado"
    if CURRENT_START <= last_stamp.date() <= CURRENT_END:
        return "Sí"
    return "No"


def declared_old_enough(value: object) -> bool:
    if not value:
        return False
    try:
        stamp = datetime.fromisoformat(str(value).strip().rstrip('"'))
    except ValueError:
        return False
    return stamp.date() < CURRENT_START


def target_walker_site(site: dict) -> bool:
    location = site.get("info", {}).get("location", {})
    region = str(location.get("region") or "").lower()
    if any(word in region for word in TARGET_REGION_WORDS):
        return True
    # Tres estaciones del catastro carecen de nombre de región, pero sus
    # coordenadas y topónimos publicados las ubican inequívocamente al norte.
    return site.get("code") in {"CHAJNANTOR", "CALAMA", "CANCHONES"}


def walker_frequency(site: dict) -> str:
    loggers = site.get("info", {}).get("instrumental_platform", {}).get("logger", [])
    values = sorted(
        {
            int(logger["output_interval_minutes"])
            for logger in loggers
            if logger.get("output_interval_minutes") not in (None, "")
        }
    )
    if not values:
        return "No informado"
    return "; ".join(f"{value} min" for value in values)


def walker_timezone(site: dict) -> str | None:
    loggers = site.get("info", {}).get("instrumental_platform", {}).get("logger", [])
    zones = {str(item.get("output_date_timezone")) for item in loggers if item.get("output_date_timezone")}
    return next(iter(zones)) if len(zones) == 1 else None


def wanted_walker_columns(site: dict) -> list[dict]:
    wanted = []
    for index, meta in enumerate(site.get("vars", []), start=1):
        key = (meta.get("code"), meta.get("stat"))
        variable = WALKER_VARIABLES.get(key)
        if variable:
            wanted.append(
                {
                    "index": index,
                    "variable": variable,
                    "height": clean_number(meta.get("alt")),
                    "original_code": meta.get("code"),
                    "stat": meta.get("stat"),
                }
            )
    return wanted


def walker_columns_from_header(site: dict, header: list[str]) -> list[dict]:
    """Identifica variables por el encabezado que acompaña realmente al dato.

    ``siteinfo.json`` contiene en algunos sitios variables adicionales o en un
    orden distinto al CSV. Por ello no se usa su posición para asignar columnas.
    """
    out: list[dict] = []
    for index, name in enumerate(header[1:], start=1):
        lowered = name.lower()
        stat_match = re.search(r"\[([^,\]]+),", lowered)
        stat = stat_match.group(1).strip() if stat_match else ""
        original_code = ""
        variable = ""
        if "velocidad de viento" in lowered and stat == "mean":
            original_code, variable = "vels", "Intensidad del viento"
        elif "velocidad de viento" in lowered and stat == "max":
            original_code, variable = "vels", "Ráfaga del viento"
        elif "direcci" in lowered and "viento" in lowered and stat == "mean":
            original_code, variable = "dirv", "Dirección del viento"
        elif "temperatura" in lowered and stat == "mean":
            original_code, variable = "temp", "Temperatura"
        elif "humedad relativa" in lowered and stat == "mean":
            original_code, variable = "hrel", "Humedad"
        elif "presi" in lowered and "atmosf" in lowered and stat == "mean":
            original_code, variable = "pres", "Presión"
        elif "radiaci" in lowered and "global horizontal" in lowered and stat == "mean":
            original_code, variable = "rglb", "Radiación solar"
        if not variable:
            continue
        height_match = re.search(
            r"\ben\s+([0-9]+(?:\.[0-9]+)?)\s+metros\b", name, re.IGNORECASE
        )
        height = "No informado"
        height_source = "no_publicada"
        if height_match and float(height_match.group(1)) > 0:
            height = clean_number(float(height_match.group(1)))
            height_source = "encabezado_csv"
        item = {
            "index": index,
            "variable": variable,
            "height": height,
            "original_code": original_code,
            "stat": stat,
        }
        item["height"] = resolved_walker_height(site, item, name)
        if height_source == "no_publicada" and item["height"] != "No informado":
            height_source = "catastro_instrumentos_o_vars"
        item["height_source"] = height_source
        out.append(item)
    return out


def resolved_walker_height(site: dict, item: dict, header_name: str) -> str:
    if item["height"] not in {"0", "No informado"}:
        return item["height"]
    match = re.search(r"\ben\s+([0-9]+(?:\.[0-9]+)?)\s+metros\b", header_name, re.IGNORECASE)
    if match and float(match.group(1)) > 0:
        return clean_number(float(match.group(1)))
    sensors = [
        sensor
        for sensor in site.get("info", {})
        .get("instrumental_platform", {})
        .get("sensor", [])
        if item["original_code"] in (sensor.get("variables") or [])
    ]
    if sensors:
        if any(sensor.get("height") in (None, "", 0, 0.0) for sensor in sensors):
            return "No informado"
        heights = {clean_number(sensor.get("height")) for sensor in sensors}
        return next(iter(heights)) if len(heights) == 1 else "No informado"
    metadata_vars = [
        value
        for value in site.get("vars", [])
        if value.get("code") == item["original_code"]
    ]
    if metadata_vars and all(value.get("alt") not in (None, "", 0, 0.0) for value in metadata_vars):
        heights = {clean_number(value.get("alt")) for value in metadata_vars}
        if len(heights) == 1:
            return next(iter(heights))
    return "No informado"


def parse_walker_first(
    site: dict,
) -> tuple[list[str], list[dict], dict[int, datetime], dict[int, str], list[str], str, str]:
    code = site["code"]
    url = urljoin(WALKER_CSV, f"{quote(code)}.csv")
    wanted: list[dict] = []
    remaining: set[int] = set()
    first: dict[int, datetime] = {}
    first_value: dict[int, str] = {}
    warnings: list[str] = []
    header: list[str] = []
    previous: datetime | None = None
    cadence: Counter[int] = Counter()
    timestamp_count = 0
    with session().get(url, stream=True, timeout=(20, 300)) as response:
        response.raise_for_status()
        lines = (
            line.decode("utf-8-sig", errors="replace")
            for line in response.iter_lines(decode_unicode=False)
        )
        reader = csv.reader(lines)
        header = next(reader)
        wanted = walker_columns_from_header(site, header)
        wanted_by_index = {item["index"]: item for item in wanted}
        remaining = {item["index"] for item in wanted}
        if len(header) - 1 != len(site.get("vars", [])):
            warnings.append(
                f"columnas_csv={len(header)-1}; variables_metadata={len(site.get('vars', []))}"
            )
        for row in reader:
            if not row:
                continue
            try:
                stamp = datetime.fromisoformat(row[0].strip().strip('"'))
            except ValueError:
                continue
            if previous is not None:
                delta = int((stamp - previous).total_seconds() // 60)
                if 0 < delta <= 24 * 60:
                    cadence[delta] += 1
            previous = stamp
            timestamp_count += 1
            for index in tuple(remaining):
                item = wanted_by_index[index]
                not_before = WALKER_SERIES_NOT_BEFORE.get((site["code"], item["variable"]))
                if not_before is not None and stamp < not_before:
                    continue
                if index < len(row) and valid_for_variable(
                    row[index], item["variable"]
                ):
                    first[index] = stamp
                    first_value[index] = row[index].strip()
                    remaining.remove(index)
            if not remaining and timestamp_count >= 1000:
                break
    if remaining:
        warnings.append("sin_valor=" + ",".join(str(value) for value in sorted(remaining)))
    inferred_frequency, inferred_detail = cadence_label(cadence)
    return header, wanted, first, first_value, warnings, inferred_frequency, inferred_detail


def parse_walker_tail(
    site: dict, wanted: list[dict], tail_bytes: int = 1_048_576
) -> dict[int, datetime]:
    code = site["code"]
    url = urljoin(WALKER_CSV, f"{quote(code)}.csv")
    payload = request_bytes(url, headers={"Range": f"bytes=-{tail_bytes}"})
    text = payload.decode("utf-8-sig", errors="replace")
    if payload and not text.startswith('"Fecha'):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    last: dict[int, datetime] = {}
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        try:
            stamp = datetime.fromisoformat(row[0].strip().strip('"'))
        except ValueError:
            continue
        for item in wanted:
            index = item["index"]
            if index < len(row) and valid_for_variable(row[index], item["variable"]):
                last[index] = stamp
    return last


def probe_walker(site: dict) -> tuple[list[dict], list[dict]]:
    (
        header,
        wanted,
        first,
        first_value,
        warnings,
        inferred_frequency,
        inferred_detail,
    ) = parse_walker_first(site)
    last = parse_walker_tail(site, wanted)
    info = site["info"]
    location = info["location"]
    name = (info.get("name2") or info.get("name") or site["code"]).strip()
    frequency = walker_frequency(site)
    frequency_detail = "intervalo de salida publicado en siteinfo.json"
    if frequency == "No informado":
        frequency = inferred_frequency
        frequency_detail = "inferido de las primeras marcas temporales: " + inferred_detail
    zone = walker_timezone(site)
    rows: list[dict] = []
    evidence: list[dict] = []
    for item in wanted:
        index = item["index"]
        first_stamp = first.get(index)
        if first_stamp is None:
            continue
        header_name = header[index] if index < len(header) else str(index)
        height = item["height"]
        last_stamp = last.get(index)
        availability = current_state(last_stamp)
        if availability == "No determinado" and declared_old_enough(info.get("final_date")):
            availability = "No"
        row_warnings = list(warnings)
        instrument_heights = {
            clean_number(sensor.get("height"))
            for sensor in info.get("instrumental_platform", {}).get("sensor", [])
            if item["original_code"] in (sensor.get("variables") or [])
            and sensor.get("height") not in (None, "", 0, 0.0)
        }
        if (
            item.get("height_source") == "encabezado_csv"
            and instrument_heights
            and height not in instrument_heights
        ):
            row_warnings.append(
                "conflicto_altura: encabezado_csv="
                + height
                + "; catastro_instrumentos="
                + "|".join(sorted(instrument_heights))
            )
        rows.append(
            {
                "nombre_estacion": name,
                "id": site["code"],
                "fuente_institucion": "MINENERGIA",
                "latitud": clean_number(location.get("lat")),
                "longitud": clean_number(location.get("lon")),
                "altitud_snm_m": clean_number(location.get("elev")),
                "variable": item["variable"],
                "tipo_variable": "Meteorológica",
                "altura_sensor_suelo_m": height,
                "frecuencia_muestreo": frequency,
                "primera_fecha_disponible": iso_stamp(first_stamp, zone),
                "datos_disponibles_actualmente": availability,
                "readme_descarga": README_PATH,
            }
        )
        evidence.append(
            {
                "publicacion": "walker",
                "id": site["code"],
                "archivo": f"{site['code']}.csv",
                "columna": header_name,
                "variable_normalizada": item["variable"],
                "altura_m": height,
                "fuente_altura": item.get("height_source", "No informado"),
                "primera_fecha_con_valor": iso_stamp(first_stamp, zone),
                "primer_valor_verificado": first_value.get(index, ""),
                "ultima_fecha_con_valor_en_cola": iso_stamp(last_stamp, zone) if last_stamp else "",
                "frecuencia": frequency,
                "detalle_frecuencia": frequency_detail,
                "url": urljoin(WALKER_CSV, f"{quote(site['code'])}.csv"),
                "advertencias": "; ".join(value for value in row_warnings if value),
            }
        )
    return rows, evidence


def parse_station_catalog(payload: bytes) -> dict[str, dict]:
    text = payload.decode("utf-8", errors="replace")
    stations: dict[str, dict] = {}
    for pane in re.split(r'<div class="tab-pane[^>]*>', text, flags=re.IGNORECASE)[1:]:
        heading = re.search(r"<h4[^>]*>(.*?)<h4>", pane, re.IGNORECASE | re.DOTALL)
        region = strip_html(heading.group(1)) if heading else ""
        for tr in re.findall(
            r'<tr class="default">(.*?)</tr>', pane, re.IGNORECASE | re.DOTALL
        ):
            cells = [
                strip_html(cell)
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.IGNORECASE | re.DOTALL)
            ]
            if len(cells) != 9 or not cells[0].isdigit():
                continue
            stations[cells[0]] = {
                "id": cells[0],
                "name": cells[1],
                "supporting_owner": cells[2],
                "lat": cells[3],
                "lon": cells[4],
                "elev": cells[5],
                "region": region,
            }
    return stations


def strip_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(unescape(text).split())


def target_station(metadata: dict) -> bool:
    region = metadata.get("region", "").lower()
    return any(word in region for word in TARGET_REGION_WORDS)


def list_supplement_files(product: SupplementProduct) -> list[dict]:
    directory_url = urljoin(SUPPLEMENT_ROOT, quote(product.directory, safe="") + "/")
    text = request_bytes(directory_url).decode("utf-8", errors="replace")
    pattern = re.compile(
        r'<a href="([^"]+\.csv)">.*?</a>\s+([0-9]{2}-[A-Za-z]{3}-[0-9]{4}\s+[0-9:]+)\s+([0-9]+)',
        re.IGNORECASE,
    )
    out = []
    for filename, modified, size in pattern.findall(text):
        match = re.search(r"(\d{6})", filename)
        if not match:
            continue
        out.append(
            {
                "product": product.key,
                "directory": product.directory,
                "kind": product.kind,
                "height": product.height,
                "station_code": match.group(1),
                "filename": unescape(filename),
                "modified": modified,
                "size_bytes": int(size),
                "url": urljoin(directory_url, quote(unescape(filename))),
            }
        )
    return out


def parse_supplement_stamp(row: list[str], kind: str) -> datetime | None:
    try:
        if kind == "single":
            return datetime.strptime(row[1].strip(), "%Y-%m-%d-%H-%M")
        return datetime.strptime(
            f"{row[0].strip()} {row[1].strip()}", "%d-%m-%Y %H:%M"
        )
    except (ValueError, IndexError):
        return None


def cadence_label(counter: Counter[int]) -> tuple[str, str]:
    if not counter:
        return "No informado", "sin intervalos consecutivos comprobables"
    common = counter.most_common()
    total = sum(counter.values())
    meaningful = [(minutes, count) for minutes, count in common if count / total >= 0.05]
    detail = "; ".join(f"{minutes} min={count}" for minutes, count in common[:8])
    if len(meaningful) > 1:
        return "Variable", detail
    return f"{common[0][0]} min", detail


def probe_supplement(item: dict, station: dict) -> tuple[list[dict], list[dict]]:
    first: dict[int, datetime] = {}
    first_value: dict[int, str] = {}
    last: dict[int, datetime] = {}
    previous: datetime | None = None
    cadence: Counter[int] = Counter()
    header: list[str] = []
    variable_columns: dict[int, str] = {}
    with session().get(item["url"], stream=True, timeout=(20, 300)) as response:
        response.raise_for_status()
        lines = (
            line.decode("utf-8-sig", errors="replace")
            for line in response.iter_lines(decode_unicode=False)
        )
        reader = csv.reader(lines)
        header = next(reader)
        if item["kind"] == "single":
            original = header[2] if len(header) > 2 else item["product"]
            if item["product"] == "HR":
                variable_columns = {2: "Humedad"}
            elif item["product"] == "Ts":
                variable_columns = {2: "Temperatura"}
            else:
                variable_columns = {2: "Radiación solar"}
        else:
            original = "; ".join(header[2:4])
            variable_columns = {2: "Dirección del viento", 3: "Intensidad del viento"}
        for row in reader:
            stamp = parse_supplement_stamp(row, item["kind"])
            if stamp is None:
                continue
            if previous is not None:
                delta = int((stamp - previous).total_seconds() // 60)
                if 0 < delta <= 24 * 60:
                    cadence[delta] += 1
            previous = stamp
            for index in variable_columns:
                if index < len(row) and valid_for_variable(
                    row[index], variable_columns[index]
                ):
                    first.setdefault(index, stamp)
                    first_value.setdefault(index, row[index].strip())
                    last[index] = stamp
    frequency, detail = cadence_label(cadence)
    rows: list[dict] = []
    evidence: list[dict] = []
    for index, variable in variable_columns.items():
        first_stamp = first.get(index)
        if first_stamp is None:
            continue
        last_stamp = last.get(index)
        utc = "UTC" if item["kind"] == "wind" else None
        rows.append(
            {
                "nombre_estacion": station["name"],
                "id": station["id"],
                "fuente_institucion": "MINENERGIA",
                "latitud": station["lat"],
                "longitud": station["lon"],
                "altitud_snm_m": station["elev"],
                "variable": variable,
                "tipo_variable": "Meteorológica",
                "altura_sensor_suelo_m": item["height"],
                "frecuencia_muestreo": frequency,
                "primera_fecha_disponible": iso_stamp(first_stamp, utc),
                "datos_disponibles_actualmente": current_state(last_stamp),
                "readme_descarga": README_PATH,
            }
        )
        evidence.append(
            {
                "publicacion": "mediciones_dmc",
                "id": station["id"],
                "archivo": item["filename"],
                "columna": header[index] if index < len(header) else str(index),
                "variable_normalizada": variable,
                "altura_m": item["height"],
                "fuente_altura": (
                    "nombre_directorio" if item["height"] != "No informado" else "no_publicada"
                ),
                "primera_fecha_con_valor": iso_stamp(first_stamp, utc),
                "primer_valor_verificado": first_value.get(index, ""),
                "ultima_fecha_con_valor_en_cola": iso_stamp(last_stamp, utc) if last_stamp else "",
                "frecuencia": frequency,
                "detalle_frecuencia": detail,
                "url": item["url"],
                "advertencias": (
                    "El directorio se llama mediciones_dmc, pero la fuente se atribuye a MINENERGIA. "
                    + ("El producto indica promedio de 2 minutos; la frecuencia registrada corresponde a las marcas temporales."
                       if item["product"] == "viento_prom_2min" else "")
                ).strip(),
            }
        )
    return rows, evidence


def merge_duplicate_rows(rows: Iterable[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    key_fields = [
        "nombre_estacion",
        "id",
        "fuente_institucion",
        "latitud",
        "longitud",
        "altitud_snm_m",
        "variable",
        "tipo_variable",
        "altura_sensor_suelo_m",
        "frecuencia_muestreo",
    ]
    for row in rows:
        grouped[tuple(row[field] for field in key_fields)].append(row)
    merged = []
    for values in grouped.values():
        base = values[0].copy()
        base["primera_fecha_disponible"] = min(
            row["primera_fecha_disponible"] for row in values
        )
        states = {row["datos_disponibles_actualmente"] for row in values}
        base["datos_disponibles_actualmente"] = (
            "Sí" if "Sí" in states else "No" if states == {"No"} else "No determinado"
        )
        merged.append(base)
    return sorted(
        merged,
        key=lambda row: (
            row["id"],
            row["variable"],
            row["altura_sensor_suelo_m"],
            row["frecuencia_muestreo"],
        ),
    )


def write_csv(path: Path, rows: Iterable[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def haversine_km(a: dict, b: dict) -> float:
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [float(a["latitud"]), float(a["longitud"]), float(b["latitud"]), float(b["longitud"])],
    )
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(value))


def compare_publications(walker_rows: list[dict], supplement_rows: list[dict]) -> list[dict]:
    walker_stations = {row["id"]: row for row in walker_rows}
    supplement_stations = {row["id"]: row for row in supplement_rows}
    pairs = []
    for left in walker_stations.values():
        for right in supplement_stations.values():
            try:
                distance = haversine_km(left, right)
            except (TypeError, ValueError):
                continue
            if distance <= 5:
                shared = sorted(
                    {r["variable"] for r in walker_rows if r["id"] == left["id"]}
                    & {r["variable"] for r in supplement_rows if r["id"] == right["id"]}
                )
                pairs.append(
                    {
                        "id_walker": left["id"],
                        "nombre_walker": left["nombre_estacion"],
                        "id_mediciones_dmc": right["id"],
                        "nombre_mediciones_dmc": right["nombre_estacion"],
                        "distancia_km": f"{distance:.3f}",
                        "variables_comunes": "; ".join(shared),
                        "decision": (
                            "No consolidar automáticamente: IDs y productos distintos; "
                            "la proximidad no demuestra igualdad de series."
                        ),
                    }
                )
    return sorted(pairs, key=lambda row: float(row["distancia_km"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-metadata", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("--workers debe estar entre 1 y 4")

    RAW.mkdir(parents=True, exist_ok=True)
    walker_payload = cached_bytes(
        RAW / "siteinfo_walker.json.gz", WALKER_META, args.refresh_metadata
    )
    giz_payload = cached_bytes(
        RAW / "estaciones_portal_minenergia.json.gz", MINENERGIA_GIZ, args.refresh_metadata
    )
    catalog_payload = cached_bytes(
        RAW / "catastro_estaciones_nacional.html.gz", STATION_CATALOG, args.refresh_metadata
    )
    walker_meta = json.loads(walker_payload)
    giz_meta = json.loads(giz_payload)
    station_catalog = parse_station_catalog(catalog_payload)

    target_sites = [site for site in walker_meta["sites"] if target_walker_site(site)]
    print(f"walker: {len(target_sites)} estaciones objetivo", flush=True)

    walker_rows: list[dict] = []
    evidence: list[dict] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe_walker, site): site for site in target_sites}
        for future in as_completed(futures):
            site = futures[future]
            try:
                rows, proof = future.result()
                walker_rows.extend(rows)
                evidence.extend(proof)
                print(f"walker {site['code']}: {len(rows)} filas", flush=True)
            except Exception as exc:  # registra y continúa con las demás estaciones
                failures.append(
                    {"publicacion": "walker", "id": site["code"], "error": repr(exc)}
                )
                print(f"walker {site['code']}: ERROR {exc}", file=sys.stderr, flush=True)

    inventory: list[dict] = []
    for product in PRODUCTS:
        files = list_supplement_files(product)
        inventory.extend(files)
        print(f"{product.key}: {len(files)} archivos publicados", flush=True)
    for item in inventory:
        metadata = station_catalog.get(item["station_code"])
        item["region_catalogo_auxiliar"] = metadata["region"] if metadata else "No informado"
        item["incluido"] = "Sí" if metadata and target_station(metadata) else "No"
        item["motivo_exclusion"] = (
            "" if item["incluido"] == "Sí" else "fuera del área o sin cruce de metadatos"
        )
    target_files = [item for item in inventory if item["incluido"] == "Sí"]
    print(f"mediciones_dmc: {len(target_files)} archivos objetivo", flush=True)

    supplement_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(probe_supplement, item, station_catalog[item["station_code"]]): item
            for item in target_files
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                rows, proof = future.result()
                supplement_rows.extend(rows)
                evidence.extend(proof)
                print(f"mediciones_dmc {item['filename']}: {len(rows)} filas", flush=True)
            except Exception as exc:
                failures.append(
                    {
                        "publicacion": "mediciones_dmc",
                        "id": item["station_code"],
                        "error": f"{item['filename']}: {exc!r}",
                    }
                )
                print(f"{item['filename']}: ERROR {exc}", file=sys.stderr, flush=True)

    rows = merge_duplicate_rows(walker_rows + supplement_rows)
    write_csv(OUTPUT, rows, COLUMNS)
    write_csv(
        RAW / "inventario_archivos_minenergia.csv",
        inventory,
        [
            "product",
            "directory",
            "station_code",
            "filename",
            "modified",
            "size_bytes",
            "url",
            "region_catalogo_auxiliar",
            "incluido",
            "motivo_exclusion",
        ],
    )
    evidence_fields = [
        "publicacion",
        "id",
        "archivo",
        "columna",
        "variable_normalizada",
        "altura_m",
        "fuente_altura",
        "primera_fecha_con_valor",
        "primer_valor_verificado",
        "ultima_fecha_con_valor_en_cola",
        "frecuencia",
        "detalle_frecuencia",
        "url",
        "advertencias",
    ]
    write_csv(RAW / "sondeo_series.csv", evidence, evidence_fields)
    write_csv(
        RAW / "conflictos_alturas_walker.csv",
        [row for row in evidence if "conflicto_altura" in row.get("advertencias", "")],
        evidence_fields,
    )
    write_csv(RAW / "fallos.csv", failures, ["publicacion", "id", "error"])
    comparisons = compare_publications(walker_rows, supplement_rows)
    write_csv(
        RAW / "comparacion_publicaciones.csv",
        comparisons,
        [
            "id_walker",
            "nombre_walker",
            "id_mediciones_dmc",
            "nombre_mediciones_dmc",
            "distancia_km",
            "variables_comunes",
            "decision",
        ],
    )
    write_csv(
        RAW / "metadatos_estaciones_walker.csv",
        (
            {
                "id": site["code"],
                "nombre": site["info"].get("name2") or site["info"].get("name"),
                "region": site["info"]["location"].get("region", ""),
                "latitud": site["info"]["location"].get("lat", ""),
                "longitud": site["info"]["location"].get("lon", ""),
                "altitud_snm_m": site["info"]["location"].get("elev", ""),
                "fecha_inicial_declarada": site["info"].get("initial_date", ""),
                "fecha_final_declarada": site["info"].get("final_date", ""),
            }
            for site in target_sites
        ),
        [
            "id",
            "nombre",
            "region",
            "latitud",
            "longitud",
            "altitud_snm_m",
            "fecha_inicial_declarada",
            "fecha_final_declarada",
        ],
    )
    mirror_codes = {item.get("name") for item in giz_meta if isinstance(item, dict)}
    walker_codes = {site["code"] for site in walker_meta["sites"]}
    comparison_summary = {
        "fecha_consulta": NOW_LOCAL.isoformat(),
        "ventana_actual_inicio": CURRENT_START.isoformat(),
        "ventana_actual_fin": CURRENT_END.isoformat(),
        "sitios_walker_total": len(walker_meta["sites"]),
        "sitios_portal_minenergia_total": len(giz_meta),
        "codigos_portal_minenergia_con_coincidencia_en_walker": len(mirror_codes & walker_codes),
        "estaciones_walker_objetivo": len(target_sites),
        "archivos_mediciones_dmc_objetivo": len(target_files),
        "filas_catalogo": len(rows),
        "fallos": len(failures),
        "pares_geograficos_a_revisar": len(comparisons),
    }
    (RAW / "resumen_ejecucion.json").write_text(
        json.dumps(comparison_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(comparison_summary, ensure_ascii=False, indent=2), flush=True)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
