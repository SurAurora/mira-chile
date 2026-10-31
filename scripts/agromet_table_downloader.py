#!/usr/bin/env python3
"""Consulta reproducible de la vista pública de tabla de AGROMET.

AGROMET exige una verificación visual para exportar CSV o Excel. Este programa no
intenta resolverla: solicita la vista HTML ``tabla``, conserva la respuesta
original comprimida y genera un CSV derivado para facilitar su inspección.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import io
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path


BASE_URL = "https://agrometeorologia.cl/"
USER_AGENT = "Mozilla/5.0 (compatible; CatalogoEstaciones/1.0; consulta-investigacion)"
REGIONS = (
    "Arica y Parinacota",
    "Tarapacá",
    "Antofagasta",
    "Atacama",
)
VALID_INTERVALS = ("hour", "day")


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    region: str


def request(url: str, data: dict[str, object] | None = None, retries: int = 4) -> bytes:
    encoded = None
    if data is not None:
        pairs: list[tuple[str, str]] = []
        for key, value in data.items():
            values = value if isinstance(value, list) else [value]
            pairs.extend((key, str(item)) for item in values)
        encoded = urllib.parse.urlencode(pairs).encode("utf-8")

    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            data=encoded,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"No se pudo acceder a {url}: {exc}") from exc
            time.sleep((2**attempt) + random.random())
    raise AssertionError("unreachable")


def extract_select(page: str, select_id: str) -> str:
    match = re.search(
        rf'<select\b[^>]*\bid=["\']{re.escape(select_id)}["\'][^>]*>(.*?)</select>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise RuntimeError(f"No se encontró el selector {select_id!r}; el portal pudo cambiar")
    return match.group(1)


def catalog() -> tuple[list[Station], dict[str, str]]:
    page = request(BASE_URL).decode("utf-8", errors="replace")
    station_html = extract_select(page, "estaciones")
    stations: list[Station] = []
    for group in re.finditer(
        r'<optgroup\b[^>]*\blabel=["\']([^"\']+)["\'][^>]*>(.*?)</optgroup>',
        station_html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        region = html.unescape(group.group(1)).strip()
        for option in re.finditer(
            r'<option\b[^>]*\bvalue=["\']([^"\']+)["\'][^>]*>(.*?)</option>',
            group.group(2),
            flags=re.IGNORECASE | re.DOTALL,
        ):
            name = re.sub(r"\s+", " ", html.unescape(option.group(2))).strip()
            stations.append(Station(option.group(1), name, region))

    variables: dict[str, str] = {}
    for option in re.finditer(
        r'<option\b[^>]*\bvalue=["\']([^"\']+)["\'][^>]*>(.*?)</option>',
        extract_select(page, "variables"),
        flags=re.IGNORECASE | re.DOTALL,
    ):
        variables[option.group(1)] = re.sub(
            r"\s+", " ", html.unescape(option.group(2))
        ).strip()
    return stations, variables


class ObservationTableParser(HTMLParser):
    """Extrae las filas ``tbody`` de la tabla de resultados."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_target_table = False
        self.table_depth = 0
        self.in_tbody = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.cell_colspan = 1
        self.row: list[str] | None = None
        self.rows: list[list[str]] = []
        self.timezone = "No informado"

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = self._attrs(attrs)
        if tag == "table":
            if self.in_target_table:
                self.table_depth += 1
            elif "table-bordered" in values.get("class", ""):
                self.in_target_table = True
                self.table_depth = 1
            return
        if not self.in_target_table:
            return
        if tag == "tbody":
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.in_cell = True
            self.cell_parts = []
            try:
                self.cell_colspan = max(1, int(values.get("colspan", "1").strip()))
            except ValueError:
                self.cell_colspan = 1

    def handle_endtag(self, tag: str) -> None:
        if not self.in_target_table:
            return
        if tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_target_table = False
                self.in_tbody = False
        elif tag in ("td", "th") and self.in_cell and self.row is not None:
            value = re.sub(r"\s+", " ", " ".join(self.cell_parts)).strip()
            self.row.extend([value] * self.cell_colspan)
            self.in_cell = False
            self.cell_parts = []
            self.cell_colspan = 1
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None
        elif tag == "tbody":
            self.in_tbody = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            value = data.strip()
            if value:
                self.cell_parts.append(value)


def parse_table(
    content: bytes,
    variables: list[str],
    interval: str,
    invalid_timestamps: list[str] | None = None,
) -> tuple[list[list[str]], str]:
    page = content.decode("utf-8", errors="replace")
    parser = ObservationTableParser()
    parser.feed(page)

    timezone_match = re.search(r"Tiempo\s*<sup>\s*([^<]+)\s*</sup>", page, re.I)
    timezone = html.unescape(timezone_match.group(1)).strip() if timezone_match else "No informado"
    if not parser.rows:
        return [], timezone

    expected_columns = 1 + 2 * len(variables)
    rows: list[list[str]] = []
    stamp_format = "%d-%m-%Y %H:%M" if interval == "hour" else "%d-%m-%Y"
    for row in parser.rows:
        if len(row) != expected_columns:
            raise RuntimeError(
                f"Fila con {len(row)} columnas; se esperaban {expected_columns}. "
                "El formato del portal pudo cambiar"
            )
        try:
            stamp = datetime.strptime(row[0], stamp_format)
        except ValueError as exc:
            if invalid_timestamps is not None:
                invalid_timestamps.append(row[0])
                continue
            raise RuntimeError(f"Marca temporal inesperada: {row[0]!r}") from exc
        row[0] = stamp.isoformat(timespec="minutes")
        rows.append(row)
    return rows, timezone


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use fechas AAAA-MM-DD") from exc


def chunks(start: date, end: date, days: int):
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def write_snapshot(output_dir: Path, stations: list[Station], variables: dict[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    station_path = output_dir / "estaciones_agromet.csv"
    with station_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("id", "region", "nombre_publicado"))
        writer.writerows((s.id, s.region, s.name) for s in stations if s.region in REGIONS)

    variable_path = output_dir / "variables_agromet.csv"
    with variable_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("codigo", "nombre_publicado"))
        writer.writerows(variables.items())

    metadata = {
        "fuente": BASE_URL,
        "consultado_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "regiones": list(REGIONS),
        "cantidad_estaciones": sum(s.region in REGIONS for s in stations),
        "sha256_estaciones": hashlib.sha256(station_path.read_bytes()).hexdigest(),
        "sha256_variables": hashlib.sha256(variable_path.read_bytes()).hexdigest(),
    }
    (output_dir / "snapshot.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def download_table(
    stations: list[Station],
    variables: list[str],
    interval: str,
    start: date,
    end: date,
    output_dir: Path,
    overwrite: bool,
) -> dict[str, str]:
    if len(stations) > 1 and len(variables) > 1:
        raise ValueError("AGROMET sólo permite una variable al consultar varias estaciones")
    station_label = stations[0].id if len(stations) == 1 else "GRUPO-" + "-".join(s.id for s in stations)
    stem = safe_name(
        f"{station_label}_{interval}_{start.isoformat()}_{end.isoformat()}_{'-'.join(variables)}"
    )
    raw_path = output_dir / "html_gz" / f"{stem}.html.gz"
    csv_path = output_dir / "csv" / f"{stem}.csv"
    if raw_path.exists() and csv_path.exists() and not overwrite:
        return {
            "estado": "omitido",
            "filas": "No recalculado",
            "huso_horario": "No recalculado",
            "html_gz": str(raw_path.relative_to(output_dir)),
            "csv": str(csv_path.relative_to(output_dir)),
            "sha256_html": hashlib.sha256(gzip.decompress(raw_path.read_bytes())).hexdigest(),
        }

    payload = {
        "estaciones[]": [station.id for station in stations],
        "variables[]": variables,
        "intervalo": interval,
        "desde": start.strftime("%d-%m-%Y"),
        "hasta": end.strftime("%d-%m-%Y"),
        "vista[]": "tabla",
    }
    content = request(BASE_URL, payload)
    series = (
        variables
        if len(stations) == 1
        else [f"{station.id}__{variables[0]}" for station in stations]
    )
    rows, timezone = parse_table(content, series, interval)

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(raw_path, "wb", compresslevel=6) as handle:
        handle.write(content)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["fecha_hora"]
            + series
            + [f"porcentaje_datos_{item}" for item in series]
        )
        writer.writerows(rows)

    return {
        "estado": "descargado" if rows else "sin_datos",
        "filas": str(len(rows)),
        "huso_horario": timezone,
        "html_gz": str(raw_path.relative_to(output_dir)),
        "csv": str(csv_path.relative_to(output_dir)),
        "sha256_html": hashlib.sha256(content).hexdigest(),
    }


def append_manifest(path: Path, record: dict[str, str]) -> None:
    fields = (
        "consultado_utc",
        "estacion_id",
        "estacion_nombre",
        "region",
        "variables",
        "intervalo",
        "desde",
        "hasta",
        "estado",
        "filas",
        "huso_horario",
        "html_gz",
        "csv",
        "sha256_html",
        "error",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: record.get(field, "") for field in fields})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", choices=("stations", "variables"))
    parser.add_argument("--snapshot", type=Path, help="Guarda el catálogo vigente de las cuatro regiones")
    parser.add_argument("--region", action="append", default=[], help="Región exacta; repetible")
    parser.add_argument("--station", action="append", default=[], help="ID de estación; repetible")
    parser.add_argument("--variables", nargs="+", help="Códigos de variables, máximo 6")
    parser.add_argument("--interval", choices=VALID_INTERVALS, default="hour")
    parser.add_argument("--from", dest="start", type=parse_date)
    parser.add_argument("--to", dest="end", type=parse_date)
    parser.add_argument("--chunk-days", type=int, default=31)
    parser.add_argument("--output", type=Path, default=Path("datos_originales/agromet"))
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument(
        "--stations-per-query",
        type=int,
        default=1,
        help="Agrupa estaciones; con un valor mayor que 1 se permite una sola variable",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    stations, available_variables = catalog()

    if args.list == "stations":
        writer = csv.writer(sys.stdout)
        writer.writerow(("id", "region", "nombre"))
        writer.writerows((s.id, s.region, s.name) for s in stations if s.region in REGIONS)
        return 0
    if args.list == "variables":
        print(json.dumps(available_variables, ensure_ascii=False, indent=2))
        return 0
    if args.snapshot:
        write_snapshot(args.snapshot, stations, available_variables)
        print(f"Snapshot guardado en {args.snapshot.resolve()}")
        if not args.variables:
            return 0

    missing = [name for name in ("variables", "start", "end") if getattr(args, name) is None]
    if missing:
        raise SystemExit("Faltan --variables, --from y --to")
    if args.start > args.end:
        raise SystemExit("--from debe ser anterior o igual a --to")
    if args.chunk_days < 1:
        raise SystemExit("--chunk-days debe ser al menos 1")
    if args.stations_per_query < 1:
        raise SystemExit("--stations-per-query debe ser al menos 1")
    if len(args.variables) > 6:
        raise SystemExit("La interfaz permite como máximo 6 variables por consulta")
    if args.stations_per_query > 1 and len(args.variables) > 1:
        raise SystemExit("AGROMET sólo permite una variable al consultar varias estaciones")
    unknown = sorted(set(args.variables) - set(available_variables))
    if unknown:
        raise SystemExit(f"Variables desconocidas: {', '.join(unknown)}")

    selected = [s for s in stations if s.region in REGIONS]
    if args.region:
        invalid_regions = set(args.region) - set(REGIONS)
        if invalid_regions:
            raise SystemExit(f"Regiones fuera del alcance: {', '.join(sorted(invalid_regions))}")
        selected = [s for s in selected if s.region in set(args.region)]
    if args.station:
        wanted = set(args.station)
        selected = [s for s in selected if s.id in wanted]
        missing_ids = wanted - {s.id for s in selected}
        if missing_ids:
            raise SystemExit(f"Estaciones desconocidas o fuera del alcance: {', '.join(sorted(missing_ids))}")
    if not selected:
        raise SystemExit("Ninguna estación coincide con los filtros")

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / "manifest.csv"
    station_groups = [
        selected[index : index + args.stations_per_query]
        for index in range(0, len(selected), args.stations_per_query)
    ]
    jobs = [
        (station_group, start, end)
        for station_group in station_groups
        for start, end in chunks(args.start, args.end, args.chunk_days)
    ]
    print(
        f"Estaciones: {len(selected)} | grupos: {len(station_groups)} | "
        f"consultas: {len(jobs)} | salida: {args.output.resolve()}"
    )
    failures = 0
    for index, (station_group, start, end) in enumerate(jobs, 1):
        station_label = "|".join(station.id for station in station_group)
        base = {
            "consultado_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "estacion_id": station_label,
            "estacion_nombre": "|".join(station.name for station in station_group),
            "region": "|".join(dict.fromkeys(station.region for station in station_group)),
            "variables": "|".join(args.variables),
            "intervalo": args.interval,
            "desde": start.isoformat(),
            "hasta": end.isoformat(),
        }
        try:
            result = download_table(
                station_group,
                args.variables,
                args.interval,
                start,
                end,
                args.output,
                args.overwrite,
            )
            record = base | result | {"error": ""}
            print(
                f"[{index}/{len(jobs)}] {station_label} {start}..{end}: "
                f"{result['estado']} ({result['filas']} filas)",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            record = base | {
                "estado": "error",
                "filas": "0",
                "huso_horario": "",
                "html_gz": "",
                "csv": "",
                "sha256_html": "",
                "error": str(exc),
            }
            print(f"[{index}/{len(jobs)}] {station_label}: ERROR: {exc}", file=sys.stderr, flush=True)
        append_manifest(manifest, record)
        if index < len(jobs) and args.pause > 0 and record["estado"] != "omitido":
            time.sleep(args.pause)

    if failures:
        print(f"Finalizó con {failures} fallo(s); revise {manifest}", file=sys.stderr)
        return 2
    print("Consultas completadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
