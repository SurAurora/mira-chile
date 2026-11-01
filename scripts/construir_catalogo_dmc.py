#!/usr/bin/env python3
"""Construye catalogo_dmc.csv desde las evidencias DMC verificadas."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datos_originales" / "dmc"
OUTPUT = ROOT / "catalogo_dmc.csv"
COLUMNS = [
    "nombre_estacion", "id", "fuente_institucion", "latitud", "longitud",
    "altitud_snm_m", "variable", "tipo_variable", "altura_sensor_suelo_m",
    "frecuencia_muestreo", "primera_fecha_disponible",
    "datos_disponibles_actualmente", "readme_descarga",
]


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def altitude(value: str) -> str:
    text = value.strip().replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return "No informado"
    return str(int(number)) if number.is_integer() else str(number)


def main() -> int:
    # El portal SACLIM también publica estaciones de otras instituciones.
    # El inventario permite atribuir cada serie a su propietario sin excluirla
    # de la red/portal DMC. Sólo la evidencia con valores genera filas.
    stations = {row["codigo_nacional"]: row for row in read(RAW / "inventario_saclim_regiones.csv")}
    availability = {
        (row["codigo_nacional"], row["variable"]): row["datos_disponibles_actualmente"]
        for row in read(RAW / "disponibilidad_actual.csv")
    }
    api_availability_path = RAW / "disponibilidad_actual_api_15min.csv"
    api_availability = {}
    if api_availability_path.exists():
        api_availability = {
            (row["codigo_nacional"], row["variable"], row["altura_sensor_suelo_m"]):
                row["datos_disponibles_actualmente"]
            for row in read(api_availability_path)
        }
    evidence: list[dict[str, str]] = []
    evidence.extend(read(RAW / "sondeo_ema_minuto_existente.csv"))
    public_pending = RAW / "sondeo_ema_minuto_publico_pendientes.csv"
    if public_pending.exists():
        evidence.extend(read(public_pending))
    eso_public = RAW / "sondeo_ema_minuto_publico_eso.csv"
    if eso_public.exists():
        evidence.extend(read(eso_public))
    evidence.extend(read(RAW / "sondeo_ema_15min_existente.csv"))
    eso_current_path = RAW / "disponibilidad_actual_eso_minuto.csv"
    eso_current = {}
    if eso_current_path.exists():
        eso_current = {
            (row["codigo_nacional"], row["variable"], row["altura_sensor_suelo_m"], row["frecuencia_muestreo"]):
                row["datos_disponibles_actualmente"]
            for row in read(eso_current_path)
        }
    for row in read(RAW / "sondeo_saclim_horario.csv"):
        if row["estado"] != "con_datos" or not row["frecuencia_observada_min"].isdigit():
            continue
        evidence.append({
            "codigo_nacional": row["codigo_nacional"],
            "variable": row["variable"],
            "altura_sensor_suelo_m": "No informado",
            "frecuencia_muestreo": f"{row['frecuencia_observada_min']} min",
            "primera_fecha_verificada": row["primera_fecha_verificada"],
            # El boletín reciente prueba la variable, pero no la permanencia
            # del antiguo intervalo sinóptico de 1, 3 o 5 horas.
            "estado_actual_override": "No determinado",
        })

    by_key: dict[tuple[str, ...], dict[str, str]] = {}
    for item in evidence:
        code = item["codigo_nacional"]
        if code not in stations:
            continue
        station = stations[code]
        key = (
            code, item["variable"], item["altura_sensor_suelo_m"],
            item["frecuencia_muestreo"],
        )
        fallback_state = availability.get((code, item["variable"]), "No determinado")
        api_state = api_availability.get(
            (code, item["variable"], item["altura_sensor_suelo_m"]), "No determinado"
        )
        if "Sí" in {fallback_state, api_state}:
            current_state = "Sí"
        elif api_state == "No":
            current_state = "No"
        else:
            current_state = fallback_state
        row = {
            "nombre_estacion": station["nombre"],
            "id": code,
            "fuente_institucion": station["propietario"] or "No informado",
            "latitud": station["latitud"],
            "longitud": station["longitud"],
            "altitud_snm_m": altitude(station["altitud_m"]),
            "variable": item["variable"],
            "tipo_variable": "Meteorológica",
            "altura_sensor_suelo_m": item["altura_sensor_suelo_m"],
            "frecuencia_muestreo": item["frecuencia_muestreo"],
            "primera_fecha_disponible": item["primera_fecha_verificada"],
            "datos_disponibles_actualmente": item.get("estado_actual_override") or eso_current.get(key, current_state),
            "readme_descarga": "fuentes/dmc/README.md",
        }
        previous = by_key.get(key)
        if previous is None or row["primera_fecha_disponible"] < previous["primera_fecha_disponible"]:
            by_key[key] = row

    rows = sorted(by_key.values(), key=lambda row: (
        row["id"], row["variable"], row["altura_sensor_suelo_m"], row["frecuencia_muestreo"],
    ))
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"estaciones={len({row['id'] for row in rows})} filas={len(rows)} salida={OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
