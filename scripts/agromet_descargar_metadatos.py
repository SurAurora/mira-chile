#!/usr/bin/env python3
"""Descarga y normaliza metadatos del JSON público del mapa AGROMET."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from agromet_table_downloader import BASE_URL, REGIONS, catalog, request


FIELDS = (
    "id",
    "nombre",
    "latitud",
    "longitud",
    "elevacion_m",
    "comuna",
    "region",
    "institucion_sigla",
    "institucion_nombre",
    "estado_portal",
    "estado_portal_nombre",
    "fecha_consulta_utc",
    "sha256_json_fuente",
)


def normalized_id(item: dict[str, object]) -> str:
    prefix = "INIA" if item.get("source") == "inia" else "EXT"
    return f"{prefix}-{item['id']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-gz", type=Path, required=True)
    args = parser.parse_args()

    page = request(BASE_URL).decode("utf-8", errors="replace")
    match = re.search(r"data-ts-map-tmp\s*=\s*[\"']?([A-Za-z0-9_-]+)", page)
    if not match:
        raise SystemExit("No se encontró data-ts-map-tmp en la portada de AGROMET")
    temporary_dir = match.group(1)
    json_url = f"{BASE_URL}json/{temporary_dir}/items-resumen.json"
    content = request(json_url)
    source_hash = hashlib.sha256(content).hexdigest()
    data = json.loads(content)
    if not isinstance(data, list):
        raise SystemExit("El JSON del mapa no contiene una lista")

    station_catalog, _ = catalog()
    expected = {station.id for station in station_catalog if station.region in REGIONS}
    by_id = {
        normalized_id(item): item
        for item in data
        if isinstance(item, dict) and "id" in item and item.get("region") in REGIONS
    }
    missing = expected - set(by_id)
    extra = set(by_id) - expected
    if missing:
        raise SystemExit(f"Faltan estaciones en el JSON del mapa: {', '.join(sorted(missing))}")
    if extra:
        raise SystemExit(f"Hay estaciones inesperadas en el JSON del mapa: {', '.join(sorted(extra))}")

    consulted = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_gz.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".part")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for station_id in sorted(expected):
            item = by_id[station_id]
            writer.writerow(
                {
                    "id": station_id,
                    "nombre": item.get("nombre", "No informado"),
                    "latitud": item.get("latitud", "No informado"),
                    "longitud": item.get("longitud", "No informado"),
                    "elevacion_m": item.get("elevacion", "No informado"),
                    "comuna": item.get("comuna", "No informado"),
                    "region": item.get("region", "No informado"),
                    "institucion_sigla": item.get("institucion_sigla", "No informado"),
                    "institucion_nombre": item.get("institucion_nombre", "No informado"),
                    "estado_portal": item.get("status", "No informado"),
                    "estado_portal_nombre": item.get("status_name", "No informado"),
                    "fecha_consulta_utc": consulted,
                    "sha256_json_fuente": source_hash,
                }
            )
    temp.replace(args.output)
    with gzip.open(args.raw_gz, "wb", compresslevel=6) as handle:
        handle.write(content)
    print(
        f"Metadatos: {len(expected)} estaciones | tmp={temporary_dir} | "
        f"sha256={source_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
