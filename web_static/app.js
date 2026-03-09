const state = {
  sessionId: null,
  view: null,
  mode: "event",
  pending: null,
  detection: null,
  userSelectedPreset: false,
}

const modeHelp = {
  event: "Click twice on the time-profile panel to mark the start and end of the event window.",
  crop: "Click twice on the time-profile panel to define the crop window you want to work in.",
  region: "Click twice on the time-profile panel to add a burst or sub-burst fitting region.",
  "add-peak": "Click once on the time-profile panel to place a peak marker.",
  "remove-peak": "Click once near an existing peak on the time-profile panel to remove it.",
  "mask-channel": "Click once on the heatmap or spectrum panel to mask a single frequency channel.",
  "mask-range": "Click twice on the heatmap or spectrum panel to mask a contiguous frequency range.",
  "spec-extent": "Click twice on the spectrum panel to set the spectral extent used for measurements.",
}

const statusChip = document.getElementById("statusChip")
const modeChip = document.getElementById("modeChip")
const modeHelpEl = document.getElementById("modeHelp")
const pendingBox = document.getElementById("pendingBox")
const fileSelect = document.getElementById("fileSelect")
const fileInput = document.getElementById("fileInput")
const dmInput = document.getElementById("dmInput")
const telescopeInput = document.getElementById("telescopeInput")
const sefdInput = document.getElementById("sefdInput")
const readStartInput = document.getElementById("readStartInput")
const initialCropInput = document.getElementById("initialCropInput")
const detectionHint = document.getElementById("detectionHint")
const resolutionLabel = document.getElementById("resolutionLabel")
const sessionFacts = document.getElementById("sessionFacts")
const burstTitle = document.getElementById("burstTitle")
const burstSubtitle = document.getElementById("burstSubtitle")
const heroMetrics = document.getElementById("heroMetrics")
const resultsContent = document.getElementById("resultsContent")
const presetDefaults = new Map()
let syncingPresetSelection = false

document.addEventListener("DOMContentLoaded", async () => {
  bindControls()
  await loadPresets()
  await loadFiles()
  if (fileSelect.options.length > 0) {
    fileInput.value = fileSelect.value
    await detectSelectedFile()
    await loadSession()
  }
})

function bindControls() {
  fileSelect.addEventListener("change", async () => {
    fileInput.value = fileSelect.value
    state.userSelectedPreset = false
    await detectSelectedFile()
  })

  fileInput.addEventListener("change", async () => {
    state.userSelectedPreset = false
    await detectSelectedFile()
  })

  telescopeInput.addEventListener("change", () => {
    if (!syncingPresetSelection) {
      state.userSelectedPreset = true
    }
    syncPresetDefaults()
    renderDetectionHint()
  })

  document.getElementById("loadButton").addEventListener("click", loadSession)
  document.getElementById("setDmButton").addEventListener("click", () => {
    postAction("set_dm", { dm: Number(dmInput.value) })
  })
  document.getElementById("resetViewButton").addEventListener("click", () => postAction("reset_view"))
  document.getElementById("clearRegionsButton").addEventListener("click", () => postAction("clear_regions"))
  document.getElementById("computeButton").addEventListener("click", () => postAction("compute_properties"))
  document.getElementById("undoMaskButton").addEventListener("click", () => postAction("undo_mask"))
  document.getElementById("resetMaskButton").addEventListener("click", () => postAction("reset_mask"))
  document.getElementById("jessButton").addEventListener("click", () => postAction("auto_mask_jess"))

  document.getElementById("timeDownButton").addEventListener("click", () => scaleFactor("time", 0.5))
  document.getElementById("timeUpButton").addEventListener("click", () => scaleFactor("time", 2))
  document.getElementById("freqDownButton").addEventListener("click", () => scaleFactor("freq", 0.5))
  document.getElementById("freqUpButton").addEventListener("click", () => scaleFactor("freq", 2))

  document.querySelectorAll(".mode-button").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode))
  })
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  })
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(payload.detail || "Request failed")
  }
  return payload
}

async function loadPresets() {
  try {
    const payload = await api("/api/presets")
    telescopeInput.innerHTML = ""
    for (const preset of payload.presets) {
      presetDefaults.set(preset.key, preset)
      const option = document.createElement("option")
      option.value = preset.key
      option.textContent = preset.label
      telescopeInput.appendChild(option)
    }
    setPresetSelection("generic")
    renderDetectionHint()
  } catch (error) {
    setStatus(error.message, true)
  }
}

async function detectSelectedFile() {
  const bfile = fileInput.value.trim() || fileSelect.value
  if (!bfile) {
    state.detection = null
    renderDetectionHint()
    return null
  }

  try {
    const payload = await api("/api/detect", {
      method: "POST",
      body: JSON.stringify({ bfile }),
    })
    state.detection = payload
    if (!state.userSelectedPreset) {
      setPresetSelection(payload.detected_preset_key)
    }
    renderDetectionHint()
    return payload
  } catch (error) {
    state.detection = null
    renderDetectionHint(error.message)
    throw error
  }
}

async function loadFiles() {
  try {
    const payload = await api("/api/files")
    fileSelect.innerHTML = ""
    for (const file of payload.files) {
      const option = document.createElement("option")
      option.value = file
      option.textContent = file
      fileSelect.appendChild(option)
    }
    if (payload.files.length > 0) {
      fileSelect.value = payload.files[0]
    }
  } catch (error) {
    setStatus(error.message, true)
  }
}

async function loadSession() {
  const bfile = fileInput.value.trim() || fileSelect.value
  if (!bfile) {
    setStatus("Pick a filterbank first", true)
    return
  }

  setStatus("Loading", false)
  clearPending()
  try {
    await detectSelectedFile()
    const payload = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({
        bfile,
        dm: Number(dmInput.value),
        telescope: telescopeInput.value,
        sefd_jy: parseOptionalNumber(sefdInput.value),
        read_start_sec: parseOptionalNumber(readStartInput.value),
        initial_crop_sec: parseOptionalNumber(initialCropInput.value),
      }),
    })
    state.sessionId = payload.session_id
    applyView(payload.view)
    setStatus("Loaded", false)
  } catch (error) {
    setStatus(error.message, true)
  }
}

async function postAction(type, payload = {}) {
  if (!state.sessionId) {
    setStatus("Load a session first", true)
    return
  }

  setStatus("Updating", false)
  try {
    const response = await api(`/api/sessions/${state.sessionId}/actions`, {
      method: "POST",
      body: JSON.stringify({ type, payload }),
    })
    applyView(response.view)
    setStatus("Ready", false)
  } catch (error) {
    setStatus(error.message, true)
  }
}

function applyView(view) {
  state.view = view
  resolutionLabel.textContent = `t x${view.state.time_factor} / f x${view.state.freq_factor}`
  burstTitle.textContent = view.meta.burst_name
  const detectionSummary =
    view.meta.preset_key === view.meta.detected_preset_key
      ? `Detected from ${view.meta.detection_basis}.`
      : `Detected ${view.meta.detected_telescope} from ${view.meta.detection_basis}, loaded with ${view.meta.telescope}.`
  burstSubtitle.textContent = `${fmt(view.meta.shape[0], 0)} channels x ${fmt(view.meta.shape[1], 0)} time bins loaded with the ${view.meta.telescope} profile. ${detectionSummary}`
  renderHero(view)
  renderSessionFacts(view)
  renderResults(view.results)
  renderPlots(view)
}

function renderHero(view) {
  const maskedCount = view.state.masked_channels.length
  heroMetrics.innerHTML = `
    <div class="metric-card">
      <span>Time Resolution</span>
      <strong>${fmt(view.meta.tsamp_us * view.state.time_factor, 2)} us</strong>
    </div>
    <div class="metric-card">
      <span>Calibration</span>
      <strong>${view.meta.sefd_jy === null ? "Uncalibrated" : `${fmt(view.meta.sefd_jy, 2)} Jy SEFD`}</strong>
    </div>
    <div class="metric-card">
      <span>Masked Channels</span>
      <strong>${maskedCount}</strong>
    </div>
  `
}

function renderSessionFacts(view) {
  const facts = [
    ["File", view.meta.burst_name],
    ["Selected profile", view.meta.telescope],
    ["Detected profile", view.meta.detected_telescope],
    ["Telescope ID", view.meta.telescope_id === null ? "missing" : String(view.meta.telescope_id)],
    ["Machine ID", view.meta.machine_id === null ? "missing" : String(view.meta.machine_id)],
    ["Detection", view.meta.detection_basis],
    ["Raw shape", `${view.meta.shape[0]} x ${view.meta.shape[1]}`],
    ["Displayed shape", `${view.meta.view_shape[0]} x ${view.meta.view_shape[1]}`],
    ["SEFD", view.meta.sefd_jy === null ? "not set" : `${fmt(view.meta.sefd_jy, 2)} Jy`],
    ["Crop", `${fmt(view.state.crop_ms[0], 2)} to ${fmt(view.state.crop_ms[1], 2)} ms`],
    ["Event", `${fmt(view.state.event_ms[0], 2)} to ${fmt(view.state.event_ms[1], 2)} ms`],
    ["Spectral extent", `${fmt(view.state.spectral_extent_mhz[0], 1)} to ${fmt(view.state.spectral_extent_mhz[1], 1)} MHz`],
    ["Peaks", view.state.peak_ms.length ? view.state.peak_ms.map((value) => `${fmt(value, 2)} ms`).join(", ") : "auto"],
    ["Masked channels", compactList(view.state.masked_channels)],
  ]

  sessionFacts.innerHTML = facts
    .map(
      ([label, value]) =>
        `<div class="kv-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`,
    )
    .join("")
}

function renderResults(results) {
  if (!results) {
    resultsContent.innerHTML =
      '<div class="empty-state">No measurements yet. Click Compute after marking the burst.</div>'
    return
  }

  const tiles = [
    tile("Fluence", results.fluence_jyms === null ? "n/a" : `${fmt(results.fluence_jyms, 3)} Jy ms`),
    tile("Peak Flux", results.peak_flux_jy === null ? "n/a" : `${fmt(results.peak_flux_jy, 3)} Jy`),
    tile("Duration", `${fmt(results.event_duration_ms, 3)} ms`),
    tile("MJD @ Peak", fmt(results.mjd_at_peak, 8)),
    tile("Spectral Extent", `${fmt(results.spectral_extent_mhz, 2)} MHz`),
    tile("Peak Positions", results.peak_positions_ms.length ? results.peak_positions_ms.map((value) => `${fmt(value, 2)} ms`).join(", ") : "n/a"),
    tile("Mask Count", String(results.mask_count)),
    tile("Gaussian Fits", `${results.gaussian_fits.length} region(s)`),
  ]

  const fitList = results.gaussian_fits.length
    ? `<ol class="fit-list">${results.gaussian_fits
        .map(
          (fit) =>
            `<li>mu ${fmt(fit.mu_ms, 3)} ms, sigma ${fmt(fit.sigma_ms, 3)} ms, amp ${fmt(fit.amp, 3)}</li>`,
        )
        .join("")}</ol>`
    : "<div class=\"empty-state\">No Gaussian fits were produced for the current burst-region selection.</div>"

  resultsContent.innerHTML = `
    ${tiles.join("")}
    <div class="result-tile code">
      <span class="results-label">Gaussian Fit Summary</span>
      ${fitList}
    </div>
  `
}

function tile(label, value) {
  return `<div class="result-tile"><span class="results-label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
}

async function renderPlots(view) {
  await Plotly.react(
    "timePlot",
    [
      {
        x: view.plot.time_profile.x_ms,
        y: view.plot.time_profile.y,
        mode: "lines",
        type: "scattergl",
        line: { color: "#162e3a", width: 2.2 },
        hovertemplate: "%{x:.3f} ms<br>%{y:.3f}<extra></extra>",
      },
    ],
    {
      margin: { l: 60, r: 20, t: 10, b: 44 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(255,255,255,0.55)",
      xaxis: { title: "Time (ms)", gridcolor: "rgba(24,33,38,0.08)" },
      yaxis: { title: "Summed Intensity", gridcolor: "rgba(24,33,38,0.08)" },
      shapes: buildTimeShapes(view),
      showlegend: false,
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )

  await Plotly.react(
    "heatmapPlot",
    [
      {
        x: view.plot.heatmap.x_ms,
        y: view.plot.heatmap.y_mhz,
        z: view.plot.heatmap.z,
        type: "heatmap",
        colorscale: "Viridis",
        zmin: view.plot.heatmap.zmin,
        zmax: view.plot.heatmap.zmax,
        hovertemplate: "%{x:.3f} ms<br>%{y:.3f} MHz<br>%{z:.3f}<extra></extra>",
        colorbar: { title: "I" },
      },
    ],
    {
      margin: { l: 72, r: 24, t: 10, b: 46 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(255,255,255,0.55)",
      xaxis: { title: "Time (ms)" },
      yaxis: { title: "Frequency (MHz)" },
      shapes: buildHeatmapShapes(view),
      showlegend: false,
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )

  await Plotly.react(
    "spectrumPlot",
    [
      {
        x: view.plot.spectrum.x,
        y: view.plot.spectrum.y_mhz,
        mode: "lines",
        type: "scattergl",
        line: { color: "#8b4513", width: 2.2 },
        hovertemplate: "%{x:.3f}<br>%{y:.3f} MHz<extra></extra>",
      },
    ],
    {
      margin: { l: 56, r: 20, t: 10, b: 44 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(255,255,255,0.55)",
      xaxis: { title: "Summed Intensity", gridcolor: "rgba(24,33,38,0.08)" },
      yaxis: { title: "Frequency (MHz)", gridcolor: "rgba(24,33,38,0.08)" },
      shapes: buildSpectrumShapes(view),
      showlegend: false,
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )

  bindPlotEvents()
}

function bindPlotEvents() {
  bindPlotEvent("timePlot", handleTimePlotClick)
  bindPlotEvent("heatmapPlot", handleFreqPlotClick)
  bindPlotEvent("spectrumPlot", handleFreqPlotClick)
}

function bindPlotEvent(elementId, handler) {
  const plot = document.getElementById(elementId)
  if (plot.dataset.bound === "true") {
    return
  }
  plot.on("plotly_click", handler)
  plot.dataset.bound = "true"
}

function handleTimePlotClick(event) {
  const point = event.points?.[0]
  if (!point) return
  const timeMs = Number(point.x)

  if (state.mode === "event") {
    handlePair("set_event", timeMs, "ms")
  } else if (state.mode === "crop") {
    handlePair("set_crop", timeMs, "ms")
  } else if (state.mode === "region") {
    handlePair("add_region", timeMs, "ms")
  } else if (state.mode === "add-peak") {
    postAction("add_peak", { time_ms: timeMs })
  } else if (state.mode === "remove-peak") {
    postAction("remove_peak", { time_ms: timeMs })
  }
}

function handleFreqPlotClick(event) {
  const point = event.points?.[0]
  if (!point) return
  const freqMHz = Number(point.y)

  if (state.mode === "mask-channel") {
    postAction("mask_channel", { freq_mhz: freqMHz })
  } else if (state.mode === "mask-range") {
    handlePair("mask_range", freqMHz, "MHz")
  } else if (state.mode === "spec-extent") {
    handlePair("set_spectral_extent", freqMHz, "MHz")
  }
}

function handlePair(action, value, unit) {
  if (!state.pending || state.pending.action !== action) {
    state.pending = { action, value, unit }
    pendingBox.textContent = `First point set at ${fmt(value, 3)} ${unit}. Click the second point.`
    return
  }

  const firstValue = state.pending.value
  clearPending()

  if (action === "set_crop" || action === "set_event" || action === "add_region") {
    postAction(action, { start_ms: firstValue, end_ms: value })
  } else if (action === "mask_range" || action === "set_spectral_extent") {
    postAction(action, { start_freq_mhz: firstValue, end_freq_mhz: value })
  }
}

function clearPending() {
  state.pending = null
  pendingBox.textContent = "Waiting for first click."
}

function setMode(mode) {
  state.mode = mode
  clearPending()
  modeChip.textContent = `Mode: ${mode}`
  modeHelpEl.textContent = modeHelp[mode]
  document.querySelectorAll(".mode-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode)
  })
}

function scaleFactor(axis, multiplier) {
  if (!state.view) return
  const key = axis === "time" ? "time_factor" : "freq_factor"
  const current = state.view.state[key]
  const next = multiplier > 1 ? current * multiplier : Math.max(1, Math.floor(current / 2))
  postAction(key, { value: next })
}

function buildTimeShapes(view) {
  const yMin = minFinite(view.plot.time_profile.y)
  const yMax = maxFinite(view.plot.time_profile.y)
  const shapes = [
    verticalLine(view.state.event_ms[0], yMin, yMax, "#d97706"),
    verticalLine(view.state.event_ms[1], yMin, yMax, "#d97706"),
  ]

  for (const region of view.state.burst_regions_ms) {
    shapes.push({
      type: "rect",
      xref: "x",
      yref: "paper",
      x0: region[0],
      x1: region[1],
      y0: 0,
      y1: 1,
      fillcolor: "rgba(22, 163, 74, 0.14)",
      line: { width: 0 },
    })
  }

  for (const peak of view.state.peak_ms) {
    shapes.push(verticalLine(peak, yMin, yMax, "#dc2626", "dot"))
  }
  return shapes
}

function buildHeatmapShapes(view) {
  const x0 = view.state.crop_ms[0]
  const x1 = view.state.crop_ms[1]
  const y0 = view.meta.freq_range_mhz[0]
  const y1 = view.meta.freq_range_mhz[1]
  const shapes = [
    verticalLine(view.state.event_ms[0], y0, y1, "#d97706"),
    verticalLine(view.state.event_ms[1], y0, y1, "#d97706"),
    horizontalLine(view.state.spectral_extent_mhz[0], x0, x1, "#7c3aed"),
    horizontalLine(view.state.spectral_extent_mhz[1], x0, x1, "#7c3aed"),
  ]

  for (const peak of view.state.peak_ms) {
    shapes.push(verticalLine(peak, y0, y1, "#dc2626", "dot"))
  }
  return shapes
}

function buildSpectrumShapes(view) {
  const xMin = minFinite(view.plot.spectrum.x)
  const xMax = maxFinite(view.plot.spectrum.x)
  return [
    horizontalLine(view.state.spectral_extent_mhz[0], xMin, xMax, "#7c3aed"),
    horizontalLine(view.state.spectral_extent_mhz[1], xMin, xMax, "#7c3aed"),
  ]
}

function verticalLine(x, y0, y1, color, dash = "solid") {
  return {
    type: "line",
    x0: x,
    x1: x,
    y0,
    y1,
    line: { color, width: 2, dash },
  }
}

function horizontalLine(y, x0, x1, color, dash = "solid") {
  return {
    type: "line",
    x0,
    x1,
    y0: y,
    y1: y,
    line: { color, width: 2, dash },
  }
}

function setStatus(text, isError) {
  statusChip.textContent = text
  statusChip.style.background = isError ? "rgba(192, 86, 33, 0.12)" : "rgba(15, 118, 110, 0.1)"
  statusChip.style.color = isError ? "#c05621" : "#0f766e"
}

function setPresetSelection(presetKey) {
  syncingPresetSelection = true
  telescopeInput.value = presetKey
  syncingPresetSelection = false
  syncPresetDefaults()
}

function renderDetectionHint(errorMessage = null) {
  if (errorMessage) {
    detectionHint.textContent = `Detection error: ${errorMessage}`
    return
  }

  if (!state.detection) {
    detectionHint.textContent = "Detection: waiting for a filterbank."
    return
  }

  const detectedLabel = state.detection.detected_preset_label
  const selectedLabel = presetDefaults.get(telescopeInput.value)?.label || telescopeInput.value
  let text = `Detection: ${detectedLabel} (${state.detection.detection_basis}).`

  if (state.detection.detected_preset_key === "generic") {
    text = `Detection: no known telescope match (${state.detection.detection_basis}). Using Generic Filterbank by default.`
  }

  if (state.userSelectedPreset && telescopeInput.value !== state.detection.detected_preset_key) {
    text += ` Manual override active: ${selectedLabel}.`
  } else {
    text += " You can override this before loading."
  }

  detectionHint.textContent = text
}

function syncPresetDefaults() {
  const preset = presetDefaults.get(telescopeInput.value)
  if (!preset) return
  sefdInput.placeholder = preset.sefd_jy === null ? "optional" : String(preset.sefd_jy)
  readStartInput.placeholder = String(preset.read_start_sec)
  initialCropInput.placeholder = preset.initial_crop_sec === null ? "full file" : String(preset.initial_crop_sec)
}

function parseOptionalNumber(value) {
  const trimmed = value.trim()
  return trimmed === "" ? null : Number(trimmed)
}

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-"
  }
  return Number(value).toFixed(digits)
}

function minFinite(values) {
  const finite = values.map(Number).filter(Number.isFinite)
  return finite.length ? Math.min(...finite) : 0
}

function maxFinite(values) {
  const finite = values.map(Number).filter(Number.isFinite)
  return finite.length ? Math.max(...finite) : 1
}

function compactList(values) {
  if (!values.length) return "0"
  if (values.length <= 10) return values.join(", ")
  return `${values.slice(0, 10).join(", ")} ... (+${values.length - 10})`
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;")
}
