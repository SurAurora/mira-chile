#!/usr/bin/env python3
"""Inspect historical 15-minute DMC EMA data without storing observations.

API responses are processed in memory. Only audit metadata and the first and
last timestamp with a physically valid value per station-variable are persisted;
the observed values themselves are discarded.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import os
import random
import re
import tempfile
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

try:
    from dmc_descargar_metadatos import TableParser
except ModuleNotFoundError:  # Permite importarlo desde pruebas en la raíz.
    from scripts.dmc_descargar_metadatos import TableParser


BASE = "https://climatologia.meteochile.gob.cl/application/servicios/getDatosRecientesEma"
PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "datos_originales" / "dmc" / "api_15min"
DEFAULT_CATALOGS = (
    PROJECT / "datos_originales" / "dmc" / "CatastrodeEstacionesdelSistemaSACLIM_2026-10-31_15_25.xls",
    PROJECT / "datos_originales" / "dmc" / "CatastrodeEstacionesdelSistemaSACLIM_2026-11-24_03_47.xls",
)
EXTRA_PORTAL_CODES = {"230021", "240005"}  # ESO-owned; already downloaded via DMC portal.
SERIES = {
    "temperatura": ("Temperatura", "1.5", -80, 80),
    "temperatura02Mts": ("Temperatura", "2", -80, 80),
    "temperatura10Mts": ("Temperatura", "10", -80, 80),
    "temperatura30Mts": ("Temperatura", "30", -80, 80),
    "humedadRelativa": ("Humedad", "No informado", 0, 100),
    "radiacionGlobalInst": ("Radiación solar", "No informado", -50, 2000),
    "presionEstacion": ("Presión", "No informado", 1, 1200),
    "aguaCaidaDelMinuto": ("Precipitación", "No informado", 0, 1000),
    "direccionDelViento": ("Dirección del viento", "No informado", 0, 360),
    "fuerzaDelViento": ("Intensidad del viento", "No informado", 0, 300),
    "fuerzaDelViento02MinutosMax": ("Ráfaga del viento", "No informado", 0, 300),
    "fuerzaDelViento10MinutosMax": ("Ráfaga del viento", "No informado", 0, 300),
}


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    owner: str
    created: datetime


def stations_from_catalog(
    path: Path, include_eso: bool = True, all_owners: bool = False,
) -> list[Station]:
    parser = TableParser()
    parser.feed(path.read_text(encoding="utf-8"))
    stations = {}
    for cells in parser.rows:
        if len(cells) != 18 or not cells[1].isdigit():
            continue
        code, name, owner = cells[1], cells[3], cells[8]
        try:
            created = datetime.strptime(cells[9].split()[0], "%d-%m-%Y")
        except ValueError:
            created = datetime(1950, 1, 1)
        if all_owners or owner == "DMC" or (include_eso and code in EXTRA_PORTAL_CODES):
            stations[code] = Station(code, name, owner, created)
    return [stations[code] for code in sorted(stations)]


def months_between(start: str, end: str) -> list[str]:
    first = datetime.strptime(start, "%Y-%m")
    last = datetime.strptime(end, "%Y-%m")
    if first > last:
        raise ValueError("start must not exceed end")
    out = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        out.append(f"{year:04d}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def valid_payload(payload: object, month: str) -> tuple[bool, int, str]:
    if not isinstance(payload, dict):
        return False, 0, "not_object"
    station_data = payload.get("datosEstaciones")
    if not isinstance(station_data, dict):
        return False, 0, "missing_station_data"
    records = station_data.get("datos")
    if not isinstance(records, list) or not records:
        return False, 0, "no_records"
    if payload.get("registros") != len(records):
        return False, len(records), "record_count_mismatch"
    expected = None if month == "current" else f"{month[:4]}-{month[4:]}-"
    times = [row.get("momento") for row in records if isinstance(row, dict)]
    if len(times) != len(records) or any(
        not isinstance(stamp, str) or (expected is not None and not stamp.startswith(expected))
        for stamp in times
    ):
        return False, len(records), "timestamps_outside_month"
    if len(times) != len(set(times)):
        return False, len(records), "duplicate_timestamps"
    return True, len(records), "ok"


def numeric(value: object) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)", str(value).strip())
    if not match:
        return None
    try:
        return float(match.group().replace(",", "."))
    except ValueError:
        return None


def summarize_payload(payload: dict, station: Station, evidence_month: str) -> list[dict]:
    records = payload.get("datosEstaciones", {}).get("datos", [])
    found: dict[tuple[str, str], dict] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            stamp = datetime.fromisoformat(str(record.get("momento", "")).strip())
        except ValueError:
            continue
        iso = stamp.isoformat(timespec="seconds")
        for field, (variable, height, low, high) in SERIES.items():
            value = numeric(record.get(field))
            if value is None or not low <= value <= high:
                continue
            key = (variable, height)
            row = found.setdefault(key, {
                "station_code": station.code, "station_name": station.name,
                "owner": station.owner, "variable": variable,
                "height_m": height, "frequency": "15 min",
                "evidence_month": evidence_month,
                "first_timestamp": iso, "last_timestamp": iso,
                "valid_values": 0, "first_field": field, "last_field": field,
            })
            row["valid_values"] += 1
            if iso < row["first_timestamp"]:
                row.update(first_timestamp=iso, first_field=field)
            if iso > row["last_timestamp"]:
                row.update(last_timestamp=iso, last_field=field)
    return list(found.values())


def classify_payload_without_data(payload: object, email: str, token: str) -> tuple[str, str]:
    """Distingue una ausencia normal de datos de un rechazo de acceso.

    DMC ha usado más de una forma JSON para los meses sin información. Nunca
    se conserva ni imprime la credencial en el detalle de diagnóstico.
    """
    if not isinstance(payload, dict):
        return "invalid_response", "payload_not_object"
    texts = []
    for key in ("mensaje", "message", "status", "estado", "error", "detalle"):
        value = payload.get(key)
        if isinstance(value, str):
            texts.append(value)
    message = " | ".join(texts).replace(token, "[REDACTED]").replace(email, "[REDACTED]")
    lowered = message.casefold()
    if any(word in lowered for word in ("bloque", "block", "token", "credencial", "autoriz")):
        return "access_blocked", message[:240] or "access_rejected"
    if any(phrase in lowered for phrase in (
        "sin información", "sin informacion", "sin datos", "no hay datos",
        "no se encontraron", "no existe información", "no existe informacion",
        "información no disponible", "informacion no disponible",
    )):
        return "no_data", message[:240] or "explicit_no_data"
    if payload.get("registros") in (0, "0"):
        return "no_data", message[:240] or "zero_records"
    keys = ",".join(sorted(str(key) for key in payload))
    return "invalid_response", f"keys={keys}; {message[:180]}".strip()


def fetch(task: tuple[Station, str], output: Path, email: str, token: str,
          timeout: float, pause: float) -> tuple[dict, list[dict]]:
    station, month = task
    base = {
        "station_code": station.code, "station_name": station.name,
        "owner": station.owner, "month": month, "records": 0,
        "status": "", "http_status": "", "bytes": 0, "local_path": "",
        "detail": "",
    }
    url = (
        f"{BASE}/{station.code}"
        if month == "current"
        else f"{BASE}/{station.code}/{month[:4]}/{month[4:]}"
    )
    for attempt in range(3):
        try:
            time.sleep(pause + random.uniform(0, pause))
            response = requests.get(url, params={"usuario": email, "token": token},
                                    timeout=timeout)
            base["http_status"] = response.status_code
            if response.status_code in (401, 403):
                base.update(status="auth_error")
                return base, []
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                base.update(status="http_error")
                return base, []
            if response.status_code != 200:
                base.update(status="http_error")
                return base, []
            if response.text.strip().lower() == "sin información":
                base.update(status="no_data")
                return base, []
            try:
                payload = response.json()
            except ValueError:
                base.update(status="non_json", detail=response.headers.get("Content-Type", ""))
                return base, []
            valid, n, detail = valid_payload(payload, month)
            base["records"] = n
            if not valid:
                status, diagnostic = classify_payload_without_data(payload, email, token)
                base.update(status=status, detail=f"{detail}; {diagnostic}")
                return base, []
            if token in response.text:
                base.update(status="credential_in_response")
                return base, []
            # The response must be transferred to inspect it, but observations
            # are deliberately not persisted after extracting the summary.
            base.update(status="verified", bytes=len(response.content), local_path="")
            return base, summarize_payload(payload, station, month)
        except requests.RequestException as exc:
            if attempt == 2:
                base.update(status="network_error", detail=type(exc).__name__)
                return base, []
            time.sleep(2 ** attempt)
    return base, []


def read_manifest(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            (row["station_code"], row["month"]): row
            for row in csv.DictReader(handle)
        }


def write_manifest(path: Path, rows: list[dict], merge_existing: bool = True) -> None:
    fields = ("station_code", "station_name", "owner", "month", "records", "status",
              "http_status", "bytes", "local_path", "detail")
    path.parent.mkdir(parents=True, exist_ok=True)
    current = {(row["station_code"], row["month"]): row for row in rows}
    if merge_existing and path.exists():
        with path.open(encoding="utf-8", newline="") as existing_handle:
            for old_row in csv.DictReader(existing_handle):
                current.setdefault((old_row["station_code"], old_row["month"]), old_row)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".manifest_",
                                     suffix=".csv", encoding="utf-8", newline="",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(current.values(), key=lambda row: (row["station_code"], row["month"])))
    os.replace(temporary, path)


SUMMARY_FIELDS = (
    "station_code", "station_name", "owner", "variable", "height_m", "frequency",
    "first_timestamp", "last_timestamp", "valid_values", "first_field", "last_field",
)


def read_summaries(path: Path) -> dict[tuple[str, str, str], dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            (row["station_code"], row["variable"], row["height_m"]): row
            for row in csv.DictReader(handle)
        }


def merge_summary(current: dict[tuple[str, str, str], dict], row: dict) -> None:
    row = {key: value for key, value in row.items() if key in SUMMARY_FIELDS}
    key = (row["station_code"], row["variable"], row["height_m"])
    previous = current.get(key)
    if previous is None:
        current[key] = row
        return
    if row["first_timestamp"] < previous["first_timestamp"]:
        previous.update(
            first_timestamp=row["first_timestamp"], first_field=row["first_field"],
        )
    if row["last_timestamp"] > previous["last_timestamp"]:
        previous.update(
            last_timestamp=row["last_timestamp"], last_field=row["last_field"],
        )
    previous["valid_values"] = int(previous.get("valid_values") or 0) + int(row["valid_values"])


def write_summaries(path: Path, rows: dict[tuple[str, str, str], dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=".summary_", suffix=".csv",
        encoding="utf-8", newline="", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows[key] for key in sorted(rows))
    os.replace(temporary, path)


def read_monthly_summaries(path: Path) -> dict[tuple[str, str, str, str], dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            (row["station_code"], row["evidence_month"], row["variable"], row["height_m"]): row
            for row in csv.DictReader(handle)
        }


def write_monthly_summaries(
    path: Path, rows: dict[tuple[str, str, str, str], dict],
) -> None:
    fields = ("evidence_month",) + SUMMARY_FIELDS
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=".monthly_summary_", suffix=".csv",
        encoding="utf-8", newline="", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows[key] for key in sorted(rows))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", type=Path, action="append",
        help="Catastro SACLIM; se puede repetir. Por defecto combina norte y zona central.",
    )
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--start", default="2010-01")
    parser.add_argument("--end", default="2025-12")
    parser.add_argument("--email", required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--station", action="append", help="Restrict to one or more station codes")
    parser.add_argument(
        "--from-creation", action="store_true",
        help="Inspect every month from max(1950-01, station creation) through --end",
    )
    parser.add_argument(
        "--current-only", action="store_true",
        help="Inspect only the recent API response used to determine whether each series is current",
    )
    parser.add_argument(
        "--dmc-only", action="store_true",
        help="Exclude the two ESO-owned stations historically distributed by the DMC portal",
    )
    parser.add_argument(
        "--all-owners", action="store_true",
        help="Include every station published in the supplied SACLIM catalog",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    catalogs = args.catalog or list(DEFAULT_CATALOGS)
    by_code: dict[str, Station] = {}
    for catalog in catalogs:
        for station in stations_from_catalog(
            catalog, include_eso=not args.dmc_only, all_owners=args.all_owners,
        ):
            by_code[station.code] = station
    stations = [by_code[code] for code in sorted(by_code)]
    if args.station:
        stations = [station for station in stations if station.code in args.station]
    if args.current_only:
        all_tasks = [(station, "current") for station in stations]
        month_description = "1"
    elif args.from_creation:
        end = datetime.strptime(args.end, "%Y-%m")
        all_tasks = []
        for station in stations:
            start = max(datetime(1950, 1, 1), station.created)
            all_tasks.extend((station, month) for month in months_between(
                start.strftime("%Y-%m"), end.strftime("%Y-%m"),
            ))
        month_description = "variable desde creación/1950"
    else:
        months = months_between(args.start, args.end)
        all_tasks = [(station, month) for station in stations for month in months]
        month_description = str(len(months))
    print(
        f"stations={len(stations)} months={month_description} requests={len(all_tasks)}",
        flush=True,
    )
    if args.dry_run:
        print(" ".join(f"{s.code}:{s.owner}" for s in stations), flush=True)
        return 0
    # Evita que espacios o saltos de línea copiados junto con la clave hagan
    # que DMC la rechace. La credencial sigue sin guardarse ni imprimirse.
    token = getpass.getpass("DMC API key: ").strip()
    if not token:
        raise ValueError("DMC API key must not be empty")
    manifest = args.output / "manifest_api_15min.csv"
    summary_path = args.output / "resumen_series_api_15min.csv"
    monthly_summary_path = args.output / "resumen_mensual_api_15min.csv"
    manifest_rows = read_manifest(manifest)
    summary_rows = read_summaries(summary_path)
    monthly_summary_rows = read_monthly_summaries(monthly_summary_path)
    skipped: list[dict] = []
    tasks: list[tuple[Station, str]] = []
    for station, month in all_tasks:
        previous = manifest_rows.get((station.code, month))
        if month != "current" and previous and previous.get("status") in {
            "no_data", "verified", "summarized_existing",
        }:
            skipped.append(previous)
            continue
        tasks.append((station, month))
    counts: Counter[str] = Counter(row["status"] for row in skipped)
    completed = len(skipped)
    total = len(all_tasks)
    print(
        f"resume_skipped={completed} pending={len(tasks)} {dict(counts)}",
        flush=True,
    )
    since_checkpoint = 0

    def save() -> None:
        write_summaries(summary_path, summary_rows)
        write_monthly_summaries(monthly_summary_path, monthly_summary_rows)
        write_manifest(manifest, list(manifest_rows.values()), merge_existing=False)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        iterator = iter(tasks)
        pending = {}

        def fill() -> None:
            while len(pending) < max(1, args.workers * 2):
                try:
                    task = next(iterator)
                except StopIteration:
                    break
                future = pool.submit(
                    fetch, task, args.output, args.email, token,
                    args.timeout, args.pause,
                )
                pending[future] = task

        fill()
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                pending.pop(future)
                row, summaries = future.result()
                for summary in summaries:
                    merge_summary(summary_rows, summary)
                    monthly_key = (
                        summary["station_code"], summary["evidence_month"],
                        summary["variable"], summary["height_m"],
                    )
                    monthly_summary_rows[monthly_key] = summary
                manifest_rows[(row["station_code"], row["month"])] = row
                counts[row["status"]] += 1
                completed += 1
                since_checkpoint += 1
            fill()
            if since_checkpoint >= 250 or completed == total:
                save()
                print(f"{completed}/{total} {dict(counts)}", flush=True)
                since_checkpoint = 0
    if since_checkpoint:
        save()
    return 1 if any(counts[status] for status in (
        "auth_error", "access_blocked", "http_error", "non_json", "invalid_response", "network_error",
        "credential_in_response",
    )) else 0


if __name__ == "__main__":
    raise SystemExit(main())
