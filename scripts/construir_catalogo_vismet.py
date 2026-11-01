#!/usr/bin/env python3
"""Construye el catálogo VISMET únicamente después de completar el sondeo."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from construir_catalogo_general import COLUMNS


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datos_originales" / "vismet"
DOCS = ROOT / "fuentes" / "vismet"


def catalog_id(station: dict) -> str:
    national = station.get("nationalCode")
    return f"{national} [VISMET-{station['id']}]" if national else f"VISMET-{station['id']}"


def source_institution(station: dict) -> str:
    if "(SERVIMET)" in station["name"].upper():
        return "SERVIMET"
    return station["fuente_institucion"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW, help="Directorio del sondeo que se desea publicar")
    args = parser.parse_args()
    raw = args.raw
    state = json.loads((raw / "estado_sondeo.json").read_text(encoding="utf-8"))
    if not state.get("completo"):
        raise ValueError("Falta terminar el recorrido histórico; no se publica un catálogo parcial")
    stations = json.loads((raw / "estaciones_objetivo.json").read_text(encoding="utf-8"))
    metadata = {str(station["id"]): station for station in stations}
    rows = []
    for key, result in state["primeras"].items():
        station = metadata[str(result["id_vismet"])]
        current = state["actuales"].get(key)
        if current is None:
            raise ValueError(f"Sin verificación actual: {key}")
        rows.append({
            "nombre_estacion": station["name"],
            "id": catalog_id(station),
            "fuente_institucion": source_institution(station),
            "latitud": station["latitude"], "longitud": station["longitude"],
            "altitud_snm_m": station.get("altitude") if station.get("altitude") is not None else "No informado",
            "variable": result["variable"], "tipo_variable": "Meteorológica",
            "altura_sensor_suelo_m": "No informado",
            "frecuencia_muestreo": "60 min",
            "primera_fecha_disponible": result["primera_fecha_disponible"],
            "datos_disponibles_actualmente": current["estado"],
            "readme_descarga": "fuentes/vismet/README.md",
        })
    rows.sort(key=lambda row: (row["fuente_institucion"], row["id"], row["variable"]))
    with (ROOT / "catalogo_vismet.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)

    DOCS.mkdir(parents=True, exist_ok=True)
    station_columns = [
        "id_catalogo", "id_interno_vismet", "codigo_nacional_vismet", "nombre_vismet",
        "organizacion_vismet", "fuente_institucion", "latitud", "longitud",
        "altitud_snm_m", "codigo_region", "region",
    ]
    with (DOCS / "identificadores_estaciones.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=station_columns, lineterminator="\n")
        writer.writeheader()
        for station in sorted(stations, key=lambda row: row["id"]):
            writer.writerow({
                "id_catalogo": catalog_id(station), "id_interno_vismet": station["id"],
                "codigo_nacional_vismet": station["nationalCode"], "nombre_vismet": station["name"],
                "organizacion_vismet": station["organization"]["name"],
                "fuente_institucion": source_institution(station),
                "latitud": station["latitude"], "longitud": station["longitude"],
                "altitud_snm_m": station.get("altitude"),
                "codigo_region": station["codigo_region"], "region": station["region"],
            })

    used = {row["id"] for row in rows}
    summary = {
        "consulta_utc": state["consultado_utc"],
        "inicio_historico_documentado": state["start"],
        "ventana_actual_desde_inclusiva": state["ventana_actual_desde"],
        "ventana_actual_hasta_exclusiva": state["ventana_actual_hasta_exclusiva"],
        "semanas_consecutivas_revisadas": state["semanas_totales"],
        "estaciones_en_area": len(stations), "estaciones_con_series": len(used),
        "metadatos_sin_coordenadas_en_inventario_nacional": len(json.loads((raw / "estaciones_sin_coordenadas.json").read_text(encoding="utf-8"))),
        "series_verificadas": len(rows),
        "variables": dict(Counter(row["variable"] for row in rows)),
        "instituciones_series": dict(Counter(row["fuente_institucion"] for row in rows)),
        "disponibilidad_actual": dict(Counter(row["datos_disponibles_actualmente"] for row in rows)),
        "combinaciones_sin_valores_historicos": [
            key for key in sorted(state["actuales"]) if key not in state["primeras"]
        ],
    }
    (DOCS / "resumen_verificacion.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"VISMET: estaciones={len(used)} series={len(rows)} disponibilidad={summary['disponibilidad_actual']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
