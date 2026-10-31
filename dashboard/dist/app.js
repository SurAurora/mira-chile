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
    "DMC / SACLIM": "#3c6ca8",
    "Explorador Eólico": "#d89a2b",
    SINCA: "#c65336",
  };
  const filterIds = ["region", "commune", "network", "institution", "variable", "type", "height", "frequency", "current"];
  const filters = Object.fromEntries(filterIds.map((name) => [name, document.getElementById(`${name}Filter`)]));
  const searchInput = document.getElementById("searchFilter");

  const map = L.map("map", { zoomControl: true, preferCanvas: true, minZoom: 4, maxZoom: 15 });
  map.attributionControl.setPrefix(false);
  map.fitBounds([[-29.65, -72.1], [-17.2, -66.6]]);
  map.createPane("reliefPane");
  map.getPane("reliefPane").style.zIndex = 200;
  map.getPane("reliefPane").style.pointerEvents = "none";

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
  reliefLayer.addTo(map);
  regionLayer.addTo(map);

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

  function fillSelect(select, values, label = "Todos") {
    select.innerHTML = `<option value="">${label}</option>` + unique(values).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }

  function populateFilters() {
    fillSelect(filters.region, stations.map((s) => s.region));
    fillSelect(filters.commune, stations.map((s) => s.comuna));
    fillSelect(filters.network, metadata.networks);
    fillSelect(filters.institution, metadata.institutions);
    fillSelect(filters.variable, metadata.variables);
    fillSelect(filters.type, unique(series.map((s) => s.tipo_variable)));
    fillSelect(filters.height, metadata.heights);
    fillSelect(filters.frequency, metadata.frequencies);
    fillSelect(filters.current, unique(series.map((s) => s.datos_disponibles_actualmente)));
  }

  function selected(name) { return filters[name].value; }

  function seriesMatches(row) {
    return (!selected("variable") || row.variable === selected("variable")) &&
      (!selected("type") || row.tipo_variable === selected("type")) &&
      (!selected("height") || String(row.altura_sensor_suelo_m) === selected("height")) &&
      (!selected("frequency") || row.frecuencia_muestreo === selected("frequency")) &&
      (!selected("current") || row.datos_disponibles_actualmente === selected("current"));
  }

  function getFiltered() {
    const query = searchInput.value.trim().toLocaleLowerCase("es");
    const filteredStations = stations.filter((station) => {
      if (selected("region") && station.region !== selected("region")) return false;
      if (selected("commune") && station.comuna !== selected("commune")) return false;
      if (selected("network") && station.red !== selected("network")) return false;
      if (selected("institution") && station.fuente_institucion !== selected("institution")) return false;
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
    const active = filterIds.filter((name) => selected(name)).length + (searchInput.value.trim() ? 1 : 0);
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
    const rows = (seriesByStation.get(key) || []).slice().sort((a, b) => `${a.variable}${a.altura_sensor_suelo_m}`.localeCompare(`${b.variable}${b.altura_sensor_suelo_m}`, "es", { numeric: true }));
    if (!station) return;
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
      <h3>Series disponibles</h3>
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
    const lines = [columns.map(csvValue).join(",")];
    filteredSeries.forEach((row) => {
      const station = stationMap.get(row.station_key);
      const merged = { ...station, ...row };
      lines.push(columns.map((column) => csvValue(merged[column])).join(","));
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
  [...Object.values(filters), searchInput].forEach((element) => element.addEventListener(element === searchInput ? "input" : "change", render));
  document.getElementById("resetFilters").addEventListener("click", () => { Object.values(filters).forEach((f) => { f.value = ""; }); searchInput.value = ""; render(); });
  document.getElementById("exportCsv").addEventListener("click", exportCsv);
  document.getElementById("closeDetail").addEventListener("click", closeDetail);
  document.getElementById("detailBackdrop").addEventListener("click", closeDetail);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDetail(); });
  document.getElementById("layerRelief").addEventListener("change", (event) => event.target.checked ? reliefLayer.addTo(map) : reliefLayer.remove());
  document.getElementById("layerRegions").addEventListener("change", (event) => event.target.checked ? regionLayer.addTo(map) : regionLayer.remove());
  document.getElementById("layerCommunes").addEventListener("change", (event) => event.target.checked ? communeLayer.addTo(map) : communeLayer.remove());
  document.getElementById("layerRoads").addEventListener("change", async (event) => {
    if (event.target.checked) (await addRoads()).addTo(map); else if (roadsLayer) roadsLayer.remove();
  });
  render();
})();
