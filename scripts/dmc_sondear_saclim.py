#!/usr/bin/env python3
"""Busca el primer valor horario real en los ZIP anuales SACLIM de DMC."""

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path("/home/alagos/catalogo_estaciones")
INVENTORY = ROOT / "datos_originales" / "dmc" / "inventario_dmc.csv"
OUTPUT = ROOT / "datos_originales" / "dmc"
BASE = "https://climatologia.meteochile.gob.cl/application/datos/getDatosSaclim"
_LOCAL = threading.local()

SOURCES = {
    "Temperatura": ("Temperatura", "Ts", -80.0, 80.0),
    "Humedad": ("Humedad", "HR", 0.0, 100.0),
    "Presión": ("PresionQFE", "QFE", 1.0, 1200.0),
    "Intensidad del viento": ("Viento", "ff", 0.0, 300.0),
    "Dirección del viento": ("Viento", "dd", 0.0, 360.0),
}


def session() -> requests.Session:
    if not hasattr(_LOCAL, "session"):
        _LOCAL.session = requests.Session()
        _LOCAL.session.headers["User-Agent"] = "catalogo-estaciones-DMC-SACLIM/1.0"
    return _LOCAL.session


def parse_date(text: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            pass
    return None


def inspect_zip(content: bytes, field: str, low: float, high: float) -> tuple[str, str, str, int] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir() and item.filename.endswith(".csv")]
            if len(members) != 1 or archive.testzip() is not None:
                return None
            raw = archive.read(members[0])
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return None
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not reader.fieldnames or field not in reader.fieldnames or "Instante" not in reader.fieldnames:
        return None
    valid: list[tuple[datetime, float]] = []
    for row in reader:
        stamp = parse_date(row.get("Instante", ""))
        try:
            value = float((row.get(field) or "").replace(",", "."))
        except ValueError:
            continue
        if stamp is not None and low <= value <= high:
            valid.append((stamp, value))
    if not valid:
        return None
    valid.sort()
    deltas = [int((b[0] - a[0]).total_seconds() // 60) for a, b in zip(valid, valid[1:]) if b[0] > a[0]]
    positive = [delta for delta in deltas if delta > 0]
    nominal = min(positive) if positive else 0
    return valid[0][0].isoformat(timespec="minutes"), str(valid[0][1]), members[0].filename, nominal


def probe(task: tuple[dict[str, str], str], end_year: int, pause: float, timeout: float) -> dict[str, object]:
    station, variable = task
    source, field, low, high = SOURCES[variable]
    code = station["codigo_nacional"]
    try:
        start_year = datetime.strptime(station["fecha_creacion"], "%d-%m-%Y").year
    except ValueError:
        start_year = 1900
    attempts = 0
    errors = 0
    first_found: tuple[str, str, str, str, int] | None = None
    for year in range(start_year, end_year + 1):
        attempts += 1
        stem = f"{code}_{year}_{source}_"
        url = f"{BASE}/{stem}"
        for retry in range(3):
            try:
                time.sleep(pause + random.uniform(0, pause))
                response = session().get(url, timeout=timeout)
                if response.status_code in {429, 500, 502, 503, 504} and retry < 2:
                    time.sleep(2 ** retry)
                    continue
                if response.status_code != 200 or not response.content.startswith(b"PK\x03\x04"):
                    break
                result = inspect_zip(response.content, field, low, high)
                if result is None:
                    break
                stamp, value, member, nominal = result
                raw_dir = OUTPUT / "saclim_primeros_zip" / code
                raw_dir.mkdir(parents=True, exist_ok=True)
                target = raw_dir / f"{stem}.csv.zip"
                if not target.exists():
                    with tempfile.NamedTemporaryFile(dir=raw_dir, prefix=".saclim_", suffix=".tmp", delete=False) as h:
                        temporary = Path(h.name)
                        h.write(response.content)
                    os.replace(temporary, target)
                if first_found is None:
                    first_found = (stamp, value, member, str(target.relative_to(ROOT)), year)
                if not nominal:
                    # Un único valor al límite del año prueba la primera fecha,
                    # pero no el intervalo. Continuar hasta observar un delta.
                    break
                first_stamp, first_value, first_member, first_target, first_year = first_found
                return {
                    "codigo_nacional": code, "nombre_estacion": station["nombre"],
                    "variable": variable, "variable_saclim": source, "campo_saclim": field,
                    "primera_fecha_verificada": first_stamp, "primer_valor_verificado": first_value,
                    "frecuencia_observada_min": nominal or "No determinado",
                    "anio_inicial_sondeo": start_year, "anio_hallazgo": first_year,
                    "solicitudes": attempts, "errores_red": errors, "estado": "con_datos",
                    "url_evidencia": url,
                    "archivo_evidencia": first_target,
                    "miembro_csv": first_member,
                }
            except requests.RequestException:
                errors += 1
                if retry == 2:
                    break
                time.sleep(2 ** retry)
    if first_found is not None:
        first_stamp, first_value, first_member, first_target, first_year = first_found
        return {
            "codigo_nacional": code, "nombre_estacion": station["nombre"],
            "variable": variable, "variable_saclim": source, "campo_saclim": field,
            "primera_fecha_verificada": first_stamp, "primer_valor_verificado": first_value,
            "frecuencia_observada_min": "No determinado", "anio_inicial_sondeo": start_year,
            "anio_hallazgo": first_year, "solicitudes": attempts, "errores_red": errors,
            "estado": "con_datos", "url_evidencia": "", "archivo_evidencia": first_target,
            "miembro_csv": first_member,
        }
    return {
        "codigo_nacional": code, "nombre_estacion": station["nombre"],
        "variable": variable, "variable_saclim": source, "campo_saclim": field,
        "primera_fecha_verificada": "", "primer_valor_verificado": "",
        "frecuencia_observada_min": "", "anio_inicial_sondeo": start_year,
        "anio_hallazgo": "", "solicitudes": attempts, "errores_red": errors,
        "estado": "sin_datos_verificados", "url_evidencia": "",
        "archivo_evidencia": "", "miembro_csv": "",
    }


def write_results(path: Path, rows: list[dict[str, object]]) -> None:
    fields = (
        "codigo_nacional", "nombre_estacion", "variable", "variable_saclim", "campo_saclim",
        "primera_fecha_verificada", "primer_valor_verificado", "frecuencia_observada_min",
        "anio_inicial_sondeo", "anio_hallazgo", "solicitudes", "errores_red", "estado",
        "url_evidencia", "archivo_evidencia", "miembro_csv",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (row["codigo_nacional"], row["variable"])))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--output", type=Path, default=OUTPUT / "sondeo_saclim_horario.csv")
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--pause", type=float, default=0.08)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--station", action="append")
    args = parser.parse_args()
    with args.inventory.open(encoding="utf-8", newline="") as handle:
        stations = list(csv.DictReader(handle))
    if args.station:
        stations = [row for row in stations if row["codigo_nacional"] in set(args.station)]
    tasks = [(station, variable) for station in stations for variable in SOURCES]
    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(probe, task, args.end_year, args.pause, args.timeout) for task in tasks]
        for future in as_completed(futures):
            rows.append(future.result())
            if len(rows) % 10 == 0 or len(rows) == len(tasks):
                write_results(args.output, rows)
                print(f"{len(rows)}/{len(tasks)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
