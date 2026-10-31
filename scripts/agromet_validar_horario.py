#!/usr/bin/env python3
"""Verifica numéricamente las primeras fechas y la ventana actual de AGROMET.

Parte del sondeo diario histórico y consulta sólo ventanas horarias pequeñas:
el día de la primera observación (con un día de margen a cada lado) y los
siete días completos usados para disponibilidad actual.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from agromet_table_downloader import BASE_URL, parse_table, request
from agromet_sondear_disponibilidad import FIELDS, is_valid


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def query_first(station: str, variables: list[str], candidate: date) -> dict:
    start, end = candidate - timedelta(days=1), candidate + timedelta(days=1)
    payload = {
        "estaciones[]": station,
        "variables[]": variables,
        "intervalo": "hour",
        "desde": start.strftime("%d-%m-%Y"),
        "hasta": end.strftime("%d-%m-%Y"),
        "vista[]": "tabla",
    }
    content = request(BASE_URL, payload)
    invalid: list[str] = []
    rows, timezone = parse_table(content, variables, "hour", invalid)
    result = {}
    for column, variable in enumerate(variables, start=1):
        found = [
            (datetime.fromisoformat(row[0]), row[column].strip())
            for row in rows
            if is_valid(row[column], variable)
        ]
        result[(station, variable)] = min(found, default=None, key=lambda item: item[0])
    return {
        "values": result,
        "timezone": timezone,
        "invalid_timestamps": invalid,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def query_current(stations: list[str], variable: str, start: date, end: date) -> dict:
    payload = {
        "estaciones[]": stations,
        "variables[]": variable,
        "intervalo": "hour",
        "desde": start.strftime("%d-%m-%Y"),
        "hasta": end.strftime("%d-%m-%Y"),
        "vista[]": "tabla",
    }
    content = request(BASE_URL, payload)
    series = [f"{station}__{variable}" for station in stations]
    invalid: list[str] = []
    rows, timezone = parse_table(content, series, "hour", invalid)
    result = {}
    first_current = {}
    for column, station in enumerate(stations, start=1):
        found = [
            (datetime.fromisoformat(row[0]), row[column].strip())
            for row in rows
            if start <= datetime.fromisoformat(row[0]).date() <= end
            and is_valid(row[column], variable)
        ]
        result[(station, variable)] = "Sí" if found else "No"
        first_current[(station, variable)] = min(found, default=None, key=lambda item: item[0])
    return {
        "states": result,
        "first_current": first_current,
        "timezone": timezone,
        "invalid_timestamps": invalid,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--reuse-first",
        action="store_true",
        help="Reutiliza primeras fechas/valores ya verificados y repite sólo la ventana actual",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 2:
        parser.error("--workers debe estar entre 1 y 2")

    with args.input.open(encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    by_key = {(row["estacion_id"], row["variable_codigo"]): row for row in records}
    current_end = args.as_of - timedelta(days=1)
    current_start = current_end - timedelta(days=args.window_days - 1)

    first_groups: dict[tuple[str, date], list[str]] = defaultdict(list)
    for row in records:
        value = row["primera_fecha_disponible"]
        if value and value != "No informado":
            candidate = datetime.fromisoformat(value).date()
            first_groups[(row["estacion_id"], candidate)].append(row["variable_codigo"])
    first_tasks = [
        (station, variable_chunk, candidate)
        for (station, candidate), variables in first_groups.items()
        for variable_chunk in chunks(sorted(variables), 6)
    ]
    reused_first = {
        (row["estacion_id"], row["variable_codigo"]): (
            datetime.fromisoformat(row["primera_fecha_disponible"]),
            row["primer_valor_verificado"],
        )
        for row in records
        if args.reuse_first
        and row.get("primer_valor_verificado")
        and row["primera_fecha_disponible"] != "No informado"
    }
    if args.reuse_first:
        first_tasks = []

    variables = sorted({row["variable_codigo"] for row in records})
    stations = sorted({row["estacion_id"] for row in records})
    current_tasks = [
        (station_chunk, variable)
        for variable in variables
        for station_chunk in chunks(stations, 10)
    ]

    first_values: dict[tuple[str, str], tuple[datetime, str] | None] = reused_first
    current_states: dict[tuple[str, str], str] = {}
    first_current: dict[tuple[str, str], tuple[datetime, str] | None] = {}
    failures: list[dict] = []
    timezones: set[str] = set()
    invalid_timestamps: list[str] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(query_first, station, vars_, candidate): (station, vars_, candidate)
            for station, vars_, candidate in first_tasks
        }
        for future in as_completed(futures):
            station, vars_, candidate = futures[future]
            try:
                result = future.result()
                first_values.update(result["values"])
                timezones.add(result["timezone"])
                invalid_timestamps.extend(result["invalid_timestamps"])
            except Exception as exc:
                failures.append(
                    {"etapa": "primera_fecha", "estacion": station, "variables": vars_, "error": repr(exc)}
                )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(query_current, station_group, variable, current_start, current_end): (
                station_group,
                variable,
            )
            for station_group, variable in current_tasks
        }
        for future in as_completed(futures):
            station_group, variable = futures[future]
            try:
                result = future.result()
                current_states.update(result["states"])
                first_current.update(result["first_current"])
                timezones.add(result["timezone"])
                invalid_timestamps.extend(result["invalid_timestamps"])
            except Exception as exc:
                failures.append(
                    {"etapa": "ventana_actual", "estacion": station_group, "variables": [variable], "error": repr(exc)}
                )

    consulted = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    missing_hourly: list[dict] = []
    for key, row in by_key.items():
        observed = first_values.get(key)
        if observed is None and row["primera_fecha_disponible"] != "No informado":
            missing_hourly.append(
                {
                    "estacion": key[0],
                    "variable": key[1],
                    "fecha_diaria": row["primera_fecha_disponible"],
                }
            )
        if observed is None and first_current.get(key) is not None:
            observed = first_current[key]
        if observed is not None:
            row["primera_fecha_disponible"] = observed[0].isoformat(timespec="minutes")
            row["primer_valor_verificado"] = observed[1]
        elif row["primera_fecha_disponible"] == "No informado":
            row["primer_valor_verificado"] = ""
        row["datos_disponibles_actualmente"] = current_states.get(key, "No determinado")
        row["ventana_actual_desde"] = current_start.isoformat()
        row["ventana_actual_hasta"] = current_end.isoformat()
        row["fecha_consulta_utc"] = consulted
        row["requiere_extender_inicio"] = "No"
        if row["datos_disponibles_actualmente"] == "No determinado":
            row["estado_consulta"] = "error"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(records, key=lambda row: (row["estacion_id"], row["variable_codigo"])))
    temporary.replace(args.output)

    evidence = {
        "fecha_consulta_utc": consulted,
        "ventana_actual": [current_start.isoformat(), current_end.isoformat()],
        "series": len(records),
        "series_con_primera_fecha_horaria": sum(
            row["primer_valor_verificado"] != "" for row in records
        ),
        "series_sin_fecha_historica": sum(
            row["primera_fecha_disponible"] == "No informado" for row in records
        ),
        "series_con_datos_actuales": sum(
            row["datos_disponibles_actualmente"] == "Sí" for row in records
        ),
        "husos_publicados": sorted(timezones),
        "marcas_temporales_invalidas_omitidas": sorted(set(invalid_timestamps)),
        "sin_valor_horario_en_fecha_diaria": missing_hourly,
        "fallos": failures,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 2 if failures or missing_hourly else 0


if __name__ == "__main__":
    raise SystemExit(main())
