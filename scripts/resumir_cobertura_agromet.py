#!/usr/bin/env python3
"""Resume cobertura por estación y variable a partir de CSV derivados de AGROMET."""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


MISSING = {"", "s/i", "si", "nan", "na", "null", "none", "no informado"}
FILE_RE = re.compile(
    r"^(?P<station>.+?)_(?P<interval>hour|day)_(?P<start>\d{4}-\d{2}-\d{2})_"
    r"(?P<end>\d{4}-\d{2}-\d{2})_(?P<variables>.+)\.csv$"
)


@dataclass
class Coverage:
    first: datetime | None = None
    last: datetime | None = None
    valid_count: int = 0
    files: set[str] | None = None

    def __post_init__(self) -> None:
        if self.files is None:
            self.files = set()

    def add(self, stamp: datetime, file_name: str) -> None:
        self.first = stamp if self.first is None else min(self.first, stamp)
        self.last = stamp if self.last is None else max(self.last, stamp)
        self.valid_count += 1
        self.files.add(file_name)


def is_valid(value: str) -> bool:
    return value.strip().casefold() not in MISSING


def load_stations(path: Path) -> dict[str, tuple[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["id"]: (row["region"], row["nombre_publicado"])
            for row in csv.DictReader(handle)
        }


def summarize(input_dir: Path) -> dict[tuple[str, str, str], Coverage]:
    coverage: dict[tuple[str, str, str], Coverage] = defaultdict(Coverage)
    for path in sorted(input_dir.glob("*.csv")):
        match = FILE_RE.match(path.name)
        if not match:
            continue
        file_station = match.group("station")
        interval = match.group("interval")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "fecha_hora" not in reader.fieldnames:
                raise RuntimeError(f"CSV sin fecha_hora: {path}")
            series = [
                field
                for field in reader.fieldnames
                if field != "fecha_hora" and not field.startswith("porcentaje_datos_")
            ]
            for row in reader:
                stamp = datetime.fromisoformat(row["fecha_hora"])
                for item in series:
                    if "__" in item:
                        station, variable = item.split("__", 1)
                    else:
                        station, variable = file_station, item
                    if is_valid(row.get(item, "")):
                        coverage[(station, variable, interval)].add(stamp, path.name)
    return coverage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Directorio que contiene los CSV")
    parser.add_argument("--stations", type=Path, required=True, help="Snapshot estaciones_agromet.csv")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    stations = load_stations(args.stations)
    coverage = summarize(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        fields = (
            "estacion_id",
            "nombre_publicado",
            "region",
            "variable_codigo",
            "intervalo",
            "primera_fecha_con_valor",
            "ultima_fecha_con_valor",
            "cantidad_registros_con_valor",
            "archivos_fuente",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for (station_id, variable, interval), item in sorted(coverage.items()):
            region, name = stations.get(station_id, ("No informado", "No informado"))
            writer.writerow(
                {
                    "estacion_id": station_id,
                    "nombre_publicado": name,
                    "region": region,
                    "variable_codigo": variable,
                    "intervalo": interval,
                    "primera_fecha_con_valor": item.first.isoformat(timespec="minutes") if item.first else "No informado",
                    "ultima_fecha_con_valor": item.last.isoformat(timespec="minutes") if item.last else "No informado",
                    "cantidad_registros_con_valor": item.valid_count,
                    "archivos_fuente": "|".join(sorted(item.files or ())),
                }
            )
    print(f"Resumen: {len(coverage)} combinaciones -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
