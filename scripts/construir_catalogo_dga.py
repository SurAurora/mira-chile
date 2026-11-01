#!/usr/bin/env python3
"""Construye catalogo_dga.csv desde el sondeo verificable de VIPNET."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datos_originales" / "dga"
OUTPUT = ROOT / "catalogo_dga.csv"
COLUMNS = [
    "nombre_estacion", "id", "fuente_institucion", "latitud", "longitud",
    "altitud_snm_m", "variable", "tipo_variable", "altura_sensor_suelo_m",
    "frecuencia_muestreo", "primera_fecha_disponible",
    "datos_disponibles_actualmente", "readme_descarga",
]


def fmt(value: object) -> str:
    return "" if value is None else str(value)


def main() -> int:
    stations = json.loads((RAW / "vipnet_estaciones_variables.json").read_text(encoding="utf-8"))
    evidence = json.loads((RAW / "vipnet_resumen_series.json").read_text(encoding="utf-8"))
    by_key = {(row["codigoEstacion"], int(row["tipoEstacion"])): row for row in evidence}
    rows = []
    for station in stations:
        key = (station["codigoEstacion"], int(station["tipoEstacion"]))
        probe = by_key.get(key)
        if not probe or probe.get("error") or not probe.get("primera_fecha"):
            continue
        rows.append({
            "nombre_estacion": station["nombre"].strip(),
            "id": station["codigoEstacion"],
            "fuente_institucion": "DGA",
            "latitud": fmt(station.get("latitud")),
            "longitud": fmt(station.get("longitud")),
            "altitud_snm_m": fmt(station.get("altitud")),
            "variable": probe["variable"],
            "tipo_variable": "Meteorológica",
            "altura_sensor_suelo_m": "No informado",
            "frecuencia_muestreo": probe["frecuencia_observada"],
            "primera_fecha_disponible": probe["primera_fecha"],
            "datos_disponibles_actualmente": "Sí" if probe["datos_ultimos_7_dias_completos"] else "No",
            "readme_descarga": "fuentes/dga/README.md",
        })
    rows.sort(key=lambda row: (row["id"], row["variable"]))
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader(); writer.writerows(rows)
    print(f"filas={len(rows)} estaciones={len({row['id'] for row in rows})} salida={OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
