#!/usr/bin/env python3
"""Construye el catálogo horario SINCA verificando los datos descargables.

La fecha inicial de la ficha se usa solamente como punto de partida. La fecha
incorporada al catálogo es la primera marca temporal con un valor numérico en
el CSV oficial (validado, preliminar o no validado).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import io
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests


PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "datos_originales" / "sinca"
OUTPUT = PROJECT / "catalogo_sinca.csv"
README_PATH = "fuentes/sinca/README.md"
BASE = "https://sinca.mma.gob.cl"
REGIONS = {
    "XV": "Arica y Parinacota",
    "I": "Tarapacá",
    "II": "Antofagasta",
    "III": "Atacama",
}
TODAY = date.today()
CURRENT_START = TODAY - timedelta(days=7)
CURRENT_END = TODAY - timedelta(days=1)

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

VARIABLES = {
    "PM25": ("MP2.5", "Calidad del aire"),
    "PM10": ("MP10", "Calidad del aire"),
    "0001": ("SO2", "Calidad del aire"),
    "0NOX": ("NOX", "Calidad del aire"),
    "0002": ("NO", "Calidad del aire"),
    "0003": ("NO2", "Calidad del aire"),
    "0008": ("O3", "Calidad del aire"),
    "0004": ("CO", "Calidad del aire"),
    "00Pb": ("Pb", "Calidad del aire"),
    "GLOB": ("Radiación solar", "Meteorológica"),
    "RAIN": ("Precipitación", "Meteorológica"),
    "TEMP": ("Temperatura", "Meteorológica"),
    "RHUM": ("Humedad", "Meteorológica"),
    "PRES": ("Presión", "Meteorológica"),
    "WSPD": ("Intensidad del viento", "Meteorológica"),
    "WDIR": ("Dirección del viento", "Meteorológica"),
}

# Series en las que el CGI contiene valores anteriores que no corresponden al
# emplazamiento actual. Se comprobó que MP2.5 de Arica devuelve en 2000 una
# secuencia artificial, aunque la estación comenzó a operar en 2013.
START_AT_OPERATION = {("232", "PM25")}

# Estas macros contienen registros de 1990 ajenos al período publicado de la
# serie actual. Se comienza en la fecha específica declarada para la variable.
START_AT_DECLARED = {
    ("176", "0001"),
    ("176", "PM10"),
    ("176", "PM25"),
    ("255", "PM25"),
}


def http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "catalogo-estaciones-sinca/1.0"})
    return session


def request_bytes(url: str, *, params: dict | None = None, attempts: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with http_session().get(url, params=params, timeout=(20, 180)) as response:
                response.raise_for_status()
                return response.content
        except requests.RequestException as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.0 + attempt)
    assert last is not None
    raise last


def cached_page(path: Path, url: str, refresh: bool) -> str:
    if path.exists() and not refresh:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    payload = request_bytes(url)
    text = payload.decode("utf-8", errors="replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as handle:
        handle.write(text)
    return text


def strip_tags(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_region_page(text: str, region_code: str) -> list[dict]:
    stations: dict[str, dict] = {}
    for station_id, name in re.findall(
        r'/index\.php/estacion/index/id/(\d+)">([^<]+)', text, re.IGNORECASE
    ):
        stations[station_id] = {
            "id": station_id,
            "name_list": html.unescape(name).strip(),
            "region_code": region_code,
            "region": REGIONS[region_code],
            "url": f"{BASE}/index.php/estacion/index/id/{station_id}",
        }
    return list(stations.values())


def parse_date_dmy(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def yymmdd_to_date(value: str) -> date | None:
    if not re.fullmatch(r"\d{6}", value):
        return None
    year2, month, day = int(value[:2]), int(value[2:4]), int(value[4:6])
    year = 2000 + year2 if year2 <= TODAY.year % 100 else 1900 + year2
    try:
        return date(year, month, day)
    except ValueError:
        return None


def date_to_yymmdd(value: date) -> str:
    return value.strftime("%y%m%d")


def query_parts(href: str) -> dict[str, str]:
    href = html.unescape(href)
    parsed = urlparse("https:" + href if href.startswith("//") else href)
    return {key: values[0] for key, values in parse_qs(parsed.query).items() if values}


def parse_general(text: str) -> dict:
    name_match = re.search(r"<h1>Estaci(?:&oacute;|ó)n\s+(.*?)</h1>", text, re.I | re.S)
    general = {
        strip_tags(key): strip_tags(value)
        for key, value in re.findall(
            r'<th class="right">(.*?)</th>\s*<td class="left">(.*?)</td>',
            text,
            re.I | re.S,
        )
    }
    utm = re.search(r"(\d+(?:\.\d+)?)\s*E\s+(\d+(?:\.\d+)?)\s*N", general.get("Coordenadas UTM", ""))
    zone = re.search(r"\d+", general.get("Huso horario", ""))
    published_e = float(utm.group(1)) if utm else None
    published_n = float(utm.group(2)) if utm else None
    utm_e, utm_n = published_e, published_n
    utm_correction = "Ninguna"
    # Una coordenada UTM E válida no tiene siete dígitos y la componente N
    # del norte de Chile sí. La ficha 224 las publica rotuladas al revés.
    if utm_e is not None and utm_n is not None and utm_e > 1_000_000 and utm_n < 1_000_000:
        utm_e, utm_n = utm_n, utm_e
        utm_correction = "Componentes E/N publicadas en orden inverso; se intercambiaron"
    return {
        "name": strip_tags(name_match.group(1)) if name_match else "No informado",
        "owner": general.get("Propietario", "No informado"),
        "operator": general.get("Operador", "No informado"),
        "region_reported": general.get("Región", "No informado"),
        "province": general.get("Provincia", "No informado"),
        "commune": general.get("Comuna", "No informado"),
        "utm_e_publicado": published_e,
        "utm_n_publicado": published_n,
        "utm_e": utm_e,
        "utm_n": utm_n,
        "utm_correction": utm_correction,
        "utm_zone": int(zone.group()) if zone else None,
        "reception": general.get("Recepción de datos", "No informado"),
        "operation_start": general.get("Inicio de operación reportada", "No informado"),
    }


def parse_contaminants(text: str) -> list[dict]:
    start = text.find("Parámetros contaminantes")
    if start < 0:
        return []
    end = text.find("</table>", start)
    section = text[start:end]
    rows = re.findall(r'<tr class="serie([^>]*)>(.*?)</tr>', section, re.I | re.S)
    pending: dict | None = None
    out: list[dict] = []
    for classes, body in rows:
        anchor = re.search(r'<a name="([^"]+)">([^<]+)</a>', body, re.I | re.S)
        if anchor:
            cells = [strip_tags(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", body, re.I | re.S)]
            pending = {
                "original_code": anchor.group(1),
                "original_name": html.unescape(anchor.group(2)).strip(),
                "declared_first": cells[0] if len(cells) > 0 else "",
                "declared_last": cells[1] if len(cells) > 1 else "",
                "technique": cells[2] if len(cells) > 2 else "No informado",
                "height": "No informado",
                "frequency": "60 min",
            }
        if pending and "registros horarios" in strip_tags(body).lower():
            href_match = re.search(r'href="([^"]+pageFrame[^"]+)"', body, re.I | re.S)
            if not href_match:
                continue
            parts = query_parts(href_match.group(1))
            item = pending.copy()
            item.update(
                {
                    "macropath": parts.get("macropath", ""),
                    "macro": parts.get("macro", ""),
                    "hint_from": parts.get("from", ""),
                    "hint_to": parts.get("to", ""),
                    "page_url": "https:" + html.unescape(href_match.group(1)),
                }
            )
            out.append(item)
            pending = None
    return out


def parse_meteorology(text: str) -> list[dict]:
    start = text.find("Parámetros meteorológicos")
    if start < 0:
        return []
    end = text.find("</table>", start)
    section = text[start:end]
    out = []
    for body in re.findall(r'<tr class="serie[^>]*>(.*?)</tr>', section, re.I | re.S):
        anchor = re.search(r'<a name="([^"]+)">([^<]+)</a>', body, re.I | re.S)
        href_match = re.search(r'href="([^"]+pageFrame[^"]+)"', body, re.I | re.S)
        if not anchor or not href_match:
            continue
        cells = [strip_tags(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", body, re.I | re.S)]
        parts = query_parts(href_match.group(1))
        height_match = re.search(r"-?\d+(?:[.,]\d+)?", cells[1] if len(cells) > 1 else "")
        height = height_match.group().replace(",", ".") if height_match else "No informado"
        if height.endswith(".0"):
            height = height[:-2]
        out.append(
            {
                "original_code": anchor.group(1),
                "original_name": html.unescape(anchor.group(2)).strip(),
                "frequency": "60 min",
                "height": height,
                "declared_first": cells[2] if len(cells) > 2 else "",
                "declared_last": cells[3] if len(cells) > 3 else "",
                "technique": cells[4] if len(cells) > 4 else "No informado",
                "macropath": parts.get("macropath", ""),
                "macro": parts.get("macro", ""),
                "hint_from": parts.get("from", ""),
                "hint_to": parts.get("to", ""),
                "page_url": "https:" + html.unescape(href_match.group(1)),
            }
        )
    return out


def utm_wgs84_to_latlon(easting: float, northing: float, zone: int) -> tuple[float, float]:
    """Convierte UTM WGS84 hemisferio sur a grados decimales."""
    a = 6378137.0
    ecc_sq = 0.00669437999014
    k0 = 0.9996
    x = easting - 500000.0
    y = northing - 10000000.0
    ecc_prime_sq = ecc_sq / (1.0 - ecc_sq)
    m = y / k0
    mu = m / (a * (1 - ecc_sq / 4 - 3 * ecc_sq**2 / 64 - 5 * ecc_sq**3 / 256))
    e1 = (1 - math.sqrt(1 - ecc_sq)) / (1 + math.sqrt(1 - ecc_sq))
    j1 = 3 * e1 / 2 - 27 * e1**3 / 32
    j2 = 21 * e1**2 / 16 - 55 * e1**4 / 32
    j3 = 151 * e1**3 / 96
    j4 = 1097 * e1**4 / 512
    fp = mu + j1 * math.sin(2 * mu) + j2 * math.sin(4 * mu) + j3 * math.sin(6 * mu) + j4 * math.sin(8 * mu)
    sin_fp, cos_fp = math.sin(fp), math.cos(fp)
    tan_fp = math.tan(fp)
    n1 = a / math.sqrt(1 - ecc_sq * sin_fp**2)
    r1 = a * (1 - ecc_sq) / (1 - ecc_sq * sin_fp**2) ** 1.5
    t1 = tan_fp**2
    c1 = ecc_prime_sq * cos_fp**2
    d = x / (n1 * k0)
    lat = fp - (n1 * tan_fp / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ecc_prime_sq) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ecc_prime_sq - 3 * c1**2) * d**6 / 720
    )
    lon = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ecc_prime_sq + 24 * t1**2) * d**5 / 120
    ) / cos_fp
    lon_origin = (zone - 1) * 6 - 180 + 3
    return math.degrees(lat), lon_origin + math.degrees(lon)


def full_macro(series: dict) -> str:
    macro = series["macro"]
    # La ficha abre por defecto la rosa de vientos. El mismo selector ofrece
    # una segunda macro con los grados horarios: "Dir. viento - serie de
    # tiempo". Esta es la que corresponde al catálogo de observaciones.
    if series.get("original_code") == "WDIR":
        if macro.endswith(".ic"):
            macro = macro[:-3]
        if not macro.endswith("_spec"):
            macro += "_spec"
    if not macro.endswith(".ic"):
        macro += ".ic"
    return series["macropath"].rstrip("/") + "//" + macro


def valid_value(value: str) -> bool:
    value = value.strip().replace(",", ".")
    if not value:
        return False
    try:
        number = float(value)
    except ValueError:
        return False
    return math.isfinite(number) and number > -900


def valid_series_value(series: dict, value: str) -> bool:
    """Valida faltantes y restricciones físicas inequívocas por variable."""
    if not valid_value(value):
        return False
    number = float(value.strip().replace(",", "."))
    code = series.get("original_code")
    if code == "WDIR":
        return 0.0 <= number <= 360.0
    if code == "PRES":
        return number > 0.0
    if code in {"WSPD", "RAIN"}:
        return number >= 0.0
    if code == "RHUM":
        return 0.0 <= number <= 100.0
    return True


def fetch_data(series: dict, start: date, end: date) -> list[tuple[datetime, list[str]]]:
    # El CGI puede omitir la fila 00:00 cuando `from` coincide exactamente con
    # el primer día solicitado. Se pide un día adicional y los llamadores
    # filtran luego por el intervalo exacto.
    query_start = start - timedelta(days=1)
    params = {
        "outtype": "xcl",
        "macro": full_macro(series),
        "from": date_to_yymmdd(query_start),
        "to": date_to_yymmdd(end),
        "path": "/usr/airviro/data/CONAMA/",
        "lang": "esp",
        "rsrc": "",
        "macropath": "",
    }
    payload = request_bytes(f"{BASE}/cgi-bin/APUB-MMA/apub.tsindico2.cgi", params=params)
    text = payload.decode("utf-8-sig", errors="replace")
    out = []
    for row in csv.reader(io.StringIO(text), delimiter=";"):
        if len(row) < 3 or not re.fullmatch(r"\d{6}", row[0].strip()):
            continue
        day = yymmdd_to_date(row[0].strip())
        hour_text = row[1].strip().zfill(4)
        if day is None or not re.fullmatch(r"\d{4}", hour_text):
            continue
        hour, minute = int(hour_text[:2]), int(hour_text[2:])
        if hour == 24:
            stamp = datetime.combine(day + timedelta(days=1), datetime.min.time())
        elif 0 <= hour <= 23 and 0 <= minute <= 59:
            stamp = datetime(day.year, day.month, day.day, hour, minute)
        else:
            continue
        out.append((stamp, row[2:]))
    return out


def first_hint(series: dict, station: dict) -> date:
    operation = parse_date_dmy(station.get("operation_start", ""))
    declared = parse_date_dmy(series.get("declared_first", ""))
    link_hint = yymmdd_to_date(series.get("hint_from", ""))
    declared_start = None
    if declared and date(1980, 1, 1) <= declared <= TODAY:
        # Varias descargas comienzan a las 23:00 del día anterior a la fecha
        # de la tabla; se amplía un día y se conserva el sello real del CSV.
        declared_start = declared - timedelta(days=1)
    valid_starts = [
        value
        for value in (declared_start, link_hint)
        if value and date(1980, 1, 1) <= value <= TODAY
    ]
    metadata_start = min(valid_starts) if valid_starts else date(1980, 1, 1)
    key = (station["id"], series["original_code"])
    if key in START_AT_DECLARED and declared_start:
        return declared_start
    if key in START_AT_OPERATION:
        if operation and date(1980, 1, 1) <= operation <= TODAY:
            return max(metadata_start, operation)
    return metadata_start


def probe_series(station: dict, series: dict) -> dict:
    start = first_hint(series, station)
    first: datetime | None = None
    years_checked = 0
    for year in range(start.year, TODAY.year + 1):
        interval_start = start if year == start.year else date(year, 1, 1)
        interval_end = min(date(year, 12, 31), TODAY)
        records = fetch_data(series, interval_start, interval_end)
        years_checked += 1
        candidates = [
            stamp
            for stamp, values in records
            if interval_start <= stamp.date() <= interval_end
            and any(valid_series_value(series, value) for value in values)
        ]
        if candidates:
            first = min(candidates)
            break
    current_records = fetch_data(series, CURRENT_START, CURRENT_END)
    current_values = [
        stamp
        for stamp, values in current_records
        if CURRENT_START <= stamp.date() <= CURRENT_END
        and any(valid_series_value(series, value) for value in values)
    ]
    return {
        "station_id": station["id"],
        "original_code": series["original_code"],
        "macro_full": full_macro(series),
        "first_verified": first.isoformat(timespec="minutes") if first else "",
        "current_available": "Sí" if current_values else "No",
        "last_in_current_window": max(current_values).isoformat(timespec="minutes") if current_values else "",
        "years_checked": years_checked,
        "window_start": CURRENT_START.isoformat(),
        "window_end": CURRENT_END.isoformat(),
        "status": "ok" if first else "sin_valores_verificados",
    }


def read_existing_probes(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            (row["station_id"], row["macro_full"]): row
            for row in csv.DictReader(handle)
            if row.get("window_start") == CURRENT_START.isoformat()
            and row.get("window_end") == CURRENT_END.isoformat()
        }


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-metadata", action="store_true")
    parser.add_argument("--refresh-series", action="store_true")
    parser.add_argument("--refresh-direction", action="store_true")
    parser.add_argument(
        "--refresh-code",
        action="append",
        default=[],
        choices=sorted(VARIABLES),
        help="Repite el sondeo sólo para el código SINCA indicado; se puede repetir",
    )
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("--workers debe estar entre 1 y 4")

    RAW.mkdir(parents=True, exist_ok=True)
    stations: list[dict] = []
    for code in REGIONS:
        text = cached_page(
            RAW / f"region_{code}.html.gz",
            f"{BASE}/index.php/region/index/id/{code}",
            args.refresh_metadata,
        )
        stations.extend(parse_region_page(text, code))
    stations.sort(key=lambda item: (item["region_code"], int(item["id"])))
    print(f"estaciones={len(stations)}", flush=True)

    def enrich(station: dict) -> tuple[dict, list[dict]]:
        text = cached_page(
            RAW / "fichas" / f"estacion_{station['id']}.html.gz",
            station["url"],
            args.refresh_metadata,
        )
        general = parse_general(text)
        station = {**station, **general}
        if station["utm_e"] is not None and station["utm_n"] is not None and station["utm_zone"] is not None:
            station["lat"], station["lon"] = utm_wgs84_to_latlon(
                station["utm_e"], station["utm_n"], station["utm_zone"]
            )
        else:
            station["lat"], station["lon"] = None, None
        series = parse_contaminants(text) + parse_meteorology(text)
        for item in series:
            item.update({"station_id": station["id"], "station_name": station["name"]})
        return station, series

    enriched: list[dict] = []
    series_all: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(enrich, station): station for station in stations}
        for future in as_completed(futures):
            station, series = future.result()
            enriched.append(station)
            series_all.extend(series)
            print(f"ficha {station['id']} {station['name']}: {len(series)} series horarias", flush=True)
    enriched.sort(key=lambda item: int(item["id"]))
    station_by_id = {item["id"]: item for item in enriched}
    relevant = [
        item
        for item in series_all
        if item["original_code"] in VARIABLES and item.get("macropath") and item.get("macro")
    ]
    print(f"series_horarias_relevantes={len(relevant)}", flush=True)

    probe_path = RAW / "sondeo_series.csv"
    existing = {} if args.refresh_series else read_existing_probes(probe_path)
    probes: list[dict] = []
    failures: list[dict] = []
    pending = []
    for item in relevant:
        key = (item["station_id"], full_macro(item))
        force_refresh = (
            (args.refresh_direction and item["original_code"] == "WDIR")
            or item["original_code"] in args.refresh_code
        )
        if key in existing and not force_refresh:
            probes.append(existing[key])
        else:
            pending.append(item)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(probe_series, station_by_id[item["station_id"]], item): item
            for item in pending
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
                probes.append(result)
                print(
                    f"sondeo {item['station_id']} {item['original_code']} {item['height']}: "
                    f"{result['first_verified'] or 'sin datos'} actual={result['current_available']}",
                    flush=True,
                )
            except Exception as exc:
                failures.append(
                    {
                        "station_id": item["station_id"],
                        "original_code": item["original_code"],
                        "macro_full": full_macro(item),
                        "error": repr(exc),
                    }
                )
                print(f"ERROR {item['station_id']} {item['original_code']}: {exc}", file=sys.stderr)
    probe_by_key = {(row["station_id"], row["macro_full"]): row for row in probes}

    catalog: list[dict] = []
    inventory: list[dict] = []
    for item in relevant:
        station = station_by_id[item["station_id"]]
        normalized, kind = VARIABLES[item["original_code"]]
        probe = probe_by_key.get((item["station_id"], full_macro(item)))
        inventory_row = {
            **item,
            "macro_full": full_macro(item),
            "variable_normalized": normalized,
            "type_normalized": kind,
            "owner": station["owner"],
            "operator": station["operator"],
            "region": station["region"],
            "province": station["province"],
            "commune": station["commune"],
            "utm_e": station["utm_e"],
            "utm_n": station["utm_n"],
            "utm_zone": station["utm_zone"],
            "lat": f"{station['lat']:.6f}" if station["lat"] is not None else "No informado",
            "lon": f"{station['lon']:.6f}" if station["lon"] is not None else "No informado",
            "operation_start": station["operation_start"],
            "reception": station["reception"],
            "first_verified": probe["first_verified"] if probe else "",
            "current_available": probe["current_available"] if probe else "No determinado",
            "probe_status": probe["status"] if probe else "fallo",
        }
        inventory.append(inventory_row)
        if not probe or probe["status"] != "ok":
            continue
        catalog.append(
            {
                "nombre_estacion": station["name"],
                "id": station["id"],
                "fuente_institucion": "SINCA / MMA",
                "latitud": inventory_row["lat"],
                "longitud": inventory_row["lon"],
                "altitud_snm_m": "No informado",
                "variable": normalized,
                "tipo_variable": kind,
                "altura_sensor_suelo_m": item["height"],
                "frecuencia_muestreo": item["frequency"],
                "primera_fecha_disponible": probe["first_verified"],
                "datos_disponibles_actualmente": probe["current_available"],
                "readme_descarga": README_PATH,
            }
        )
    catalog.sort(
        key=lambda row: (int(row["id"]), row["variable"], row["altura_sensor_suelo_m"])
    )
    keys = [
        (row["id"], row["variable"], row["altura_sensor_suelo_m"], row["frecuencia_muestreo"])
        for row in catalog
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("El catálogo SINCA contiene combinaciones duplicadas")

    probe_fields = [
        "station_id",
        "original_code",
        "macro_full",
        "first_verified",
        "current_available",
        "last_in_current_window",
        "years_checked",
        "window_start",
        "window_end",
        "status",
    ]
    write_csv(probe_path, sorted(probes, key=lambda row: (int(row["station_id"]), row["macro_full"])), probe_fields)
    write_csv(RAW / "fallos.csv", failures, ["station_id", "original_code", "macro_full", "error"])
    station_fields = [
        "id", "name", "owner", "operator", "region", "province", "commune",
        "utm_e_publicado", "utm_n_publicado", "utm_e", "utm_n", "utm_zone",
        "utm_correction", "lat", "lon", "reception", "operation_start", "url",
    ]
    station_rows = []
    for station in enriched:
        row = station.copy()
        row["lat"] = f"{station['lat']:.6f}" if station["lat"] is not None else "No informado"
        row["lon"] = f"{station['lon']:.6f}" if station["lon"] is not None else "No informado"
        station_rows.append(row)
    write_csv(RAW / "inventario_estaciones.csv", station_rows, station_fields)
    inventory_fields = [
        "station_id", "station_name", "original_code", "original_name", "variable_normalized",
        "type_normalized", "frequency", "height", "declared_first", "declared_last", "technique",
        "macropath", "macro", "macro_full", "hint_from", "hint_to", "page_url", "owner", "operator",
        "region", "province", "commune", "utm_e", "utm_n", "utm_zone", "lat", "lon",
        "operation_start", "reception", "first_verified", "current_available", "probe_status",
    ]
    write_csv(RAW / "inventario_series.csv", inventory, inventory_fields)
    write_csv(OUTPUT, catalog, COLUMNS)
    summary = {
        "fecha_consulta": TODAY.isoformat(),
        "ventana_actual_inicio": CURRENT_START.isoformat(),
        "ventana_actual_fin": CURRENT_END.isoformat(),
        "estaciones": len(enriched),
        "series_horarias_relevantes": len(relevant),
        "series_con_datos_verificados": len(catalog),
        "fallos": len(failures),
    }
    (RAW / "resumen_ejecucion.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
