#!/usr/bin/env python3
"""Valida disponibilidad reciente de DMC central con el resumen EMA público."""

from __future__ import annotations

import csv
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datos_originales" / "dmc"
URL = "https://climatologia.meteochile.gob.cl/application/geoservicios/getEmaResumenDiarioGeo"
VARIABLES = {
    "Temperatura": ("temperatura", -80, 80),
    "Humedad": ("humedad", 0, 100),
    "Presión": ("presion", 1, 1200),
    "Dirección del viento": ("direccionDelViento", 0, 360),
    "Intensidad del viento": ("intensidadDelViento", 0, 300),
    "Radiación solar": ("radiacionGlobalInst", -50, 2000),
    "Ráfaga del viento": ("intensidadMaxVientoAyer", 0, 300),
}


def number(value: object) -> float | None:
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def main() -> int:
    with (RAW / "inventario_dmc_centro.csv").open(encoding="utf-8", newline="") as handle:
        inventory = list(csv.DictReader(handle))
    response = requests.get(URL, headers={"User-Agent": "catalogo-estaciones-DMC/1.0"}, timeout=60)
    response.raise_for_status()
    payload = response.json()
    (RAW / "resumen_diario_ema_centro.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    features = {
        str(item.get("properties", {}).get("codigoNacional")): item.get("properties", {})
        for item in payload.get("features", [])
    }
    today = date.today()
    rows = []
    for station in inventory:
        code = station["codigo_nacional"]
        props = features.get(code, {})
        stamp = str(props.get("fechaCreacion", ""))
        for variable, (field, low, high) in VARIABLES.items():
            value = number(props.get(field))
            valid = value is not None and low <= value <= high
            rows.append({
                "codigo_nacional": code, "nombre_estacion": station["nombre"],
                "variable": variable, "ventana_desde": (today - timedelta(days=1)).isoformat(),
                "ventana_hasta": today.isoformat(),
                "datos_disponibles_actualmente": "Sí" if valid else "No determinado",
                "fecha_evidencia": stamp, "valor_dia_consulta": value if valid else "",
                "detalle": f"{field} del resumen EMA público" if valid else "Sin valor verificable en el resumen EMA público",
            })
        rain = number(props.get("aguaCaidaAyer"))
        rain_count = number(props.get("aguaCaidaAyerDatos"))
        valid_rain = rain is not None and rain >= 0 and rain_count is not None and rain_count > 0
        rows.append({
            "codigo_nacional": code, "nombre_estacion": station["nombre"],
            "variable": "Precipitación", "ventana_desde": (today - timedelta(days=1)).isoformat(),
            "ventana_hasta": today.isoformat(),
            "datos_disponibles_actualmente": "Sí" if valid_rain else "No determinado",
            "fecha_evidencia": stamp, "valor_dia_consulta": rain if valid_rain else "",
            "detalle": "aguaCaidaAyer con recuento de datos del resumen EMA público" if valid_rain else "Sin valor verificable en el resumen EMA público",
        })
    fields = tuple(rows[0])
    target = RAW / "disponibilidad_actual_centro.csv"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=target.parent,
        prefix=f".{target.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(target)
    print(f"estaciones={len(inventory)} evidencias_si={sum(x['datos_disponibles_actualmente'] == 'Sí' for x in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
