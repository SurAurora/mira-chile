#!/usr/bin/env python3
"""Valida valores DMC autenticados dentro de siete días completos."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path("/home/alagos/catalogo_estaciones")
RAW = ROOT / "datos_originales" / "dmc"
API_ROOT = Path("/home/alagos/fondef_polvo/data/observations/surface/meteo_chem/dmc_ema/raw/api_15min")
MANIFEST = API_ROOT / "manifest_api_15min.csv"

DEFINITIONS = {
    ("Temperatura", "1.5"): (("temperatura",), -80, 80),
    ("Temperatura", "2"): (("temperatura02Mts",), -80, 80),
    ("Temperatura", "10"): (("temperatura10Mts",), -80, 80),
    ("Temperatura", "30"): (("temperatura30Mts",), -80, 80),
    ("Humedad", "No informado"): (("humedadRelativa",), 0, 100),
    ("Radiación solar", "No informado"): (("radiacionGlobalInst",), -50, 2000),
    ("Presión", "No informado"): (("presionEstacion",), 1, 1200),
    ("Precipitación", "No informado"): (("aguaCaidaDelMinuto",), 0, 1000),
    ("Dirección del viento", "No informado"): ((
        "direccionDelViento", "direccionDelVientoPromedio2Minutos",
        "direccionDelVientoPromedio10Minutos",
    ), 0, 360),
    ("Intensidad del viento", "No informado"): ((
        "fuerzaDelViento", "fuerzaDelVientoPromedio2Minutos",
        "fuerzaDelVientoPromedio10Minutos",
    ), 0, 300),
    ("Ráfaga del viento", "No informado"): ((
        "fuerzaDelViento02MinutosMax", "fuerzaDelViento10MinutosMax",
    ), 0, 300),
}


def number(value: object) -> float | None:
    match = re.search(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group().replace(",", "."))
    except ValueError:
        return None


def mode_frequency(rows: list[dict]) -> str:
    stamps = []
    for row in rows:
        try:
            stamps.append(datetime.fromisoformat(row.get("momento", "")))
        except ValueError:
            pass
    stamps = sorted(set(stamps))
    deltas = [int((b - a).total_seconds() // 60) for a, b in zip(stamps, stamps[1:]) if b > a]
    deltas = [delta for delta in deltas if 0 < delta <= 180]
    return f"{Counter(deltas).most_common(1)[0][0]} min" if deltas else "No determinado"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args()
    end = args.as_of - timedelta(days=1)
    start = end - timedelta(days=6)
    month = args.as_of.strftime("%Y%m")
    with args.manifest.open(encoding="utf-8", newline="") as handle:
        manifest = [row for row in csv.DictReader(handle) if row["month"] == month]
    if len(manifest) != 29:
        raise ValueError(f"Se esperaban 29 estaciones DMC para {month}; se encontraron {len(manifest)}")

    out = []
    for item in sorted(manifest, key=lambda row: row["station_code"]):
        code = item["station_code"]
        status = item["status"]
        window_rows: list[dict] = []
        if status in {"downloaded", "existing"}:
            path = Path(item["local_path"])
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            records = payload.get("datosEstaciones", {}).get("datos", [])
            window_rows = [
                row for row in records
                if isinstance(row, dict) and start.isoformat() <= str(row.get("momento", ""))[:10] <= end.isoformat()
            ]
        frequency = mode_frequency(window_rows)
        for (variable, height), (fields, low, high) in DEFINITIONS.items():
            first = None
            first_value = None
            first_field = ""
            valid_count = 0
            fields_present = any(
                any(field in record for field in fields) for record in window_rows
            )
            for record in window_rows:
                for field in fields:
                    value = number(record.get(field))
                    if value is not None and low <= value <= high:
                        valid_count += 1
                        if first is None:
                            first = record.get("momento")
                            first_value = value
                            first_field = field
                        break
            if status == "no_data":
                state = "No"
                detail = "La API autenticada respondió no_data para todo el mes"
            elif status not in {"downloaded", "existing"}:
                state = "No determinado"
                detail = f"Estado de descarga: {status}"
            elif valid_count:
                state = "Sí"
                detail = f"{valid_count} registros válidos en la ventana"
            elif not fields_present:
                state = "No determinado"
                detail = "La respuesta no incluye los campos de esta serie"
            else:
                state = "No"
                detail = "Respuesta válida, sin valores de esta serie en la ventana"
            out.append({
                "codigo_nacional": code, "nombre_estacion": item["station_name"],
                "variable": variable, "altura_sensor_suelo_m": height,
                "ventana_desde": start.isoformat(), "ventana_hasta": end.isoformat(),
                "datos_disponibles_actualmente": state,
                "primer_instante_ventana": first or "",
                "primer_valor_ventana": "" if first_value is None else first_value,
                "campo_original": first_field,
                "registros_validos_ventana": valid_count,
                "frecuencia_observada_respuesta": frequency,
                "estado_respuesta_mes": status,
                "detalle": detail,
            })
    fields = (
        "codigo_nacional", "nombre_estacion", "variable", "altura_sensor_suelo_m",
        "ventana_desde", "ventana_hasta", "datos_disponibles_actualmente",
        "primer_instante_ventana", "primer_valor_ventana", "campo_original",
        "registros_validos_ventana", "frecuencia_observada_respuesta",
        "estado_respuesta_mes", "detalle",
    )
    target = RAW / "disponibilidad_actual_api_15min.csv"
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out)
    counts = Counter((row["variable"], row["datos_disponibles_actualmente"]) for row in out)
    print(f"estaciones={len(manifest)} filas={len(out)} ventana={start}/{end}")
    for key, count in sorted(counts.items()):
        print(key[0], key[1], count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
