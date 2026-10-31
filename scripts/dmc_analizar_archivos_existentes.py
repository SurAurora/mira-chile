#!/usr/bin/env python3
"""Extrae primeras fechas verificadas de descargas DMC ya disponibles localmente."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import statistics
import tempfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path


OLD_ROOT = Path("/home/alagos/fondef_polvo/data/observations/surface/meteo_chem/dmc_ema/raw")
OUTPUT = Path("/home/alagos/catalogo_estaciones/datos_originales/dmc")

SERIES_15 = {
    "temperatura": ("Temperatura", "1.5", -80, 80),
    "temperatura02Mts": ("Temperatura", "2", -80, 80),
    "temperatura10Mts": ("Temperatura", "10", -80, 80),
    "temperatura30Mts": ("Temperatura", "30", -80, 80),
    "humedadRelativa": ("Humedad", "No informado", 0, 100),
    "radiacionGlobalInst": ("Radiación solar", "No informado", -50, 2000),
    "presionEstacion": ("Presión", "No informado", 1, 1200),
    "aguaCaidaDelMinuto": ("Precipitación", "No informado", 0, 1000),
    "direccionDelViento": ("Dirección del viento", "No informado", 0, 360),
    "fuerzaDelViento": ("Intensidad del viento", "No informado", 0, 300),
    "fuerzaDelViento02MinutosMax": ("Ráfaga del viento", "No informado", 0, 300),
    "fuerzaDelViento10MinutosMax": ("Ráfaga del viento", "No informado", 0, 300),
}

SERIES_MINUTE = {
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


def numeric(value: object) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)", str(value).strip())
    if not match:
        return None
    try:
        return float(match.group().replace(",", "."))
    except ValueError:
        return None


def timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def update(first: dict, key: tuple, stamp: datetime, value: float, source: str, field: str) -> None:
    candidate = (stamp, value, source, field)
    if key not in first or candidate[0] < first[key][0]:
        first[key] = candidate


def analyze_15(root: Path) -> list[dict[str, object]]:
    first: dict[tuple, tuple] = {}
    files = sorted((root / "api_15min").glob("*/*.json.gz"))
    for path in files:
        code = path.parent.name
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        rows = payload.get("datosEstaciones", {}).get("datos", [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp = timestamp(row.get("momento"))
            if stamp is None:
                continue
            for field, (variable, height, low, high) in SERIES_15.items():
                value = numeric(row.get(field))
                if value is not None and low <= value <= high:
                    # Las dos definiciones máximas respaldan una sola serie normalizada.
                    key = (code, variable, height)
                    update(first, key, stamp, value, str(path), field)
    out = []
    for (code, variable, height), (stamp, value, source, field) in sorted(first.items()):
        out.append({
            "codigo_nacional": code, "variable": variable,
            "altura_sensor_suelo_m": height, "frecuencia_muestreo": "15 min",
            "primera_fecha_verificada": stamp.isoformat(sep="T", timespec="seconds"),
            "primer_valor_verificado": value, "campo_original": field,
            "archivo_evidencia": source,
        })
    return out


def minute_frequency(stamps: list[datetime]) -> str:
    deltas = [int((b - a).total_seconds() // 60) for a, b in zip(stamps, stamps[1:]) if b > a]
    deltas = [delta for delta in deltas if 0 < delta <= 60]
    if not deltas:
        return "No determinado"
    return f"{Counter(deltas).most_common(1)[0][0]} min"


def analyze_minute(root: Path) -> list[dict[str, object]]:
    first: dict[tuple, tuple] = {}
    frequencies: dict[tuple[str, str], str] = {}
    for station_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit()):
        code = station_dir.name
        for group, definitions in SERIES_MINUTE.items():
            files = sorted(station_dir.glob(f"{code}_*_{group}.csv.zip"))
            missing = set(definitions)
            for path in files:
                if not missing:
                    break
                try:
                    with zipfile.ZipFile(path) as archive:
                        members = [name for name in archive.namelist() if name.endswith(".csv")]
                        if len(members) != 1:
                            continue
                        text = archive.read(members[0]).decode("utf-8-sig", errors="replace")
                except (OSError, zipfile.BadZipFile, RuntimeError):
                    continue
                reader = csv.DictReader(io.StringIO(text), delimiter=";")
                rows = list(reader)
                stamps = [stamp for row in rows if (stamp := timestamp(row.get("momento"))) is not None]
                frequencies.setdefault((code, group), minute_frequency(stamps))
                for row in rows:
                    stamp = timestamp(row.get("momento"))
                    if stamp is None:
                        continue
                    for field in list(missing):
                        variable, height, low, high = definitions[field]
                        value = numeric(row.get(field))
                        if value is not None and low <= value <= high:
                            key = (code, variable, height)
                            update(first, key, stamp, value, str(path), field)
                            # Cualquiera de los campos máximos cubre la ráfaga normalizada.
                            if variable == "Ráfaga del viento":
                                missing.difference_update({
                                    name for name, spec in definitions.items() if spec[0] == variable
                                })
                            else:
                                missing.discard(field)
    out = []
    for (code, variable, height), (stamp, value, source, field) in sorted(first.items()):
        group = next(group for group, defs in SERIES_MINUTE.items() if field in defs)
        out.append({
            "codigo_nacional": code, "variable": variable,
            "altura_sensor_suelo_m": height,
            "frecuencia_muestreo": frequencies.get((code, group), "No determinado"),
            "primera_fecha_verificada": stamp.isoformat(sep="T", timespec="seconds"),
            "primer_valor_verificado": value, "campo_original": field,
            "archivo_evidencia": source,
        })
    return out


def write(path: Path, rows: list[dict[str, object]]) -> None:
    fields = (
        "codigo_nacional", "variable", "altura_sensor_suelo_m", "frecuencia_muestreo",
        "primera_fecha_verificada", "primer_valor_verificado", "campo_original", "archivo_evidencia",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temp = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=OLD_ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    rows15 = analyze_15(args.root)
    rows1 = analyze_minute(args.root)
    write(args.output / "sondeo_ema_15min_existente.csv", rows15)
    write(args.output / "sondeo_ema_minuto_existente.csv", rows1)
    print(f"series_15min={len(rows15)} series_minuto={len(rows1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
