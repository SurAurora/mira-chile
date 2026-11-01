#!/usr/bin/env python3
"""Verifica la primera observación y disponibilidad reciente en VISMET.

Respeta el máximo oficial de 168 horas. Recorre semanas consecutivas desde
2000 (inicio descrito por CR2) y comprueba cada primera detección contra una
serie horaria. No usa una búsqueda binaria que presuponga continuidad.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import math
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import geopandas as gpd
import requests
from shapely.geometry import Point


ROOT = Path(__file__).resolve().parents[1]
BASE = "https://vismet.cr2.cl/"
RAW = ROOT / "datos_originales" / "vismet"
VARIABLES = {1: "Precipitación", 2: "Temperatura"}
ORGANIZATIONS = {
    "dga": "DGA", "dmc": "DMC", "agromet": "AGROMET",
    "ceaza": "CEAZA", "redmeteo": "REDMETEO", "ufro": "UFRO",
    "estaciones_andinas_cr2": "CR2",
}
REGIONS = {15: "Arica y Parinacota", 1: "Tarapacá", 2: "Antofagasta", 3: "Atacama"}
_local = threading.local()


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def public_client_checksum(path: str) -> str:
    """Replica hashUrl del cliente público; no es una clave de usuario."""
    result = 0
    for char in path:
        result = (31 * result + ord(char)) & 0xFFFFFFFF
    return format(result, "x")


class Client:
    def __init__(self, raw: Path, pause: float):
        self.raw = raw
        self.cache = raw / "respuestas_gz"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.pause = pause
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.requests = 0

    def get(self, path: str):
        digest = hashlib.sha256(path.encode()).hexdigest()
        target = self.cache / (digest + ".json.gz")
        if target.exists():
            with gzip.open(target, "rt", encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached["path"] != path:
                raise ValueError("Caché incompatible")
            return cached["data"]
        if not hasattr(_local, "session"):
            _local.session = requests.Session()
            _local.session.headers.update({"Referer": BASE, "User-Agent": "MIRA-Chile catalog research"})
        for attempt in range(4):
            with self.lock:
                wait = max(0.0, self.next_request - time.monotonic())
                if wait:
                    time.sleep(wait)
                self.next_request = time.monotonic() + self.pause
            try:
                response = _local.session.get(
                    BASE + path.lstrip("/"),
                    headers={"ckey": public_client_checksum(path)}, timeout=(30, 900),
                )
                response.raise_for_status()
                data = response.json()
                self.requests += 1
                temp = target.with_suffix(target.suffix + ".part")
                with gzip.open(temp, "wt", encoding="utf-8") as handle:
                    json.dump({"path": path, "consultado_utc": datetime.now(timezone.utc).isoformat(), "data": data}, handle, ensure_ascii=False)
                temp.replace(target)
                return data
            except (requests.RequestException, ValueError):
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError("inalcanzable")


def map_path(variable: int, end: int, hours: int = 168) -> str:
    statistic = "sum" if variable == 1 else "mean"
    return f"api/measure/by-measure-type/{variable}/by-timestamp/{end}/by-interval/{hours}/{statistic}"


def station_path(station: int, variable: int, end: int, hours: int = 168) -> str:
    return f"api/measure/by-station/{station}/by-measure-type/{variable}/by-timestamp/{end}/by-interval/{hours}"


def valid_observation(row: dict, variable: int) -> bool:
    value = row.get("value")
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if not isinstance(row.get("timestamp"), (int, float)):
        return False
    if (row.get("confidence") or 0) <= 0 or (row.get("lastUsedRawTimestamp") or 0) <= 0:
        return False
    return 0 <= value <= 500 if variable == 1 else -80 <= value <= 60


def find_region(station: dict, regions, coastal_regions=None) -> int | None:
    lat, lon = station.get("latitude"), station.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    point = Point(lon, lat)
    for code, geometry in regions:
        if geometry.covers(point):
            return int(code)
    # Estaciones marítimas a pocos metros de una costa generalizada. No se
    # aplica el criterio de proximidad a estaciones terrestres fronterizas.
    name = station.get("name", "").upper()
    if coastal_regions is not None and ("SERVIMET" in name or "CAPITAN" in name):
        metric_point = gpd.GeoSeries([point], crs=4326).to_crs(32719).iloc[0]
        distance = coastal_regions.geometry.distance(metric_point)
        closest = distance.idxmin()
        if distance.loc[closest] <= 20_000:
            return int(coastal_regions.loc[closest, "codregion"])
    return None


def cached_station_inventory(client: Client, latest_maps: dict) -> dict:
    """Incluye estaciones retiradas del inventario de la consulta actual."""
    candidates = {
        str(item["station"]["id"]): item["station"]
        for rows in latest_maps.values() for item in rows
    }
    current_ids = set(candidates)
    newest = {}
    for path in sorted(client.cache.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if "api/measure/by-measure-type/" not in payload["path"]:
            continue
        match = re.search(r"/by-timestamp/(\d+)/", payload["path"])
        end = int(match.group(1)) if match else 0
        for item in payload["data"]:
            station = item["station"]
            sid = str(station["id"])
            if sid in current_ids or end <= newest.get(sid, -1):
                continue
            candidates[sid] = station
            newest[sid] = end
    return candidates


def first_in_window(client: Client, station: dict, variable: int, end: int, minimum: int) -> dict | None:
    path = station_path(station["id"], variable, end)
    rows = client.get(path)
    valid = sorted(
        (row for row in rows if valid_observation(row, variable) and minimum <= row["timestamp"] <= end),
        key=lambda row: row["timestamp"],
    )
    if not valid:
        return None
    first = valid[0]
    return {
        "id_vismet": station["id"], "variable_id": variable,
        "variable": VARIABLES[variable],
        "primera_fecha_disponible": iso(first["timestamp"]),
        "timestamp": first["timestamp"], "valor": first["value"],
        "confianza": first["confidence"],
        "lastUsedRawTimestamp": first["lastUsedRawTimestamp"],
        "n_valores_validos_primera_ventana": len(valid),
        "ruta_serie_primera": path,
        "frecuencia_publicada_minutos": 60,
        "intervalos_comprobados_minutos": sorted({
            int((b["timestamp"] - a["timestamp"]) // 60) for a, b in zip(valid, valid[1:])
        }),
    }


def main(resume_internal: bool = False) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=datetime.now(ZoneInfo("America/Santiago")).date().isoformat())
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--pause", type=float, default=0.3)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-windows", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.raw.mkdir(parents=True, exist_ok=True)
    client = Client(args.raw, args.pause)
    as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=ZoneInfo("America/Santiago"))
    current_start = int((as_of - timedelta(days=7)).timestamp())
    current_end = int(as_of.timestamp())
    history_start = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp())
    history_stop = current_end - 3600
    state_path = args.raw / "estado_sondeo.json"
    state = {"start": args.start, "as_of": args.as_of, "primeras": {}, "ultima_ventana": {}, "completo": False}
    if (args.resume or resume_internal) and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["start"] != args.start or state["as_of"] != args.as_of:
            raise ValueError("El sondeo reanudado debe conservar --start y --as-of")

    # Inventario oficial del mismo portal. No se transfieren alturas desde otras redes.
    frame = gpd.read_file("/home/alagos/fondef_polvo/data/geospatial/Regiones/Regional.shp")
    frame = frame[frame["codregion"].isin(REGIONS)].to_crs(4326)
    regions = [(row.codregion, row.geometry) for row in frame.itertuples()]
    coastal_regions = frame.to_crs(32719)
    config = client.get("/api/configuration/")
    dump(args.raw / "configuracion_publica.json", config)
    exclusion_text = next((item["value"] for item in config if item["key"] == "exclude_stations"), "")
    exclusions = set(exclusion_text.split(","))  # mismo comportamiento del cliente web
    latest_maps = {v: client.get(map_path(v, history_stop)) for v in VARIABLES}
    current_ids = {str(item["station"]["id"]) for rows in latest_maps.values() for item in rows}
    inventory = cached_station_inventory(client, latest_maps)
    dump(args.raw / "inventario_actual_e_historico.json", list(inventory.values()))
    stations = {}
    excluded = []
    unlocated = []
    for sid, station in inventory.items():
        code = find_region(station, regions, coastal_regions)
        if station["nationalCode"] in exclusions:
            excluded.append({**station, "motivo": "excluida por configuración pública VISMET"})
            continue
        if code is not None:
            org = station["organization"]["name"]
            stations[sid] = {
                **station, "codigo_region": code, "region": REGIONS[code],
                "fuente_institucion": ORGANIZATIONS.get(org, org),
                "criterio_geografico": "polígono" if find_region(station, regions) is not None else "proximidad costera de estación marítima",
                "en_inventario_actual": sid in current_ids,
            }
        elif not isinstance(station.get("latitude"), (int, float)) or not isinstance(station.get("longitude"), (int, float)):
            unlocated.append(station)
        elif -29.6 <= station["latitude"] <= -17.3:
            excluded.append({**station, "motivo": "coordenadas fuera de polígonos regionales seleccionados"})
    dump(args.raw / "estaciones_objetivo.json", list(stations.values()))
    dump(args.raw / "estaciones_excluidas.json", excluded)
    dump(args.raw / "estaciones_sin_coordenadas.json", unlocated)
    previous_ids = {key.split(":")[0] for key in state.get("actuales", {})}
    added_ids = set(stations) - previous_ids
    if previous_ids and added_ids:
        # Las semanas previas están en caché: reexaminar nuevos puntos sin
        # volver a descargar las respuestas ya consultadas.
        state["ultima_ventana"] = {}
        state["completo"] = False
        print(f"Nuevas estaciones en alcance: {sorted(added_ids)}; relectura de caché histórica", flush=True)
    print(f"estaciones={len(stations)} instituciones={dict(Counter(s['fuente_institucion'] for s in stations.values()))}", flush=True)

    # Comprobar que hay valores en la ventana reciente con la serie individual.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {}
        for variable in VARIABLES:
            for station in stations.values():
                key = f"{station['id']}:{variable}"
                if "actuales" in state and key in state["actuales"]:
                    continue
                tasks[pool.submit(first_in_window, client, station, variable, history_stop, current_start)] = key
        state.setdefault("actuales", {})
        for number, future in enumerate(concurrent.futures.as_completed(tasks), 1):
            key = tasks[future]
            result = future.result()
            state["actuales"][key] = {"estado": "Sí" if result else "No", "evidencia": result}
            if number % 50 == 0:
                dump(state_path, state)
                print(f"actuales={number}/{len(tasks)}", flush=True)
        dump(state_path, state)
    print(f"actuales_verificadas={len(state['actuales'])} con_datos={sum(x['estado']=='Sí' for x in state['actuales'].values())}", flush=True)

    # Inicio declarado por CR2: 2000. Se conserva una consulta de borde anterior.
    for variable in VARIABLES:
        before = client.get(map_path(variable, history_start - 3600))
        if any(
            str(row["station"]["id"]) in stations and (row.get("confidence") or 0) > 0
            for row in before
        ):
            raise ValueError("Hay datos anteriores al inicio elegido; ampliar --start")

    # Dos series por estación, pero no se presupone que ambas existan.
    # Mapas semanales consecutivos con una hora de solapamiento.
    windows = []
    first_end = history_start + 167 * 3600
    end = first_end
    while end < history_stop:
        windows.append(end)
        end += 167 * 3600
    if not windows or windows[-1] != history_stop:
        windows.append(history_stop)
    count = 0
    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        # Se mantiene el orden temporal para que la primera detección sea auditable.
        for end in windows:
            pending = [v for v in VARIABLES if end > state["ultima_ventana"].get(str(v), 0)]
            if not pending:
                continue
            futures = {v: pool.submit(client.get, map_path(v, end)) for v in pending}
            for variable in pending:
                rows = futures[variable].result()
                verify = []
                for item in rows:
                    sid = str(item["station"]["id"])
                    key = f"{sid}:{variable}"
                    if sid not in stations or key in state["primeras"] or (item.get("confidence") or 0) <= 0:
                        continue
                    verify.append((key, pool.submit(first_in_window, client, stations[sid], variable, end, history_start)))
                for key, future in verify:
                    result = future.result()
                    if result is not None:
                        state["primeras"][key] = result
                state["ultima_ventana"][str(variable)] = end
            dump(state_path, state)
            count += 1
            if count % 25 == 0:
                print(f"semanas={count}/{len(windows)} fecha={iso(end)} primeras={len(state['primeras'])} solicitudes_nuevas={client.requests} segundos={time.monotonic()-t0:.0f}", flush=True)
            if args.max_windows and count >= args.max_windows:
                print("límite de prueba; avance guardado", flush=True)
                return 0
    # Una primera ejecución descubre el inventario histórico en el recorrido.
    # Si éste añade estaciones del área, se verifican en una segunda pasada
    # usando la caché, sin repetir las consultas de mapas ya completadas.
    expanded = cached_station_inventory(client, latest_maps)
    missing = [
        sid for sid, station in expanded.items()
        if sid not in stations and station["nationalCode"] not in exclusions
        and find_region(station, regions, coastal_regions) is not None
    ]
    if missing:
        state["completo"] = False
        dump(state_path, state)
        print(f"Estaciones históricas adicionales del área: {len(missing)}. Verificando desde caché.", flush=True)
        return main(resume_internal=True)
    state["completo"] = True
    state["consultado_utc"] = datetime.now(timezone.utc).isoformat()
    state["ventana_actual_desde"] = iso(current_start)
    state["ventana_actual_hasta_exclusiva"] = iso(current_end)
    state["semanas_totales"] = len(windows)
    dump(state_path, state)
    print(f"COMPLETO primeras={len(state['primeras'])} estaciones={len(stations)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
