#!/usr/bin/env python3
"""Obtiene primera fecha y disponibilidad actual sin conservar las observaciones."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from agromet_table_downloader import BASE_URL, REGIONS, Station, catalog, parse_table, request


MISSING = {"", "s/i", "si", "nan", "na", "null", "none", "no informado"}
FIELDS = (
    "estacion_id",
    "nombre_publicado",
    "region",
    "variable_codigo",
    "primera_fecha_disponible",
    "primer_valor_verificado",
    "datos_disponibles_actualmente",
    "ventana_actual_desde",
    "ventana_actual_hasta",
    "fecha_consulta_utc",
    "periodo_consultado_desde",
    "periodo_consultado_hasta",
    "requiere_extender_inicio",
    "sha256_respuesta_html",
    "estado_consulta",
    "error",
)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use fechas AAAA-MM-DD") from exc


def numeric_value(value: str) -> float | None:
    text = value.strip()
    if text.casefold() in MISSING:
        return None
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def is_valid(value: str, variable: str) -> bool:
    number = numeric_value(value)
    if number is None:
        return False
    if variable == "DV_AVG":
        return 0.0 <= number <= 360.0
    if variable == "HR_AVG":
        return 0.0 <= number <= 100.0
    if variable == "PS_AVG":
        return number > 0.0
    if variable in {"PP_SUM", "VV_AVG", "VV_MAX"}:
        return number >= 0.0
    if variable == "TA_AVG":
        return -80.0 <= number <= 80.0
    if variable == "RD_AVG":
        return -50.0 <= number <= 2000.0
    return True


def station_groups(stations: list[Station], size: int) -> list[list[Station]]:
    return [stations[index : index + size] for index in range(0, len(stations), size)]


def load_existing(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            (row["estacion_id"], row["variable_codigo"]): row
            for row in csv.DictReader(handle)
        }


def save_summary(path: Path, records: dict[tuple[str, str], dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records[key] for key in sorted(records))
    temp.replace(path)


def query_group(
    stations: list[Station],
    variable: str,
    start: date,
    end: date,
    current_start: date,
    current_end: date,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    payload = {
        "estaciones[]": [station.id for station in stations],
        "variables[]": variable,
        "intervalo": "day",
        "desde": start.strftime("%d-%m-%Y"),
        "hasta": end.strftime("%d-%m-%Y"),
        "vista[]": "tabla",
    }
    content = request(BASE_URL, payload)
    series = [f"{station.id}__{variable}" for station in stations]
    invalid_timestamps: list[str] = []
    rows, timezone = parse_table(content, series, "day", invalid_timestamps)
    response_hash = hashlib.sha256(content).hexdigest()
    consulted = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    results: list[dict[str, str]] = []
    evidence_stations: list[dict[str, object]] = []
    for column, station in enumerate(stations, start=1):
        first: str | None = None
        first_observed_value: str | None = None
        current_valid_count = 0
        for row in rows:
            stamp = datetime.fromisoformat(row[0])
            valid = is_valid(row[column], variable)
            if valid and first is None:
                first = stamp.date().isoformat()
                first_observed_value = row[column].strip()
            if current_start <= stamp.date() <= current_end and valid:
                current_valid_count += 1
        current = "Sí" if current_valid_count else "No"
        first_date_value = first or "No informado"
        results.append(
            {
                "estacion_id": station.id,
                "nombre_publicado": station.name,
                "region": station.region,
                "variable_codigo": variable,
                "primera_fecha_disponible": first_date_value,
                "primer_valor_verificado": first_observed_value or "",
                "datos_disponibles_actualmente": current,
                "ventana_actual_desde": current_start.isoformat(),
                "ventana_actual_hasta": current_end.isoformat(),
                "fecha_consulta_utc": consulted,
                "periodo_consultado_desde": start.isoformat(),
                "periodo_consultado_hasta": end.isoformat(),
                "requiere_extender_inicio": "Sí" if first == start.isoformat() else "No",
                "sha256_respuesta_html": response_hash,
                "estado_consulta": "completada",
                "error": "",
            }
        )
        evidence_stations.append(
            {
                "id": station.id,
                "nombre": station.name,
                "region": station.region,
                "primera_fecha_disponible": first_date_value,
                "primer_valor_verificado": first_observed_value or "",
                "cantidad_dias_con_valor_en_ventana_actual": current_valid_count,
                "datos_disponibles_actualmente": current,
            }
        )
    evidence = {
        "fuente": BASE_URL,
        "metodo": "vista[]=tabla; intervalo=day; observaciones no conservadas",
        "variable": variable,
        "periodo_consultado": [start.isoformat(), end.isoformat()],
        "ventana_actual": [current_start.isoformat(), current_end.isoformat()],
        "huso_horario_publicado": timezone,
        "fecha_consulta_utc": consulted,
        "sha256_respuesta_html": response_hash,
        "cantidad_filas_procesadas": len(rows),
        "marcas_temporales_invalidas_omitidas": invalid_timestamps,
        "estaciones": evidence_stations,
    }
    return results, evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variable", required=True)
    parser.add_argument("--from", dest="start", type=parse_date, default=date(2009, 1, 1))
    parser.add_argument("--as-of", type=parse_date, required=True, help="Fecha local de evaluación")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--stations-per-query", type=int, default=10)
    parser.add_argument("--region", action="append", default=[])
    parser.add_argument("--station", action="append", default=[])
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument(
        "--skip-completed",
        action="store_true",
        help="Omite combinaciones ya registradas con estado completada",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()

    if args.window_days < 1 or args.stations_per_query < 1:
        raise SystemExit("--window-days y --stations-per-query deben ser al menos 1")
    end = args.as_of - timedelta(days=1)
    current_start = end - timedelta(days=args.window_days - 1)
    if args.start > end:
        raise SystemExit("--from debe ser anterior a la ventana actual")

    stations, variables = catalog()
    if args.variable not in variables:
        raise SystemExit(f"Variable desconocida: {args.variable}")
    selected = [station for station in stations if station.region in REGIONS]
    if args.region:
        invalid = set(args.region) - set(REGIONS)
        if invalid:
            raise SystemExit(f"Regiones fuera del alcance: {', '.join(sorted(invalid))}")
        selected = [station for station in selected if station.region in set(args.region)]
    if args.station:
        wanted = set(args.station)
        selected = [station for station in selected if station.id in wanted]
        missing = wanted - {station.id for station in selected}
        if missing:
            raise SystemExit(f"Estaciones desconocidas: {', '.join(sorted(missing))}")
    records = load_existing(args.output)
    if args.skip_completed:
        selected = [
            station
            for station in selected
            if records.get((station.id, args.variable), {}).get("estado_consulta") != "completada"
        ]
    if not selected:
        print("No hay estaciones pendientes")
        return 0
    groups = station_groups(selected, args.stations_per_query)
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    print(f"Variable: {args.variable} | estaciones: {len(selected)} | consultas: {len(groups)}")
    for index, group in enumerate(groups, start=1):
        ids = "-".join(station.id for station in group)
        try:
            results, evidence = query_group(
                group, args.variable, args.start, end, current_start, end
            )
            for result in results:
                records[(result["estacion_id"], result["variable_codigo"])] = result
            evidence_path = args.evidence_dir / f"{args.variable}_{index:02d}_{ids}.json"
            evidence_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            yes_count = sum(result["datos_disponibles_actualmente"] == "Sí" for result in results)
            print(
                f"[{index}/{len(groups)}] {len(group)} estaciones: completada; "
                f"{yes_count} con datos actuales",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            consulted = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            for station in group:
                records[(station.id, args.variable)] = {
                    "estacion_id": station.id,
                    "nombre_publicado": station.name,
                    "region": station.region,
                    "variable_codigo": args.variable,
                    "primera_fecha_disponible": "No informado",
                    "primer_valor_verificado": "",
                    "datos_disponibles_actualmente": "No determinado",
                    "ventana_actual_desde": current_start.isoformat(),
                    "ventana_actual_hasta": end.isoformat(),
                    "fecha_consulta_utc": consulted,
                    "periodo_consultado_desde": args.start.isoformat(),
                    "periodo_consultado_hasta": end.isoformat(),
                    "requiere_extender_inicio": "No determinado",
                    "sha256_respuesta_html": "",
                    "estado_consulta": "error",
                    "error": str(exc),
                }
            print(f"[{index}/{len(groups)}] ERROR {ids}: {exc}", flush=True)
        save_summary(args.output, records)
        if index < len(groups) and args.pause > 0:
            time.sleep(args.pause)

    print(f"Resumen actualizado: {args.output}")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
