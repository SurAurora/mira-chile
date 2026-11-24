#!/usr/bin/env python3
"""Amplía el inventario DMC con Valparaíso, Metropolitana y O'Higgins.

El producto RE7001 entrega el catastro nacional. Para las estaciones de
propiedad DMC se consulta además la ficha oficial, porque allí se publica la
fecha de creación que permite acotar los sondeos históricos.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datos_originales" / "dmc"
NATIONAL = ROOT / "datos_originales" / "exp_eol" / "catastro_estaciones_nacional.html.gz"
BASE = "https://climatologia.meteochile.gob.cl/application"
TARGET_REGIONS = {
    "Región de Valparaíso",
    "Región de Metropolitana de Santiago",
    "Región de Del Libertador Gral. Bernardo O'Higgins",
}
OWNER = "Dirección Meteorológica de Chile"
FIELDS = (
    "numero", "codigo_nacional", "codigo_oaci", "nombre", "latitud", "longitud",
    "altitud_m", "altura_minima_m", "propietario", "fecha_creacion", "tipo",
    "clasificacion", "zona_geografica", "region", "emite_metar", "emite_synop",
    "paisaje", "operador",
)


def clean(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_national(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("utf-8", errors="replace")
    records: list[dict[str, str]] = []
    for pane in re.split(r'<div class="tab-pane[^>]*>', text, flags=re.I)[1:]:
        heading = re.search(r"<h4[^>]*>(.*?)<h4>", pane, re.I | re.S)
        region = clean(heading.group(1)) if heading else ""
        if region not in TARGET_REGIONS:
            continue
        for tr in re.findall(r'<tr class="default">(.*?)</tr>', pane, re.I | re.S):
            cells = [clean(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.I | re.S)]
            if len(cells) != 9 or not cells[0].isdigit() or cells[2] != OWNER:
                continue
            records.append({
                "numero": "", "codigo_nacional": cells[0], "codigo_oaci": "",
                "nombre": cells[1], "latitud": cells[3], "longitud": cells[4],
                "altitud_m": cells[5], "altura_minima_m": "", "propietario": "DMC",
                "fecha_creacion": "", "tipo": "", "clasificacion": "",
                "zona_geografica": "", "region": region.replace("Región de ", "", 1),
                "emite_metar": "", "emite_synop": "", "paisaje": "", "operador": "DMC",
            })
    return records


def fetch_sheet(record: dict[str, str], timeout: float) -> dict[str, str]:
    code = record["codigo_nacional"]
    response = requests.get(
        f"{BASE}/informacion/fichaDeEstacion/{code}",
        headers={"User-Agent": "catalogo-estaciones-DMC/1.0"}, timeout=timeout,
    )
    response.raise_for_status()
    text = response.text
    creation = re.search(
        r"Fecha de Creaci[oó]n\s*</th>\s*<td[^>]*>\s*([^<]+)", text, re.I,
    )
    oaci = re.search(r"C[oó]digo OACI\s*</th>\s*<td[^>]*>\s*([^<]*)", text, re.I)
    zone = re.search(r"Zona Geogr[aá]fica\s*</th>\s*<td[^>]*>\s*([^<]*)", text, re.I)
    if creation:
        record["fecha_creacion"] = clean(creation.group(1)).split()[0]
    if oaci:
        record["codigo_oaci"] = clean(oaci.group(1))
    if zone:
        record["zona_geografica"] = clean(zone.group(1))
    return record


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
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    with gzip.open(NATIONAL, "rb") as handle:
        central = parse_national(handle.read())
    completed: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_sheet, row, args.timeout) for row in central]
        for index, future in enumerate(as_completed(futures), 1):
            completed.append(future.result())
            if index % 25 == 0:
                print(f"fichas={index}/{len(futures)}", flush=True)

    with (RAW / "inventario_saclim_regiones.csv").open(encoding="utf-8", newline="") as handle:
        original_all = list(csv.DictReader(handle))
    with (RAW / "inventario_dmc.csv").open(encoding="utf-8", newline="") as handle:
        original_dmc = list(csv.DictReader(handle))
    central_codes = {row["codigo_nacional"] for row in completed}
    all_rows = [row for row in original_all if row["codigo_nacional"] not in central_codes] + completed
    dmc_rows = [row for row in original_dmc if row["codigo_nacional"] not in central_codes] + completed
    all_rows.sort(key=lambda row: row["codigo_nacional"])
    dmc_rows.sort(key=lambda row: row["codigo_nacional"])
    write(RAW / "inventario_saclim_regiones.csv", all_rows)
    write(RAW / "inventario_dmc.csv", dmc_rows)
    write(RAW / "inventario_dmc_centro.csv", sorted(completed, key=lambda row: row["codigo_nacional"]))
    missing = [row["codigo_nacional"] for row in completed if not row["fecha_creacion"]]
    print(f"centro={len(completed)} inventario_dmc={len(dmc_rows)} sin_fecha={len(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
