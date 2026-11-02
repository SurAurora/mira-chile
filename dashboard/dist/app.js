(() => {
  "use strict";

  const { stations, series, metadata } = window.CATALOG_DATA;
  const seriesByStation = new Map();
  series.forEach((row) => {
    if (!seriesByStation.has(row.station_key)) seriesByStation.set(row.station_key, []);
    seriesByStation.get(row.station_key).push(row);
  });

  const colors = {
    AGROMET: "#18736f",
    "DGA / Red Hidrométrica Nacional": "#7357a6",
    "DMC / SACLIM": "#3c6ca8",
    "Explorador Eólico": "#d89a2b",
    SINCA: "#c65336",
    "VISMET / CR2": "#288ea0",
  };
  const filterIds = ["region", "commune", "network", "institution", "variable", "type", "height", "frequency", "current"];
  const filters = {};
  const searchInput = document.getElementById("searchFilter");
  const startYearInput = document.getElementById("startYearFilter");

  const map = L.map("map", { zoomControl: true, preferCanvas: true, minZoom: 4, maxZoom: 15 });
  map.attributionControl.setPrefix(false);
  map.fitBounds([[-29.65, -72.1], [-17.2, -66.6]]);
  map.createPane("reliefPane");
  map.getPane("reliefPane").style.zIndex = 250;
  map.getPane("reliefPane").style.pointerEvents = "none";

  const satelliteLayer = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution: "&copy; Esri — Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community",
    },
  );
  const osmLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  });
  const baseLayers = { satellite: satelliteLayer, osm: osmLayer, none: null };
  let activeBaseLayer = satelliteLayer.addTo(map);
  const reliefLayer = L.imageOverlay(window.CATALOG_RELIEF.bounds[0] ? "data/aster_relief.webp" : "", window.CATALOG_RELIEF.bounds, { opacity: 0.72, interactive: false, pane: "reliefPane" });
  const regionLayer = L.geoJSON(window.CATALOG_REGIONS, {
    style: { color: "#244c47", weight: 1.7, fill: false, opacity: 0.85 },
    onEachFeature: (feature, layer) => layer.bindTooltip(feature.properties.region, { sticky: true }),
  });
  const communeLayer = L.geoJSON(window.CATALOG_COMMUNES, {
    style: { color: "#6d7771", weight: 0.75, fill: false, opacity: 0.7 },
    onEachFeature: (feature, layer) => layer.bindTooltip(feature.properties.comuna, { sticky: true }),
  });
  let roadsLayer = null;
  const stationLayer = L.layerGroup().addTo(map);
  regionLayer.addTo(map);

  function selectBaseLayer(name) {
    if (activeBaseLayer) activeBaseLayer.remove();
    activeBaseLayer = baseLayers[name] || null;
    if (activeBaseLayer) activeBaseLayer.addTo(map);
  }

  function addRoads() {
    if (roadsLayer) return Promise.resolve(roadsLayer);
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "data/roads.js";
      script.onload = () => {
        roadsLayer = L.geoJSON(window.CATALOG_ROADS, {
          style: (feature) => ({
            color: feature.properties.clase <= 2 ? "#725345" : "#9b7d6c",
            weight: feature.properties.clase <= 2 ? 1.2 : 0.65,
            opacity: feature.properties.clase === 9 ? 0.35 : 0.65,
          }),
          interactive: false,
        });
        resolve(roadsLayer);
      };
      script.onerror = reject;
      document.body.appendChild(script);
    });
  }

  function unique(values) {
    return [...new Set(values.filter((value) => value !== null && value !== undefined && value !== ""))].sort((a, b) => String(a).localeCompare(String(b), "es", { numeric: true }));
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }

  class MultiSelect {
    constructor(name) {
      this.name = name;
      this.element = document.getElementById(`${name}Filter`);
      this.selected = new Set();
      this.values = [];
      this.element.addEventListener("toggle", () => {
        if (this.element.open) closeFilterMenus(this.element);
      });
    }

    get active() { return this.selected.size > 0 && this.selected.size < this.values.length; }
    matches(value) { return this.selected.size === 0 || this.selected.has(String(value)); }

    setOptions(values) {
      this.values = unique(values).map(String);
      this.selected = new Set([...this.selected].filter((value) => this.values.includes(value)));
      const id = this.element.id;
      this.element.innerHTML = `<summary aria-labelledby="${id}Label ${id}Summary"><span id="${id}Summary" class="multi-select-value"></span></summary>
        <div class="multi-select-menu">
          <div class="multi-select-toolbar"><button type="button" class="multi-select-all">${escapeHtml(this.element.dataset.all)}</button><small>Una o varias opciones</small></div>
          <input class="multi-select-search" type="search" placeholder="Buscar opciones…" aria-label="Buscar opciones de ${escapeHtml(document.getElementById(`${id}Label`).textContent)}" ${this.values.length < 8 ? "hidden" : ""} />
          <div class="multi-select-options" role="group" aria-labelledby="${id}Label">
            ${this.values.map((value) => `<label class="multi-select-option"><input type="checkbox" value="${escapeHtml(value)}" /><span>${escapeHtml(value)}</span></label>`).join("")}
          </div><div class="multi-select-no-options" hidden>No hay opciones con ese nombre.</div>
        </div>`;
      this.element.querySelector(".multi-select-all").addEventListener("click", () => {
        this.selected.clear();
        this.sync();
        onFilterChange(this.name);
      });
      this.element.querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.addEventListener("change", () => {
          if (input.checked) this.selected.add(input.value); else this.selected.delete(input.value);
          this.sync();
          onFilterChange(this.name);
        });
      });
      const search = this.element.querySelector(".multi-select-search");
      search.addEventListener("input", () => {
        const query = search.value.trim().toLocaleLowerCase("es");
        let visible = 0;
        this.element.querySelectorAll(".multi-select-option").forEach((option) => {
          option.hidden = !option.textContent.toLocaleLowerCase("es").includes(query);
          if (!option.hidden) visible += 1;
        });
        this.element.querySelector(".multi-select-no-options").hidden = visible > 0;
      });
      this.sync();
    }

    sync() {
      const all = this.selected.size === 0 || this.selected.size === this.values.length;
      const text = all ? this.element.dataset.all : (this.selected.size === 1 ? [...this.selected][0] : `${this.selected.size} opciones`);
      const summary = this.element.querySelector("summary");
      this.element.querySelector(".multi-select-value").textContent = text;
      summary.title = all ? this.element.dataset.all : [...this.selected].join(", ");
      this.element.classList.toggle("has-selection", !all);
      this.element.querySelector(".multi-select-all").setAttribute("aria-pressed", String(all));
      this.element.querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.checked = this.selected.has(input.value);
        input.closest("label").classList.toggle("is-selected", input.checked);
      });
    }

    reset() {
      this.selected.clear();
      this.setOptions(this.values);
      this.element.open = false;
    }
  }

  function closeFilterMenus(except = null) {
    Object.values(filters).forEach((filter) => {
      if (filter.element !== except) filter.element.open = false;
    });
  }

  function firstYear(row) {
    const match = /^(\d{4})-\d{2}-\d{2}(?:T|$)/.exec(String(row.primera_fecha_disponible || ""));
    return match ? Number(match[1]) : null;
  }

  function populateFilters() {
    filterIds.forEach((name) => { filters[name] = new MultiSelect(name); });
    filters.region.setOptions(stations.map((s) => s.region));
    filters.commune.setOptions(stations.map((s) => s.comuna));
    filters.network.setOptions(metadata.networks);
    filters.institution.setOptions(metadata.institutions);
    filters.variable.setOptions(metadata.variables);
    filters.type.setOptions(series.map((s) => s.tipo_variable));
    filters.height.setOptions(metadata.heights);
    filters.frequency.setOptions(metadata.frequencies);
    filters.current.setOptions(series.map((s) => s.datos_disponibles_actualmente));
    const years = series.map(firstYear).filter((year) => year !== null);
    const lastYear = Math.max(new Date().getFullYear(), ...years);
    const first = years.length ? Math.min(...years) : lastYear;
    const options = Array.from({ length: lastYear - first + 1 }, (_, index) => lastYear - index);
    startYearInput.innerHTML = `<option value="">Cualquier año</option>` + options.map((year) => `<option value="${year}">${year}</option>`).join("");
  }

  function onFilterChange(name) {
    if (name === "region") {
      filters.commune.setOptions(stations.filter((station) => filters.region.matches(station.region)).map((station) => station.comuna));
    }
    closeDetail();
    render();
  }

  function seriesMatches(row) {
    const year = firstYear(row);
    return filters.variable.matches(row.variable) &&
      filters.type.matches(row.tipo_variable) &&
      filters.height.matches(row.altura_sensor_suelo_m) &&
      filters.frequency.matches(row.frecuencia_muestreo) &&
      filters.current.matches(row.datos_disponibles_actualmente) &&
      (!startYearInput.value || (year !== null && year <= Number(startYearInput.value)));
  }

  function getFiltered() {
    const query = searchInput.value.trim().toLocaleLowerCase("es");
    const filteredStations = stations.filter((station) => {
      if (!filters.region.matches(station.region)) return false;
      if (!filters.commune.matches(station.comuna)) return false;
      if (!filters.network.matches(station.red)) return false;
      if (!filters.institution.matches(station.fuente_institucion)) return false;
      if (query && !`${station.nombre_estacion} ${station.id}`.toLocaleLowerCase("es").includes(query)) return false;
      const stationSeries = seriesByStation.get(station.station_key) || [];
      return stationSeries.some(seriesMatches);
    });
    const keys = new Set(filteredStations.map((s) => s.station_key));
    const filteredSeries = series.filter((row) => keys.has(row.station_key) && seriesMatches(row));
    return { filteredStations, filteredSeries };
  }

  function statusClass(value) {
    if (value === "Sí") return "status-yes";
    if (value === "No") return "status-no";
    return "status-unknown";
  }

  function stationCurrent(station, matchingSeries = null) {
    const rows = matchingSeries || seriesByStation.get(station.station_key) || [];
    if (rows.some((row) => row.datos_disponibles_actualmente === "Sí")) return "Sí";
    if (rows.every((row) => row.datos_disponibles_actualmente === "No")) return "No";
    return "No determinado";
  }

  function renderMetrics(filteredStations, filteredSeries) {
    document.getElementById("metricStations").textContent = filteredStations.length.toLocaleString("es-CL");
    document.getElementById("metricSeries").textContent = filteredSeries.length.toLocaleString("es-CL");
    document.getElementById("metricNetworks").textContent = unique(filteredStations.map((s) => s.red)).length;
    document.getElementById("metricCurrent").textContent = filteredSeries.filter((s) => s.datos_disponibles_actualmente === "Sí").length.toLocaleString("es-CL");
    const active = filterIds.filter((name) => filters[name].active).length + (startYearInput.value ? 1 : 0) + (searchInput.value.trim() ? 1 : 0);
    document.getElementById("filterCount").textContent = `${active} activo${active === 1 ? "" : "s"}`;
  }

  function renderMap(filteredStations, filteredSeries) {
    stationLayer.clearLayers();
    const matching = new Map();
    filteredSeries.forEach((row) => {
      if (!matching.has(row.station_key)) matching.set(row.station_key, []);
      matching.get(row.station_key).push(row);
    });
    filteredStations.forEach((station) => {
      if (!Number.isFinite(station.latitud) || !Number.isFinite(station.longitud)) return;
      const marker = L.circleMarker([station.latitud, station.longitud], {
        radius: 5.7, color: "#fffdf8", weight: 1.4, fillColor: colors[station.red] || "#444", fillOpacity: 0.92,
      });
      marker.bindTooltip(`<b>${escapeHtml(station.nombre_estacion)}</b><br>${escapeHtml(station.red)} · ${escapeHtml(station.id)}`);
      marker.on("click", () => openDetail(station.station_key));
      marker.addTo(stationLayer);
    });
  }

  function renderTable(filteredStations, filteredSeries) {
    const matching = new Map();
    filteredSeries.forEach((row) => {
      if (!matching.has(row.station_key)) matching.set(row.station_key, []);
      matching.get(row.station_key).push(row);
    });
    document.getElementById("tableSummary").textContent = `${filteredStations.length} estaciones · ${filteredSeries.length} series`;
    const tbody = document.querySelector("#stationTable tbody");
    if (!filteredStations.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty">No hay estaciones que cumplan todos los filtros.</td></tr>`;
      return;
    }
    tbody.innerHTML = filteredStations
      .slice()
      .sort((a, b) => a.nombre_estacion.localeCompare(b.nombre_estacion, "es"))
      .map((station) => {
        const rows = matching.get(station.station_key) || [];
        const vars = unique(rows.map((r) => r.variable));
        const current = stationCurrent(station, rows);
        return `<tr data-key="${station.station_key}">
          <td><span class="station-name">${escapeHtml(station.nombre_estacion)}</span><br><span class="subtle">${escapeHtml(station.fuente_institucion)}</span></td>
          <td>${escapeHtml(station.id)}</td><td>${escapeHtml(station.red)}</td>
          <td>${escapeHtml(station.region)}<br><span class="subtle">${escapeHtml(station.comuna)}</span></td>
          <td title="${escapeHtml(vars.join(", "))}">${vars.length} <span class="subtle">(${escapeHtml(vars.slice(0, 3).join(", "))}${vars.length > 3 ? "…" : ""})</span></td>
          <td>${rows.length}</td><td><span class="status ${statusClass(current)}">${escapeHtml(current)}</span></td>
        </tr>`;
      }).join("");
    tbody.querySelectorAll("tr[data-key]").forEach((row) => row.addEventListener("click", () => openDetail(row.dataset.key)));
  }

  function renderMatrix(filteredStations, filteredSeries) {
    const stationKeys = new Set(filteredStations.map((s) => s.station_key));
    const stationByKey = new Map(stations.map((s) => [s.station_key, s]));
    const networks = unique(filteredStations.map((s) => s.red));
    const variables = unique(filteredSeries.map((s) => s.variable));
    const counts = new Map();
    filteredSeries.forEach((row) => {
      if (!stationKeys.has(row.station_key)) return;
      const network = stationByKey.get(row.station_key).red;
      const key = `${network}\u0000${row.variable}`;
      if (!counts.has(key)) counts.set(key, new Set());
      counts.get(key).add(row.station_key);
    });
    const max = Math.max(1, ...[...counts.values()].map((set) => set.size));
    if (!variables.length) {
      document.getElementById("coverageMatrix").innerHTML = `<div class="empty">Sin cobertura para mostrar.</div>`;
      return;
    }
    const head = variables.map((variable) => `<th title="${escapeHtml(variable)}">${escapeHtml(variable)}</th>`).join("");
    const body = networks.map((network) => `<tr><td>${escapeHtml(network)}</td>${variables.map((variable) => {
      const count = counts.get(`${network}\u0000${variable}`)?.size || 0;
      const alpha = count ? 0.16 + 0.68 * count / max : 0;
      return `<td><span class="heat" style="background:rgba(24,115,111,${alpha})">${count || "—"}</span></td>`;
    }).join("")}</tr>`).join("");
    document.getElementById("coverageMatrix").innerHTML = `<table class="matrix"><thead><tr><th>Red</th>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function openDetail(key) {
    const station = stations.find((item) => item.station_key === key);
    const rows = (seriesByStation.get(key) || []).filter(seriesMatches).sort((a, b) => `${a.variable}${a.altura_sensor_suelo_m}`.localeCompare(`${b.variable}${b.altura_sensor_suelo_m}`, "es", { numeric: true }));
    if (!station) return;
    closeFilterMenus();
    const content = document.getElementById("detailContent");
    content.innerHTML = `<p class="eyebrow">${escapeHtml(station.red)} · ${escapeHtml(station.fuente_institucion)}</p>
      <h2>${escapeHtml(station.nombre_estacion)}</h2>
      <p class="muted">ID ${escapeHtml(station.id)} · ${escapeHtml(station.region)}, ${escapeHtml(station.comuna)}</p>
      <div class="detail-meta">
        <div><b>Latitud</b>${Number(station.latitud).toFixed(5)}</div>
        <div><b>Longitud</b>${Number(station.longitud).toFixed(5)}</div>
        <div><b>Altitud</b>${station.altitud_snm_m == null ? "No informada" : `${escapeHtml(station.altitud_snm_m)} m s.n.m.`}</div>
        <div><b>Series</b>${rows.length}</div>
        <div><b>Variables</b>${unique(rows.map((r) => r.variable)).length}</div>
        <div><b>Datos actuales</b>${escapeHtml(stationCurrent(station, rows))}</div>
      </div>
      <h3>Series de esta selección</h3>
      <div class="table-wrap"><table><thead><tr><th>Variable</th><th>Tipo</th><th>Altura</th><th>Frecuencia</th><th>Primera fecha</th><th>Actual</th><th>Guía</th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td>${escapeHtml(row.variable)}</td><td>${escapeHtml(row.tipo_variable)}</td><td>${escapeHtml(row.altura_sensor_suelo_m)}</td>
        <td>${escapeHtml(row.frecuencia_muestreo)}</td><td>${escapeHtml(row.primera_fecha_disponible)}</td>
        <td><span class="status ${statusClass(row.datos_disponibles_actualmente)}">${escapeHtml(row.datos_disponibles_actualmente)}</span></td>
        <td><a href="${encodeURI(row.readme_url)}" target="_blank" rel="noopener">README</a></td>
      </tr>`).join("")}</tbody></table></div>`;
    document.getElementById("stationDetail").classList.add("open");
    document.getElementById("stationDetail").setAttribute("aria-hidden", "false");
    document.getElementById("detailBackdrop").hidden = false;
  }

  function closeDetail() {
    document.getElementById("stationDetail").classList.remove("open");
    document.getElementById("stationDetail").setAttribute("aria-hidden", "true");
    document.getElementById("detailBackdrop").hidden = true;
  }

  function csvValue(value) { return `"${String(value ?? "").replace(/"/g, '""')}"`; }
  function exportCsv() {
    const { filteredStations, filteredSeries } = getFiltered();
    const stationMap = new Map(filteredStations.map((s) => [s.station_key, s]));
    const columns = ["nombre_estacion", "id", "red", "fuente_institucion", "region", "comuna", "latitud", "longitud", "altitud_snm_m", "variable", "tipo_variable", "altura_sensor_suelo_m", "frecuencia_muestreo", "primera_fecha_disponible", "datos_disponibles_actualmente", "readme_descarga"];
    // Excel usa habitualmente punto y coma como separador en configuraciones
    // regionales hispanohablantes. Las comillas conservan como una sola celda
    // los nombres que contienen comas, punto y coma o saltos de línea.
    const separator = ";";
    const lines = [columns.map(csvValue).join(separator)];
    filteredSeries.forEach((row) => {
      const station = stationMap.get(row.station_key);
      const merged = { ...station, ...row };
      lines.push(columns.map((column) => csvValue(merged[column])).join(separator));
    });
    const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "catalogo_estaciones_filtrado.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function render() {
    const { filteredStations, filteredSeries } = getFiltered();
    renderMetrics(filteredStations, filteredSeries);
    renderMap(filteredStations, filteredSeries);
    renderTable(filteredStations, filteredSeries);
    renderMatrix(filteredStations, filteredSeries);
  }

  populateFilters();
  document.getElementById("legend").innerHTML = Object.entries(colors).map(([name, color]) => `<span><i style="background:${color}"></i>${escapeHtml(name)}</span>`).join("");
  searchInput.addEventListener("input", () => onFilterChange("search"));
  startYearInput.addEventListener("change", () => onFilterChange("year"));
  document.getElementById("resetFilters").addEventListener("click", () => {
    filters.commune.setOptions(stations.map((s) => s.comuna));
    Object.values(filters).forEach((filter) => filter.reset());
    startYearInput.value = "";
    searchInput.value = "";
    closeDetail();
    render();
  });
  document.getElementById("exportCsv").addEventListener("click", exportCsv);
  document.getElementById("closeDetail").addEventListener("click", closeDetail);
  document.getElementById("detailBackdrop").addEventListener("click", closeDetail);
  document.addEventListener("click", (event) => {
    Object.values(filters).forEach((filter) => {
      if (!filter.element.contains(event.target)) filter.element.open = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const open = Object.values(filters).find((filter) => filter.element.open);
      closeFilterMenus();
      if (open) open.element.querySelector("summary").focus(); else closeDetail();
    }
  });
  document.querySelectorAll('input[name="baseMap"]').forEach((input) => {
    input.addEventListener("change", (event) => selectBaseLayer(event.target.value));
  });
  document.getElementById("layerRelief").addEventListener("change", (event) => event.target.checked ? reliefLayer.addTo(map) : reliefLayer.remove());
  document.getElementById("layerRegions").addEventListener("change", (event) => event.target.checked ? regionLayer.addTo(map) : regionLayer.remove());
  document.getElementById("layerCommunes").addEventListener("change", (event) => event.target.checked ? communeLayer.addTo(map) : communeLayer.remove());
  document.getElementById("layerRoads").addEventListener("change", async (event) => {
    if (event.target.checked) (await addRoads()).addTo(map); else if (roadsLayer) roadsLayer.remove();
  });
  render();
})();
