#!/usr/bin/env python3
"""Comprueba actividad en un mes usando ZIP EMA públicos, sin guardarlos."""

from __future__ import annotations

import argparse
import csv
import random
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from dmc_sondear_ema_minuto_publico import BASE, DEFINITIONS, RAW, inspect


EXTRA_PORTAL_CODES = {"230021", "240005"}


def probe(station: dict[str, str], group: str, month: str, timeout: float, pause: float) -> list[dict]:
    code = station["codigo_nacional"]
    filename = f"{code}_{month}_{group}.csv.zip"
    url = f"{BASE}/{code}/{filename}"
    status = "No determinado"
    detail = "El recurso mensual no entregó un ZIP verificable"
    found: set[tuple[str, str]] = set()
    try:
        time.sleep(pause + random.uniform(0, pause))
        response = requests.get(
            url, headers={"User-Agent": "MIRA-Chile-DMC-octubre/1.0"}, timeout=timeout,
        )
        if response.status_code == 200 and response.content.startswith(b"PK\x03\x04"):
            result = inspect(response.content, group)
            if result is not None:
                series, _ = result
                found = {(row["variable"], row["altura_sensor_suelo_m"]) for row in series}
                status = "No"
                detail = "ZIP mensual válido, sin valores físicamente válidos para esta serie"
    except requests.RequestException as exc:
        detail = f"Error de red: {type(exc).__name__}"

    out = []
    series_keys = sorted({(spec[0], spec[1]) for spec in DEFINITIONS[group].values()})
    for variable, height in series_keys:
        state = "Sí" if (variable, height) in found else status
        out.append({
            "codigo_nacional": code,
            "nombre_estacion": station["nombre"],
            "variable": variable,
            "altura_sensor_suelo_m": height,
            "mes_evidencia": month,
            "datos_disponibles_actualmente": state,
            "grupo_ema": group,
            "url_evidencia": url,
            "detalle": "Al menos un valor válido en el ZIP mensual" if state == "Sí" else detail,
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=RAW / "inventario_saclim_regiones.csv")
    parser.add_argument("--month", default="202610")
    parser.add_argument("--output", type=Path, default=RAW / "disponibilidad_octubre_ema_publico.csv")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--pause", type=float, default=0.12)
    args = parser.parse_args()
    if len(args.month) != 6 or not args.month.isdigit():
        raise ValueError("--month debe tener formato AAAAMM")
    with args.inventory.open(encoding="utf-8", newline="") as handle:
        stations = [
            row for row in csv.DictReader(handle)
            if row["propietario"] == "DMC" or row["codigo_nacional"] in EXTRA_PORTAL_CODES
        ]
    tasks = [(station, group) for station in stations for group in DEFINITIONS]
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(probe, station, group, args.month, args.timeout, args.pause)
            for station, group in tasks
        ]
        for number, future in enumerate(as_completed(futures), 1):
            rows.extend(future.result())
            if number % 25 == 0 or number == len(futures):
                print(f"{number}/{len(futures)}", flush=True)
    fields = (
        "codigo_nacional", "nombre_estacion", "variable", "altura_sensor_suelo_m",
        "mes_evidencia", "datos_disponibles_actualmente", "grupo_ema",
        "url_evidencia", "detalle",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=args.output.parent,
        prefix=f".{args.output.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (
            row["codigo_nacional"], row["variable"], row["altura_sensor_suelo_m"],
        )))
    temporary.replace(args.output)
    print(
        f"estaciones={len(stations)} series_si="
        f"{sum(row['datos_disponibles_actualmente'] == 'Sí' for row in rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
