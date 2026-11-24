#!/usr/bin/env python3
"""Construye catalogo_dmc.csv desde las evidencias DMC verificadas."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datos_originales" / "dmc"
OUTPUT = ROOT / "catalogo_dmc.csv"
API_ROOT = RAW / "api_15min"
CURRENT_EVIDENCE_MONTH = "202610"
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
    availability_rows = read(RAW / "disponibilidad_actual.csv")
    central_availability = RAW / "disponibilidad_actual_centro.csv"
    if central_availability.exists():
        availability_rows.extend(read(central_availability))
    availability = {
        (row["codigo_nacional"], row["variable"]): row["datos_disponibles_actualmente"]
        for row in availability_rows
        if row.get("ventana_desde", "").startswith("2026-10")
        and row.get("ventana_hasta", "").startswith("2026-10")
    }
    public_october_path = RAW / "disponibilidad_octubre_ema_publico.csv"
    public_october_rows = read(public_october_path) if public_october_path.exists() else []
    public_october: dict[tuple[str, str], str] = {}
    for row in public_october_rows:
        key = (row["codigo_nacional"], row["variable"])
        state = row["datos_disponibles_actualmente"]
        previous = public_october.get(key, "No determinado")
        if state == "Sí" or previous == "Sí":
            public_october[key] = "Sí"
        elif state == "No" or previous == "No":
            public_october[key] = "No"
        else:
            public_october[key] = "No determinado"
    # La API se procesa en memoria. Estos CSV contienen únicamente metadatos
    # derivados; no contienen la serie temporal ni las respuestas JSON.
    api_summary_path = API_ROOT / "resumen_series_api_15min.csv"
    api_monthly_path = API_ROOT / "resumen_mensual_api_15min.csv"
    api_manifest_path = API_ROOT / "manifest_api_15min.csv"
    api_summaries = read(api_summary_path) if api_summary_path.exists() else []
    api_monthly = read(api_monthly_path) if api_monthly_path.exists() else []
    api_manifest = read(api_manifest_path) if api_manifest_path.exists() else []
    october_variables = {
        (row["station_code"], row["variable"])
        for row in api_monthly
        if row["evidence_month"] == CURRENT_EVIDENCE_MONTH
        and row.get("valid_values", "0").isdigit()
        and int(row["valid_values"]) > 0
    }
    october_station_status = {
        row["station_code"]: row["status"]
        for row in api_manifest
        if row["month"] == CURRENT_EVIDENCE_MONTH
    }
    evidence: list[dict[str, str]] = []
    for row in api_summaries:
        evidence.append({
            "codigo_nacional": row["station_code"],
            "variable": row["variable"],
            "altura_sensor_suelo_m": row["height_m"],
            "frecuencia_muestreo": row["frequency"],
            "primera_fecha_verificada": row["first_timestamp"],
        })
    all_regions_public = RAW / "sondeo_ema_minuto_publico_todas_regiones.csv"
    if all_regions_public.exists():
        evidence.extend(read(all_regions_public))
        # Estas dos estaciones son de ESO, pero sus productos EMA se publican
        # en el portal DMC y por eso se conservan en esta red.
        eso_public = RAW / "sondeo_ema_minuto_publico_eso.csv"
        if eso_public.exists():
            evidence.extend(read(eso_public))
    else:
        # Compatibilidad con las auditorías parciales anteriores.
        for name in (
            "sondeo_ema_minuto_publico_pendientes.csv",
            "sondeo_ema_minuto_publico_centro.csv",
            "sondeo_ema_minuto_publico_eso.csv",
        ):
            path = RAW / name
            if path.exists():
                evidence.extend(read(path))
    saclim_rows = read(RAW / "sondeo_saclim_horario.csv")
    for row in saclim_rows:
        if row["estado"] != "con_datos" or not row["frecuencia_observada_min"].isdigit():
            continue
        evidence.append({
            "codigo_nacional": row["codigo_nacional"],
            "variable": row["variable"],
            "altura_sensor_suelo_m": "No informado",
            "frecuencia_muestreo": f"{row['frecuencia_observada_min']} min",
            "primera_fecha_verificada": row["primera_fecha_verificada"],
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
        public_state = public_october.get((code, item["variable"]), fallback_state)
        station_october_status = october_station_status.get(code)
        if (code, item["variable"]) in october_variables:
            current_state = "Sí"
        elif public_state == "Sí":
            # Una observación válida en cualquier producto oficial de octubre
            # basta; la ausencia del campo en la API no invalida el ZIP EMA.
            current_state = "Sí"
        elif station_october_status in {"verified", "no_data"}:
            # El criterio acordado es al menos un valor físicamente válido
            # de la variable durante octubre de 2026.
            current_state = "No"
        else:
            current_state = public_state
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
            "datos_disponibles_actualmente": current_state,
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
