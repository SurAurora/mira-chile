#!/usr/bin/env python3
"""Integra los catálogos por fuente en catalogo_estaciones.csv."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "catalogo_estaciones.csv"
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
KEY = [
    "nombre_estacion",
    "id",
    "fuente_institucion",
    "variable",
    "altura_sensor_suelo_m",
    "frecuencia_muestreo",
]


def main() -> int:
    inputs = sorted(
        path
        for path in PROJECT.glob("catalogo_*.csv")
        if path.name != OUTPUT.name
    )
    rows: list[dict] = []
    for path in inputs:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != COLUMNS:
                raise ValueError(f"Columnas incompatibles en {path.name}: {reader.fieldnames}")
            rows.extend(reader)
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(row[field] for field in KEY)
        if key in seen:
            raise ValueError(f"Combinación duplicada: {key}")
        seen.add(key)
        readme = PROJECT / row["readme_descarga"]
        if not readme.is_file():
            raise FileNotFoundError(f"README inexistente: {readme}")
    rows.sort(
        key=lambda row: (
            row["fuente_institucion"],
            row["id"],
            row["variable"],
            row["altura_sensor_suelo_m"],
            row["frecuencia_muestreo"],
        )
    )
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"fuentes={len(inputs)} filas={len(rows)} salida={OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
