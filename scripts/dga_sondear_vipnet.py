#!/usr/bin/env python3
"""Sondea series meteorológicas DGA publicadas por VIPNET.

Guarda solamente evidencia resumida (primera/ultima fecha, frecuencia y
disponibilidad reciente), no las observaciones.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datos_originales" / "dga"
OUTPUT = RAW / "vipnet_resumen_series.json"
STATIONS_OUTPUT = RAW / "vipnet_estaciones_variables.json"
BASE = "https://vipnet.mop.gob.cl"
STATIONS_URL = f"{BASE}/v1/vipnet/estaciones/valor"
VALUES_URL = f"{BASE}/v1/vipnet/estacion/valores"
TZ = ZoneInfo("America/Santiago")
TARGET_REGIONS = {1, 2, 3, 15}
VARIABLES = {
    0: ("Precipitación", 0.0, 500.0),
    1: ("Temperatura", -80.0, 60.0),
    4: ("Humedad", 0.0, 100.0),
    5: ("Intensidad del viento", 0.0, 100.0),
}
HEADERS = {
    "Origin": BASE,
    "Referer": f"{BASE}/",
    "User-Agent": "MIRA-Chile/1.0 (catalogo publico; contacto: repositorio GitHub)",
    "Content-Type": "application/json",
}
OBS_RE = re.compile(
    rb'"fecha":\{"\$date":"([^"]+)"\}.*?"instantaneo":(-?[0-9.eE+]+|null)',
    re.S,
)
_local = threading.local()


def session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers.update(HEADERS)
    return _local.session


def request_json(url: str, payload: dict, attempts: int = 4) -> dict:
    for attempt in range(attempts):
        try:
            response = session().post(url, json=payload, timeout=(20, 120))
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt + 1 == attempts:
                raise
            time.sleep(2**attempt)
    raise AssertionError("inalcanzable")


def fetch_stations() -> list[dict]:
    rows: list[dict] = []
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    for variable_id, (variable, _, _) in VARIABLES.items():
        result = request_json(
            STATIONS_URL,
            {
                "tipoEstacion": variable_id,
                "currentTabIndex": 0,
                "fetchHour": 0,
                "fetchDay": today.strftime("%Y-%m-%d"),
                "hoursRange": 24 * 7,
                "mapStatistic": 0,
            },
        )
        for station in result.get("data", []):
            region = station.get("regionEstacion", station.get("region"))
            if region not in TARGET_REGIONS:
                continue
            rows.append({
                **station,
                "tipoEstacion": variable_id,
                "variable": variable,
                # El valor agregado sólo se usa como prueba de existencia en
                # la ventana; los controles físicos se aplican a observaciones.
                "datos_ultimos_7_dias_completos": isinstance(station.get("value"), (int, float)),
            })
    rows.sort(key=lambda row: (row["codigoEstacion"], row["tipoEstacion"]))
    return rows


def first_observations(station_code: str, variable_id: int, limit: int = 20) -> list[tuple[str, float | None]]:
    """Lee el comienzo ordenado de la respuesta histórica y cierra el flujo."""
    payload = {
        "codigoEstacion": station_code,
        "tipoEstacion": variable_id,
        "fetchHour": 23,
        "fetchDay": datetime.now(TZ).strftime("%Y-%m-%d"),
        "hoursRange": 1_000_000,
    }
    for attempt in range(4):
        try:
            with session().post(VALUES_URL, json=payload, timeout=(20, 180), stream=True) as response:
                response.raise_for_status()
                buffer = b""
                found: list[tuple[str, float | None]] = []
                for chunk in response.iter_content(32768):
                    buffer += chunk
                    found = [
                        (m.group(1).decode(), None if m.group(2) == b"null" else float(m.group(2)))
                        for m in OBS_RE.finditer(buffer)
                    ]
                    if len(found) >= limit or len(buffer) >= 2_000_000:
                        break
                return found
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    return []


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def valid_value(variable_id: int, value: object) -> bool:
    if not isinstance(value, (int, float)):
        return False
    _, low, high = VARIABLES[variable_id]
    return low <= float(value) <= high


def infer_frequency(dates: list[datetime]) -> str:
    unique = sorted(set(dates))
    gaps = [int((b - a).total_seconds() // 60) for a, b in zip(unique, unique[1:])]
    gaps = [gap for gap in gaps if 0 < gap <= 24 * 60]
    if not gaps:
        return "No informado"
    # La mediana resiste vacíos ocasionales sin inventar la frecuencia nominal.
    minutes = int(round(statistics.median(gaps)))
    return f"{minutes} min"


def probe(row: dict) -> dict:
    code = row["codigoEstacion"]
    variable_id = int(row["tipoEstacion"])
    early = first_observations(code, variable_id)
    early_valid = [(parse_date(date), value) for date, value in early if valid_value(variable_id, value)]

    dates_for_frequency = [date for date, _ in early_valid]
    return {
        "codigoEstacion": code,
        "tipoEstacion": variable_id,
        "variable": VARIABLES[variable_id][0],
        "primera_fecha": early_valid[0][0].isoformat() if early_valid else None,
        "ultima_fecha_reciente": None,
        "frecuencia_observada": infer_frequency(dates_for_frequency),
        "datos_ultimos_7_dias_completos": row["datos_ultimos_7_dias_completos"],
        "n_observaciones_recientes_validas": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, help="Limita series para pruebas")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    if args.resume and STATIONS_OUTPUT.exists():
        stations = json.loads(STATIONS_OUTPUT.read_text(encoding="utf-8"))
    else:
        stations = fetch_stations()
        STATIONS_OUTPUT.write_text(json.dumps(stations, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.limit:
        stations = stations[: args.limit]

    completed: dict[tuple[str, int], dict] = {}
    if args.resume and OUTPUT.exists():
        for row in json.loads(OUTPUT.read_text(encoding="utf-8")):
            completed[(row["codigoEstacion"], int(row["tipoEstacion"]))] = row
    pending = [row for row in stations if (row["codigoEstacion"], row["tipoEstacion"]) not in completed]
    print(f"series={len(stations)} pendientes={len(pending)} workers={args.workers}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, row): row for row in pending}
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            row = futures[future]
            key = (row["codigoEstacion"], row["tipoEstacion"])
            try:
                completed[key] = future.result()
            except Exception as exc:  # conserva el avance y deja el error auditable
                completed[key] = {
                    "codigoEstacion": key[0], "tipoEstacion": key[1],
                    "variable": row["variable"], "error": f"{type(exc).__name__}: {exc}",
                }
            if number % 20 == 0 or number == len(pending):
                data = sorted(completed.values(), key=lambda x: (x["codigoEstacion"], x["tipoEstacion"]))
                OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                errors = sum("error" in item for item in data)
                print(f"{number}/{len(pending)} guardadas={len(data)} errores={errors}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
