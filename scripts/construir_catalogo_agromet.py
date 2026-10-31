#!/usr/bin/env python3
"""Construye el catálogo parcial AGROMET con las 13 columnas del proyecto."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


VARIABLES = {
    "TA_AVG": "Temperatura",
    "HR_AVG": "Humedad",
    "PP_SUM": "Precipitación",
    "PS_AVG": "Presión",
    "RD_AVG": "Radiación solar",
    "VV_AVG": "Intensidad del viento",
    "DV_AVG": "Dirección del viento",
    "VV_MAX": "Ráfaga del viento",
}

FIELDS = (
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
)


def read_index(path: Path, key: str) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    indexed = {row[key]: row for row in rows}
    if len(indexed) != len(rows):
        raise SystemExit(f"Claves duplicadas en {path}: {key}")
    return indexed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--availability", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    metadata = read_index(args.metadata, "id")
    with args.availability.open(encoding="utf-8-sig", newline="") as handle:
        availability = list(csv.DictReader(handle))

    rows: list[dict[str, str]] = []
    absent = 0
    for item in availability:
        if item["estado_consulta"] != "completada":
            raise SystemExit(
                f"Consulta no completada: {item['estacion_id']} {item['variable_codigo']}"
            )
        if item["variable_codigo"] not in VARIABLES:
            raise SystemExit(f"Variable no normalizada: {item['variable_codigo']}")
        if item["primera_fecha_disponible"] == "No informado":
            absent += 1
            continue
        station = metadata.get(item["estacion_id"])
        if station is None:
            raise SystemExit(f"Sin metadatos: {item['estacion_id']}")
        rows.append(
            {
                "nombre_estacion": station["nombre"],
                "id": station["id"],
                "fuente_institucion": station["institucion_sigla"],
                "latitud": station["latitud"],
                "longitud": station["longitud"],
                "altitud_snm_m": station["elevacion_m"],
                "variable": VARIABLES[item["variable_codigo"]],
                "tipo_variable": "Meteorológica",
                "altura_sensor_suelo_m": "No informado",
                "frecuencia_muestreo": "60 min",
                "primera_fecha_disponible": item["primera_fecha_disponible"],
                "datos_disponibles_actualmente": item["datos_disponibles_actualmente"],
                "readme_descarga": "fuentes/agromet/README.md",
            }
        )

    rows.sort(key=lambda row: (row["id"], row["variable"]))
    unique = {
        (
            row["id"],
            row["fuente_institucion"],
            row["variable"],
            row["altura_sensor_suelo_m"],
            row["frecuencia_muestreo"],
        )
        for row in rows
    }
    if len(unique) != len(rows):
        raise SystemExit("El catálogo parcial contiene filas duplicadas")
    if set(metadata) != {item["estacion_id"] for item in availability}:
        raise SystemExit("La cobertura de estaciones no coincide entre metadatos y disponibilidad")
    invalid_current = {
        row["datos_disponibles_actualmente"]
        for row in rows
        if row["datos_disponibles_actualmente"] not in {"Sí", "No", "No determinado"}
    }
    if invalid_current:
        raise SystemExit(f"Estados actuales inválidos: {', '.join(sorted(invalid_current))}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".part")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(args.output)
    print(f"Catálogo AGROMET: {len(rows)} filas | combinaciones sin datos excluidas: {absent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
