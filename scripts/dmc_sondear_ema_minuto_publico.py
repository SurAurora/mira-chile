#!/usr/bin/env python3
"""Sondea el primer ZIP mensual EMA público de estaciones DMC pendientes."""

from __future__ import annotations

import argparse
import csv
import io
import os
import random
import tempfile
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path("/home/alagos/catalogo_estaciones")
RAW = ROOT / "datos_originales" / "dmc"
BASE = "https://climatologia.meteochile.gob.cl/application/datos/getDatosEma"
_LOCAL = threading.local()

DEFINITIONS = {
    "Temperatura": {
        "ts": ("Temperatura", "1.5", -80, 80),
        "ts02": ("Temperatura", "2", -80, 80),
        "ts10": ("Temperatura", "10", -80, 80),
        "ts30": ("Temperatura", "30", -80, 80),
    },
    "PresionHumedad": {
        "hr": ("Humedad", "No informado", 0, 100),
        "p0": ("Presión", "No informado", 1, 1200),
    },
    "RadiacionGlobal": {
        "radiacionGlobalInst": ("Radiación solar", "No informado", -50, 2000),
    },
    "Viento": {
        "ddInst": ("Dirección del viento", "No informado", 0, 360),
        "ffInst": ("Intensidad del viento", "No informado", 0, 300),
        "ff01MinutosMax": ("Ráfaga del viento", "No informado", 0, 300),
        "ff02MinutosMax": ("Ráfaga del viento", "No informado", 0, 300),
        "ff10MinutosMax": ("Ráfaga del viento", "No informado", 0, 300),
    },
}


def session() -> requests.Session:
    if not hasattr(_LOCAL, "session"):
        _LOCAL.session = requests.Session()
        _LOCAL.session.headers["User-Agent"] = "catalogo-estaciones-DMC-EMA/1.0"
    return _LOCAL.session


def months(start: datetime, end: datetime) -> list[str]:
    year, month = start.year, start.month
    out = []
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def number(text: str | None) -> float | None:
    try:
        return float((text or "").strip().replace(",", "."))
    except ValueError:
        return None


def inspect(content: bytes, group: str) -> tuple[list[dict[str, object]], str] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir() and item.filename.endswith(".csv")]
            if len(members) != 1 or archive.testzip() is not None:
                return None
            text = archive.read(members[0]).decode("utf-8-sig", errors="replace")
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return None
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    stamps = []
    for row in rows:
        try:
            stamps.append(datetime.fromisoformat(row.get("momento", "")))
        except ValueError:
            pass
    deltas = [int((b - a).total_seconds() // 60) for a, b in zip(stamps, stamps[1:]) if b > a]
    deltas = [value for value in deltas if 0 < value <= 60]
    frequency = f"{Counter(deltas).most_common(1)[0][0]} min" if deltas else "No determinado"
    found: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        try:
            stamp = datetime.fromisoformat(row.get("momento", ""))
        except ValueError:
            continue
        for field, (variable, height, low, high) in DEFINITIONS[group].items():
            value = number(row.get(field))
            if value is None or not low <= value <= high:
                continue
            key = (variable, height)
            candidate = {
                "variable": variable, "altura_sensor_suelo_m": height,
                "primera_fecha_verificada": stamp.isoformat(timespec="seconds"),
                "primer_valor_verificado": value, "campo_original": field,
                "frecuencia_muestreo": frequency,
            }
            if key not in found or candidate["primera_fecha_verificada"] < found[key]["primera_fecha_verificada"]:
                found[key] = candidate
    return list(found.values()), members[0].filename


def probe(task: tuple[dict[str, str], str], end: datetime, pause: float, timeout: float) -> tuple[list[dict], list[dict]]:
    station, group = task
    code = station["codigo_nacional"]
    start = datetime.strptime(station["fecha_creacion"], "%d-%m-%Y")
    attempts = []
    for month in months(start, end):
        filename = f"{code}_{month}_{group}.csv.zip"
        url = f"{BASE}/{code}/{filename}"
        status = ""
        size = 0
        try:
            time.sleep(pause + random.uniform(0, pause))
            response = session().get(url, timeout=timeout)
            size = len(response.content)
            if response.status_code != 200:
                status = f"http_{response.status_code}"
            elif not response.content.startswith(b"PK\x03\x04"):
                status = "sin_zip"
            else:
                inspected = inspect(response.content, group)
                if inspected is None:
                    status = "zip_sin_valores_validos"
                else:
                    series, member = inspected
                    if not series:
                        status = "zip_sin_valores_validos"
                    else:
                        status = "con_datos"
                        outdir = RAW / "ema_primeros_zip_publicos" / code
                        outdir.mkdir(parents=True, exist_ok=True)
                        target = outdir / filename
                        with tempfile.NamedTemporaryFile(dir=outdir, prefix=".ema_", suffix=".tmp", delete=False) as h:
                            temporary = Path(h.name)
                            h.write(response.content)
                        os.replace(temporary, target)
                        for row in series:
                            row.update({
                                "codigo_nacional": code, "nombre_estacion": station["nombre"],
                                "grupo_ema": group, "url_evidencia": url,
                                "archivo_evidencia": str(target.relative_to(ROOT)), "miembro_csv": member,
                            })
                        attempts.append({
                            "codigo_nacional": code, "grupo_ema": group, "mes": month,
                            "estado": status, "http_status": response.status_code,
                            "bytes": size, "url": url,
                        })
                        return series, attempts
        except requests.RequestException as exc:
            status = f"error_red_{type(exc).__name__}"
        attempts.append({
            "codigo_nacional": code, "grupo_ema": group, "mes": month,
            "estado": status, "http_status": "", "bytes": size, "url": url,
        })
    return [], attempts


def write(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default="2026-01")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pause", type=float, default=0.12)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--station", action="append")
    args = parser.parse_args()
    end = datetime.strptime(args.end, "%Y-%m")
    with (RAW / "inventario_dmc.csv").open(encoding="utf-8", newline="") as handle:
        stations = list(csv.DictReader(handle))
    with (ROOT / "catalogo_dmc.csv").open(encoding="utf-8", newline="") as handle:
        completed = {row["id"] for row in csv.DictReader(handle)}
    stations = [row for row in stations if row["codigo_nacional"] not in completed]
    if args.station:
        stations = [row for row in stations if row["codigo_nacional"] in set(args.station)]
    tasks = [(station, group) for station in stations for group in DEFINITIONS]
    series_rows: list[dict] = []
    attempt_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(probe, task, end, args.pause, args.timeout) for task in tasks]
        for future in as_completed(futures):
            series, attempts = future.result()
            series_rows.extend(series)
            attempt_rows.extend(attempts)
            print(f"{len(series_rows)} series; {len(attempt_rows)} solicitudes", flush=True)
    series_fields = (
        "codigo_nacional", "nombre_estacion", "variable", "altura_sensor_suelo_m",
        "frecuencia_muestreo", "primera_fecha_verificada", "primer_valor_verificado",
        "campo_original", "grupo_ema", "url_evidencia", "archivo_evidencia", "miembro_csv",
    )
    attempt_fields = ("codigo_nacional", "grupo_ema", "mes", "estado", "http_status", "bytes", "url")
    write(RAW / "sondeo_ema_minuto_publico_pendientes.csv", series_fields,
          sorted(series_rows, key=lambda row: (row["codigo_nacional"], row["variable"], row["altura_sensor_suelo_m"])))
    write(RAW / "sondeo_ema_minuto_publico_intentos.csv", attempt_fields,
          sorted(attempt_rows, key=lambda row: (row["codigo_nacional"], row["grupo_ema"], row["mes"])))
    print(f"estaciones_pendientes={len(stations)} tareas={len(tasks)} series={len(series_rows)} solicitudes={len(attempt_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
