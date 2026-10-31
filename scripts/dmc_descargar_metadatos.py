#!/usr/bin/env python3
"""Descarga inventarios y evidencia reciente pública del portal DMC.

El catastro SACLIM es HTML aunque su nombre termine en ``.xls``. Este script
conserva una copia comprimida, extrae las estaciones de las cuatro regiones de
interés y consulta los servicios GeoJSON que no requieren credenciales.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import requests


ROOT = Path("/home/alagos/catalogo_estaciones")
DEFAULT_CATALOG = ROOT / "datos_originales" / "dmc" / "CatastrodeEstacionesdelSistemaSACLIM_2026-10-31_15_25.xls"
DEFAULT_OUTPUT = ROOT / "datos_originales" / "dmc"
BASE = "https://climatologia.meteochile.gob.cl/application"
TARGET_REGIONS = {"Arica y Parinacota", "Tarapaca", "Tarapacá", "Antofagasta", "Atacama"}
REGION_IDS = {"Arica y Parinacota": 15, "Tarapaca": 1, "Antofagasta": 2, "Atacama": 3}
VARIABLES = (
    "Radiación solar", "Precipitación", "Temperatura", "Humedad", "Presión",
    "Intensidad del viento", "Dirección del viento", "Ráfaga del viento",
)


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None


def atomic_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def atomic_gzip(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with gzip.open(temporary, "wb", compresslevel=9) as handle:
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fetch_json(session: requests.Session, endpoint: str, timeout: float) -> dict:
    url = f"{BASE}/{endpoint.lstrip('/')}"
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("mensaje"):
        raise ValueError(f"Respuesta no utilizable de {url}: {payload.get('mensaje', 'no es un objeto')}")
    return payload


def number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    raw = args.catalog.read_bytes()
    text = raw.decode("utf-8")
    parsed = TableParser()
    parsed.feed(text)
    header = next((row for row in parsed.rows if row and row[0] == "N°"), None)
    if header is None or len(header) != 18:
        raise ValueError("No se reconoció la tabla de 18 columnas del catastro SACLIM")

    catalog_rows = [row for row in parsed.rows if len(row) == 18 and row[1].isdigit()]
    target_rows = [row for row in catalog_rows if row[13] in TARGET_REGIONS]
    dmc_rows = [row for row in target_rows if row[8] == "DMC"]
    if not dmc_rows:
        raise ValueError("El catastro no contiene estaciones DMC en las regiones objetivo")

    fields = (
        "numero", "codigo_nacional", "codigo_oaci", "nombre", "latitud", "longitud",
        "altitud_m", "altura_minima_m", "propietario", "fecha_creacion", "tipo",
        "clasificacion", "zona_geografica", "region", "emite_metar", "emite_synop",
        "paisaje", "operador",
    )
    all_records = [dict(zip(fields, row)) for row in target_rows]
    dmc_records = [dict(zip(fields, row)) for row in dmc_rows]
    atomic_gzip(args.output / "catastro_saclim_2026-10-31.html.gz", raw)
    atomic_csv(args.output / "inventario_saclim_regiones.csv", fields, all_records)
    atomic_csv(args.output / "inventario_dmc.csv", fields, dmc_records)

    session = requests.Session()
    session.headers["User-Agent"] = "catalogo-estaciones-DMC/1.0"
    red = fetch_json(session, "servicios/getEstacionesRedEma", args.timeout)
    resumen = fetch_json(session, "geoservicios/getEmaResumenDiarioGeo", args.timeout)
    informe = fetch_json(session, "geoservicios/getClimatDiarioRecienteEmas", args.timeout)
    atomic_gzip(args.output / "red_ema.json.gz", json.dumps(red, ensure_ascii=False).encode())
    atomic_gzip(args.output / "resumen_diario_ema.geojson.gz", json.dumps(resumen, ensure_ascii=False).encode())
    atomic_gzip(args.output / "informe_diario_temp_precip.geojson.gz", json.dumps(informe, ensure_ascii=False).encode())

    end = args.as_of - timedelta(days=1)
    start = end - timedelta(days=6)
    boletines: dict[str, object] = {
        "fecha_consulta": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ventana": {"desde": start.isoformat(), "hasta": end.isoformat()},
        "temperatura": {}, "precipitacion": {},
    }
    for day_offset in range(7):
        day = start + timedelta(days=day_offset)
        for region, region_id in REGION_IDS.items():
            suffix = f"{region_id}/{day.year}/{day.month}/{day.day}"
            key = f"{region_id}_{day.isoformat()}"
            boletines["temperatura"][key] = fetch_json(
                session, f"geoservicios/getBoletinTemperaturaDiarioRegional/{suffix}", args.timeout,
            )
            boletines["precipitacion"][key] = fetch_json(
                session, f"geoservicios/getBoletinPluviometricoDiarioRegional/{suffix}", args.timeout,
            )
    atomic_gzip(
        args.output / "boletines_regionales_7dias.geojson.gz",
        json.dumps(boletines, ensure_ascii=False).encode(),
    )

    codes = {record["codigo_nacional"] for record in dmc_records}
    evidence: dict[tuple[str, str], tuple[str, str, str]] = {}
    for kind, variable, value_fields in (
        ("temperatura", "Temperatura", ("media", "minima", "maxima")),
        ("precipitacion", "Precipitación", ("precipitacion",)),
    ):
        for key, payload in boletines[kind].items():
            day = key.split("_", 1)[1]
            for feature in payload.get("features", []):
                props = feature.get("properties", {})
                code = str(props.get("codigoNacional") or props.get("CodigoNacional") or "")
                if code not in codes:
                    continue
                values = [number(props.get(field)) for field in value_fields]
                if any(value is not None for value in values):
                    evidence.setdefault((code, variable), ("Sí", day, f"Boletín regional diario: {value_fields}"))

    for feature in resumen.get("features", []):
        props = feature.get("properties", {})
        code = str(props.get("codigoNacional") or "")
        if code not in codes:
            continue
        value = number(props.get("intensidadMaxVientoAyer"))
        if value is not None and value >= 0:
            evidence[(code, "Ráfaga del viento")] = (
                "Sí", end.isoformat(), "intensidadMaxVientoAyer del resumen EMA",
            )

    current_fields = {
        "Radiación solar": "radiacionGlobalInst",
        "Humedad": "humedad",
        "Presión": "presion",
        "Intensidad del viento": "intensidadDelViento",
        "Dirección del viento": "direccionDelViento",
    }
    current_today: dict[tuple[str, str], object] = {}
    for feature in resumen.get("features", []):
        props = feature.get("properties", {})
        code = str(props.get("codigoNacional") or "")
        if code in codes:
            for variable, field in current_fields.items():
                if number(props.get(field)) is not None:
                    current_today[(code, variable)] = props.get(field)

    out = []
    for station in sorted(dmc_records, key=lambda row: row["codigo_nacional"]):
        code = str(station["codigo_nacional"])
        for variable in VARIABLES:
            state, stamp, detail = evidence.get(
                (code, variable),
                ("No determinado", "", "Sin evidencia fechada dentro de los siete días completos"),
            )
            today_value = current_today.get((code, variable), "")
            if today_value != "" and state == "No determinado":
                detail += "; sí existe valor el día de consulta, fuera de la ventana estricta"
            out.append({
                "codigo_nacional": code,
                "nombre_estacion": station["nombre"],
                "variable": variable,
                "ventana_desde": start.isoformat(),
                "ventana_hasta": end.isoformat(),
                "datos_disponibles_actualmente": state,
                "fecha_evidencia": stamp,
                "valor_dia_consulta": today_value,
                "detalle": detail,
            })
    availability_fields = (
        "codigo_nacional", "nombre_estacion", "variable", "ventana_desde", "ventana_hasta",
        "datos_disponibles_actualmente", "fecha_evidencia", "valor_dia_consulta", "detalle",
    )
    atomic_csv(args.output / "disponibilidad_actual.csv", availability_fields, out)
    print(
        f"catastro_regiones={len(all_records)} estaciones_dmc={len(dmc_records)} "
        f"ventana={start}/{end} evidencias_si={sum(row['datos_disponibles_actualmente'] == 'Sí' for row in out)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
