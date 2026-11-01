#!/usr/bin/env python3
"""Verifica las estaciones ESO publicadas por DMC sin requerir credenciales.

Conserva los resultados separados de los sondeos DMC anteriores. Busca los
primeros ZIP públicos desde la creación SACLIM hasta el primer archivo local
conocido y revisa los siete días completos anteriores a --as-of en ZIP mensuales.
"""

from __future__ import annotations

import argparse
import csv
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import requests

from dmc_sondear_ema_minuto_publico import BASE, DEFINITIONS, RAW, ROOT, inspect, months, number, probe, write


def check_current(station, group, start, end, archive_dir, session, pause, timeout):
    code = station["codigo_nacional"]
    found = {}
    present = set()
    successful = []
    attempts = []
    for month in months(datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())):
        filename = f"{code}_{month}_{group}.csv.zip"
        path = archive_dir / code / filename
        url = f"{BASE}/{code}/{filename}"
        state = "sin_zip"
        try:
            if not path.exists():
                time.sleep(pause)
                response = session.get(url, timeout=timeout)
                response.raise_for_status()
                if response.content.startswith(b"PK\x03\x04") and inspect(response.content, group) is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(response.content)
            if path.exists():
                content = path.read_bytes()
                inspected = inspect(content, group)
                if inspected is None:
                    raise ValueError(f"ZIP no válido: {path}")
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    member = next(name for name in archive.namelist() if name.endswith(".csv"))
                    rows = list(csv.DictReader(io.StringIO(archive.read(member).decode("utf-8-sig")), delimiter=";"))
                frequency = next((row["frecuencia_muestreo"] for row in inspected[0]), "No determinado")
                if frequency != "1 min":
                    raise ValueError(f"Frecuencia no minutaria en {path}: {frequency}")
                if any(str(row.get("codigoNacional")) != code for row in rows):
                    raise ValueError(f"Código de estación incompatible en {path}")
                state = "zip_verificado"
                successful.append(str(path.relative_to(ROOT)))
                for row in rows:
                    stamp = datetime.fromisoformat(row["momento"])
                    if not start <= stamp.date() <= end:
                        continue
                    for field, (variable, height, low, high) in DEFINITIONS[group].items():
                        key = (variable, height)
                        if field in row:
                            present.add(key)
                        value = number(row.get(field))
                        if value is not None and low <= value <= high:
                            previous = found.get(key)
                            if previous is None or stamp < previous[0]:
                                found[key] = (stamp, value, field)
        except (requests.RequestException, OSError, ValueError) as error:
            state = f"error_{type(error).__name__}"
        attempts.append({"codigo_nacional": code, "grupo_ema": group, "mes": month, "estado": state, "url": url})
    out = []
    for variable, height in sorted({spec[:2] for spec in DEFINITIONS[group].values()}):
        key = (variable, height)
        value = found.get(key)
        # La ausencia de una columna o un recurso no demuestra falta de datos.
        state = "Sí" if value else ("No" if key in present and all(row["estado"] == "zip_verificado" for row in attempts) else "No determinado")
        out.append({
            "codigo_nacional": code, "variable": variable, "altura_sensor_suelo_m": height,
            "frecuencia_muestreo": "1 min", "datos_disponibles_actualmente": state,
            "ventana_desde": start.isoformat(), "ventana_hasta": end.isoformat(),
            "primer_instante_ventana": value[0].isoformat() + "+00:00" if value else "",
            "primer_valor_ventana": value[1] if value else "", "campo_original": value[2] if value else "",
            "archivo_evidencia": ";".join(successful),
        })
    return out, attempts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--pause", type=float, default=0.3)
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    with (RAW / "inventario_saclim_regiones.csv").open(encoding="utf-8", newline="") as handle:
        stations = [row for row in csv.DictReader(handle) if row["propietario"] == "ESO"]
    with (RAW / "sondeo_ema_minuto_existente.csv").open(encoding="utf-8", newline="") as handle:
        old = list(csv.DictReader(handle))
    first, attempts = [], []
    tasks = []
    session = requests.Session()
    session.headers["User-Agent"] = "MIRA-Chile-auditoria-SACLIM/1.0"
    audit_dir = RAW / f"revision_eso_{args.as_of.isoformat()}"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for station in stations:
        code = station["codigo_nacional"]
        response = session.get(f"https://climatologia.meteochile.gob.cl/application/informacion/fichaDeEstacion/{code}", timeout=args.timeout)
        response.raise_for_status()
        (audit_dir / f"ficha_{code}.html").write_bytes(response.content)
        earliest = min(row["primera_fecha_verificada"] for row in old if row["codigo_nacional"] == code)
        tasks.extend(((station, group), datetime.fromisoformat(earliest)) for group in DEFINITIONS)
    initial_cache = audit_dir / "sondeo_inicial_series.csv"
    attempts_cache = audit_dir / "sondeo_inicial_intentos.csv"
    if initial_cache.exists() and attempts_cache.exists():
        with initial_cache.open(encoding="utf-8", newline="") as handle:
            first = list(csv.DictReader(handle))
        with attempts_cache.open(encoding="utf-8", newline="") as handle:
            attempts = list(csv.DictReader(handle))
    if not first or any(row["estado"].startswith("error_") for row in attempts):
        first, attempts = [], []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(probe, task, end, args.pause, args.timeout) for task, end in tasks]
            for future in as_completed(futures):
                values, tried = future.result()
                first.extend(values)
                attempts.extend(tried)
                print(f"histórico: {len(first)} series, {len(attempts)} solicitudes", flush=True)
    else:
        print(f"histórico: {len(first)} series recuperadas de la caché", flush=True)
    fields = ("codigo_nacional", "nombre_estacion", "variable", "altura_sensor_suelo_m", "frecuencia_muestreo", "primera_fecha_verificada", "primer_valor_verificado", "campo_original", "grupo_ema", "url_evidencia", "archivo_evidencia", "miembro_csv")
    write(RAW / "sondeo_ema_minuto_publico_eso.csv", fields, first)
    write(initial_cache, fields, first)
    write(attempts_cache, ("codigo_nacional", "grupo_ema", "mes", "estado", "http_status", "bytes", "url"), attempts)
    end = args.as_of - timedelta(days=1)
    start = end - timedelta(days=6)
    current, attempts = [], []
    for station in stations:
        for group in DEFINITIONS:
            values, tried = check_current(station, group, start, end, audit_dir / "actual", session, args.pause, args.timeout)
            current.extend(values)
            attempts.extend(tried)
            print(f"actual: {station['codigo_nacional']} {group}: {len(values)} series revisadas", flush=True)
    write(RAW / "disponibilidad_actual_eso_minuto.csv", tuple(current[0]), current)
    write(audit_dir / "actual_intentos.csv", ("codigo_nacional", "grupo_ema", "mes", "estado", "url"), attempts)
    print(f"ventana UTC: {start}/{end}; estaciones: {len(stations)}; series minutarias con datos actuales: {sum(row['datos_disponibles_actualmente'] == 'Sí' for row in current)}")


if __name__ == "__main__":
    main()
