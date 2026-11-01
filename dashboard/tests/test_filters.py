"""Pruebas de interfaz de filtros con Chromium, datos reales y casos de borde."""

from __future__ import annotations

import copy
import csv
import functools
import http.server
import json
import os
import re
import threading
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


DIST = Path(__file__).resolve().parents[1] / "dist"
CATALOG_SCRIPT = (DIST / "data/catalog.js").read_text(encoding="utf-8")
CATALOG = json.loads(CATALOG_SCRIPT.removeprefix("window.CATALOG_DATA = ").strip().removesuffix(";"))


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class FilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(QuietHandler, directory=str(DIST))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.route("https://server.arcgisonline.com/**", lambda route: route.abort())

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], "Errores JavaScript en el navegador")

    def load(self):
        self.page.goto(self.url, wait_until="load")
        expect(self.page.locator("#metricSeries")).not_to_have_text("—")

    def counts(self, station_count, series_count):
        for element, expected in [("metricStations", station_count), ("metricSeries", series_count)]:
            self.page.wait_for_function(
                "([id, expected]) => document.getElementById(id).textContent.replace(/\\D/g, '') === String(expected)",
                arg=[element, expected],
            )

    def choose(self, name, values):
        menu = self.page.locator(f"#{name}Filter")
        if not menu.evaluate("element => element.open"):
            opened = self.page.locator(".multi-select[open] > summary")
            if opened.count():
                opened.click()
            menu.locator("summary").click()
        for value in values:
            menu.get_by_role("checkbox", name=value, exact=True).check()

    def reset(self):
        self.page.locator("#resetFilters").click()
        self.counts(len(CATALOG["stations"]), len(CATALOG["series"]))

    def expected(self, selected, year=None):
        by_station = {row["station_key"]: row for row in CATALOG["stations"]}
        station_fields = {"region": "region", "commune": "comuna", "network": "red", "institution": "fuente_institucion"}
        series_fields = {"variable": "variable", "type": "tipo_variable", "height": "altura_sensor_suelo_m", "frequency": "frecuencia_muestreo", "current": "datos_disponibles_actualmente"}
        result = []
        for row in CATALOG["series"]:
            station = by_station[row["station_key"]]
            if any(str(station[field]) not in selected[name] for name, field in station_fields.items() if name in selected):
                continue
            if any(str(row[field]) not in selected[name] for name, field in series_fields.items() if name in selected):
                continue
            date = str(row["primera_fecha_disponible"] or "")
            if year is not None and (not re.match(r"^\d{4}-\d{2}-\d{2}", date) or int(date[:4]) > year):
                continue
            result.append({**station, **row})
        return result

    def test_single_multiple_and_all_in_every_categorical_filter(self):
        self.load()
        options = {
            "region": ["Antofagasta", "Atacama"],
            "commune": ["Calama", "Copiapó"],
            "network": ["AGROMET", "VISMET / CR2"],
            "institution": ["DGA", "INIA"],
            "variable": ["Temperatura", "Precipitación"],
            "type": ["Meteorológica", "Calidad del aire"],
            "height": ["No informado", "10"],
            "frequency": ["1 min", "60 min"],
            "current": ["Sí", "No"],
        }
        for name, values in options.items():
            with self.subTest(filter=name):
                self.reset()
                self.choose(name, values[:1])
                expected = self.expected({name: values[:1]})
                self.counts(len({row["station_key"] for row in expected}), len(expected))
                self.choose(name, values[1:])
                expected = self.expected({name: values})
                self.counts(len({row["station_key"] for row in expected}), len(expected))
                self.page.locator(f"#{name}Filter .multi-select-all").click()
                self.counts(len(CATALOG["stations"]), len(CATALOG["series"]))
                expect(self.page.locator(f"#{name}Filter .multi-select-all")).to_have_attribute("aria-pressed", "true")

    def test_combined_filters_and_csv_follow_same_selection(self):
        self.load()
        selected = {
            "region": ["Antofagasta", "Atacama"],
            "network": ["DMC / SACLIM", "VISMET / CR2"],
            "variable": ["Temperatura", "Precipitación"],
        }
        for name, values in selected.items():
            self.choose(name, values)
        self.page.locator("#startYearFilter").select_option("2010")
        expected = self.expected(selected, year=2010)
        self.assertTrue(expected)
        self.counts(len({row["station_key"] for row in expected}), len(expected))
        expect(self.page.locator("#stationTable tbody tr[data-key]")).to_have_count(len({row["station_key"] for row in expected}))
        headers = self.page.locator("#coverageMatrix thead th").all_text_contents()
        self.assertEqual(set(headers[1:]), {row["variable"] for row in expected})
        with self.page.expect_download() as download:
            self.page.locator("#exportCsv").click()
        with Path(download.value.path()).open(encoding="utf-8-sig", newline="") as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual(len(exported), len(expected))
        fields = ["id", "red", "variable", "altura_sensor_suelo_m", "frecuencia_muestreo", "primera_fecha_disponible"]
        self.assertEqual(
            sorted(tuple(row[field] for field in fields) for row in exported),
            sorted(tuple(str(row[field]) for field in fields) for row in expected),
        )
        self.reset()
        self.assertEqual(self.page.locator("#startYearFilter").input_value(), "")
        expect(self.page.locator("#filterCount")).to_have_text("0 activos")

    def test_saclim_includes_verified_eso_stations(self):
        self.load()
        self.choose("network", ["DMC / SACLIM"])
        self.page.locator("#networkFilter summary").click()
        for code in ["240005", "230021"]:
            with self.subTest(station=code):
                self.page.locator("#searchFilter").fill(code)
                expect(self.page.locator("#stationTable tbody tr[data-key]")).to_have_count(1)
                self.assertIn("ESO", self.page.locator("#stationTable tbody").inner_text())
                self.page.locator("#stationTable tbody tr[data-key]").click()
                detail = self.page.locator("#detailContent tbody").inner_text()
                self.assertIn("1 min", detail)
                self.assertIn("15 min", detail)
                self.assertIn("2019-01-01", detail)
                self.page.locator("#closeDetail").click()

    def test_year_is_inclusive_per_series_and_excludes_unknown_dates(self):
        fixture = copy.deepcopy(CATALOG)
        station = copy.deepcopy(CATALOG["stations"][0])
        station.update({"station_key": "fixture-a", "nombre_estacion": "Estación de prueba", "red": "AGROMET"})
        rows = []
        for index, (variable, date) in enumerate([
            ("Temperatura", "2000-01-01T00:00:00+00:00"),
            ("Humedad", "2010-12-31T23:00:00+00:00"),
            ("Precipitación", "2011-01-01T00:00:00+00:00"),
            ("Presión", "No informado"),
        ]):
            row = copy.deepcopy(CATALOG["series"][0])
            row.update({"station_key": "fixture-a", "series_key": f"fixture-{index}", "variable": variable, "primera_fecha_disponible": date})
            rows.append(row)
        fixture["stations"] = [station]
        fixture["series"] = rows
        fixture["metadata"]["variables"] = [row["variable"] for row in rows]
        self.page.route("**/data/catalog.js", lambda route: route.fulfill(content_type="application/javascript", body="window.CATALOG_DATA = " + json.dumps(fixture) + ";"))
        self.load()
        self.counts(1, 4)
        self.page.locator("#startYearFilter").select_option("2010")
        self.counts(1, 2)
        self.page.locator("#stationTable tbody tr[data-key]").click()
        expect(self.page.locator("#detailContent tbody tr")).to_have_count(2)
        self.assertNotIn("Precipitación", self.page.locator("#detailContent tbody").inner_text())
        self.assertIn("2010-12-31", self.page.locator("#detailContent tbody").inner_text())
        self.page.locator("#closeDetail").click()
        self.choose("variable", ["Precipitación"])
        self.counts(0, 0)
        expect(self.page.locator("#stationTable .empty")).to_be_visible()
        self.page.locator("#resetFilters").click()
        self.counts(1, 4)

    def test_search_communes_keyboard_and_mobile_layout(self):
        self.load()
        self.choose("region", ["Antofagasta", "Atacama"])
        expected_communes = {row["comuna"] for row in CATALOG["stations"] if row["region"] in ["Antofagasta", "Atacama"]}
        actual = set(self.page.locator('#communeFilter input[type="checkbox"]').evaluate_all("inputs => inputs.map(input => input.value)"))
        self.assertEqual(actual, expected_communes)
        self.choose("commune", ["Calama"])
        self.choose("region", [])
        self.page.locator("#regionFilter").get_by_role("checkbox", name="Antofagasta", exact=True).uncheck()
        expect(self.page.locator("#communeFilterSummary")).to_have_text("Todas")
        expected = self.expected({"region": ["Atacama"]})
        self.counts(len({row["station_key"] for row in expected}), len(expected))
        self.choose("region", ["Antofagasta"])
        self.page.locator("#communeFilter summary").click()
        self.page.locator("#communeFilter .multi-select-search").fill("calama")
        expect(self.page.locator("#communeFilter .multi-select-option:not([hidden])")).to_have_count(1)
        self.page.keyboard.press("Escape")
        expect(self.page.locator("#communeFilter summary")).to_be_focused()
        self.assertFalse(self.page.locator("#communeFilter").evaluate("element => element.open"))
        self.page.keyboard.press("Enter")
        expect(self.page.locator("#communeFilter .multi-select-menu")).to_be_visible()
        checkbox = self.page.locator("#communeFilter").get_by_role("checkbox", name="Calama", exact=True)
        checkbox.focus()
        self.page.keyboard.press("Space")
        expect(checkbox).to_be_checked()
        expected = self.expected({"region": ["Antofagasta", "Atacama"], "commune": ["Calama"]})
        self.counts(len({row["station_key"] for row in expected}), len(expected))
        self.page.keyboard.press("Space")
        expect(checkbox).not_to_be_checked()
        self.page.locator("h1").click()
        self.assertFalse(self.page.locator("#communeFilter").evaluate("element => element.open"))
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.choose("network", ["AGROMET", "VISMET / CR2"])
        box = self.page.locator("#networkFilter .multi-select-menu").bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 390)
        self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), True)
        artifacts = os.environ.get("MIRA_QA_ARTIFACTS")
        if artifacts:
            target = Path(artifacts)
            target.mkdir(parents=True, exist_ok=True)
            self.page.locator(".filters").screenshot(path=str(target / "filtros-movil.png"))
            self.page.set_viewport_size({"width": 1440, "height": 1000})
            self.page.locator(".workspace").screenshot(path=str(target / "filtros-escritorio.png"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
