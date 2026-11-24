#!/usr/bin/env python3
"""Amplía el inventario DMC con el catastro SACLIM de la zona central."""

from __future__ import annotations

import argparse
import csv
import tempfile
from pathlib import Path

from dmc_descargar_metadatos import TableParser


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datos_originales" / "dmc"
DEFAULT_CATALOG = RAW / "CatastrodeEstacionesdelSistemaSACLIM_2026-11-24_03_47.xls"
TARGET_REGIONS = {
    "Valparaiso", "Metropolitana de Santiago",
    "Del Libertador Gral. Bernardo O'Higgins",
}
CENTRAL_REGION_MARKERS = ("valpar", "metropolitana", "libertador")
FIELDS = (
    "numero", "codigo_nacional", "codigo_oaci", "nombre", "latitud", "longitud",
    "altitud_m", "altura_minima_m", "propietario", "fecha_creacion", "tipo",
    "clasificacion", "zona_geografica", "region", "emite_metar", "emite_synop",
    "paisaje", "operador",
)


def parse_catalog(path: Path) -> list[dict[str, str]]:
    parser = TableParser()
    parser.feed(path.read_text(encoding="utf-8"))
    header = next((row for row in parser.rows if row and row[0] == "N°"), None)
    if header is None or len(header) != len(FIELDS):
        raise ValueError("No se reconoció la tabla SACLIM de 18 columnas")
    rows = [row for row in parser.rows if len(row) == len(FIELDS) and row[1].isdigit()]
    return [dict(zip(FIELDS, row)) for row in rows if row[13] in TARGET_REGIONS]


def central_region(value: str) -> bool:
    text = value.lower()
    return any(marker in text for marker in CENTRAL_REGION_MARKERS)


def write(path: Path, rows: list[dict[str, str]]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    all_central = parse_catalog(args.catalog)
    completed = [row for row in all_central if row["propietario"] == "DMC"]

    with (RAW / "inventario_saclim_regiones.csv").open(encoding="utf-8", newline="") as handle:
        original_all = list(csv.DictReader(handle))
    with (RAW / "inventario_dmc.csv").open(encoding="utf-8", newline="") as handle:
        original_dmc = list(csv.DictReader(handle))
    all_rows = [row for row in original_all if not central_region(row["region"])] + all_central
    dmc_rows = [row for row in original_dmc if not central_region(row["region"])] + completed
    all_rows.sort(key=lambda row: row["codigo_nacional"])
    dmc_rows.sort(key=lambda row: row["codigo_nacional"])
    write(RAW / "inventario_saclim_regiones.csv", all_rows)
    write(RAW / "inventario_dmc.csv", dmc_rows)
    write(RAW / "inventario_saclim_centro.csv", sorted(all_central, key=lambda row: row["codigo_nacional"]))
    write(RAW / "inventario_dmc_centro.csv", sorted(completed, key=lambda row: row["codigo_nacional"]))
    missing = [row["codigo_nacional"] for row in completed if not row["fecha_creacion"]]
    print(
        f"catastro_centro={len(all_central)} centro_dmc={len(completed)} "
        f"inventario_dmc={len(dmc_rows)} sin_fecha={len(missing)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
