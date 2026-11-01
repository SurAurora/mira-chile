#!/usr/bin/env python3
"""Descarga un producto horario VISMET en ventanas de hasta 168 horas."""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path

from vismet_sondear_disponibilidad import Client, RAW, VARIABLES, dump, iso, station_path


def timestamp(value: str) -> int:
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return int(date.timestamp())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", type=int, required=True, help="ID interno de VISMET, no el código nacional")
    parser.add_argument("--variable", type=int, choices=VARIABLES, required=True)
    parser.add_argument("--from", dest="date_from", required=True, help="Inicio inclusivo ISO 8601; sin zona se interpreta UTC")
    parser.add_argument("--to", dest="date_to", required=True, help="Final exclusivo ISO 8601; sin zona se interpreta UTC")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause", type=float, default=0.3)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("El archivo de salida ya existe; elija otro nombre para conservarlo")
    start, stop = timestamp(args.date_from), timestamp(args.date_to)
    if start >= stop:
        parser.error("--from debe ser anterior a --to")
    client = Client(RAW, args.pause)
    cursor = math.ceil(start / 3600) * 3600
    last_hour = ((stop - 1) // 3600) * 3600
    values = {}
    paths = []
    while cursor <= last_hour:
        end = min(cursor + 167 * 3600, last_hour)
        hours = (end - cursor) // 3600 + 1
        path = station_path(args.station, args.variable, end, hours)
        rows = client.get(path)
        for row in rows:
            if row["station"]["id"] != args.station or row["measureType"]["id"] != args.variable:
                raise ValueError("La respuesta no corresponde a la estación y variable solicitadas")
            if start <= row["timestamp"] < stop:
                values[row["timestamp"]] = row
        paths.append(path)
        cursor = end + 3600
    dump(args.output, {
        "id_vismet": args.station, "variable": VARIABLES[args.variable],
        "desde_inclusivo_utc": iso(start), "hasta_exclusivo_utc": iso(stop),
        "rutas_consultadas": paths,
        "nota": "Se conservan los valores y las banderas originales, incluidos registros sin respaldo. Revisar confidence y lastUsedRawTimestamp.",
        "data": [values[key] for key in sorted(values)],
    })
    print(f"VISMET: registros={len(values)} ventanas={len(paths)} salida={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
