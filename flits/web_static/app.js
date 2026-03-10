const state = {
  sessionId: null,
  view: null,
  mode: "event",
  pending: null,
  detection: null,
  userSelectedPreset: false,
  busyAction: null,
}

const modeLabels = {
  event: "Event Window",
  crop: "Crop Window",
  region: "Burst Region",
  "add-peak": "Add Peak",
  "remove-peak": "Remove Peak",
  "mask-channel": "Mask Channel",
  "mask-range": "Mask Range",
  "spec-extent": "Spectral Extent",
}

const modeHelp = {
  event: "Click twice on the top time profile to mark the start and end of the event window.",
  crop: "Click twice on the top time profile to define the crop window you want to work in.",
  region: "Click twice on the top time profile to add a burst or sub-burst fitting region.",
  "add-peak": "Click once on the top time profile to place a peak marker.",
  "remove-peak": "Click once near an existing peak on the top time profile to remove it.",
  "mask-channel": "Click once on the center dynamic spectrum or right frequency profile to mask a single frequency channel.",
  "mask-range": "Click twice on the center dynamic spectrum or right frequency profile to mask a contiguous frequency range.",
  "spec-extent": "Click twice on the right frequency profile to set the spectral extent used for measurements.",
}

const statusChip = document.getElementById("statusChip")
const modeChip = document.getElementById("modeChip")
const modeHelpEl = document.getElementById("modeHelp")
const pendingBox = document.getElementById("pendingBox")
const loadButton = document.getElementById("loadButton")
const fileSelect = document.getElementById("fileSelect")
const fileInput = document.getElementById("fileInput")
const dmInput = document.getElementById("dmInput")
const telescopeInput = document.getElementById("telescopeInput")
const sefdInput = document.getElementById("sefdInput")
const readStartInput = document.getElementById("readStartInput")
const initialCropInput = document.getElementById("initialCropInput")
const dmHalfRangeInput = document.getElementById("dmHalfRangeInput")
const dmStepInput = document.getElementById("dmStepInput")
const detectionHint = document.getElementById("detectionHint")
const resolutionLabel = document.getElementById("resolutionLabel")
const dmOptimizeBadge = document.getElementById("dmOptimizeBadge")
const sessionSummary = document.getElementById("sessionSummary")
const sessionFacts = document.getElementById("sessionFacts")
const sessionBadge = document.getElementById("sessionBadge")
const hero = document.querySelector(".hero")
const burstTitle = document.getElementById("burstTitle")
const heroTags = document.getElementById("heroTags")
const burstSubtitle = document.getElementById("burstSubtitle")
const heroMetrics = document.getElementById("heroMetrics")
const resultsContent = document.getElementById("resultsContent")
const dmOptimizationContent = document.getElementById("dmOptimizationContent")
const dmOptimizationPlot = document.getElementById("dmOptimizationPlot")
const setDmButton = document.getElementById("setDmButton")
const optimizeDmButton = document.getElementById("optimizeDmButton")
const applyBestDmButton = document.getElementById("applyBestDmButton")
const resetViewButton = document.getElementById("resetViewButton")
const clearRegionsButton = document.getElementById("clearRegionsButton")
const computeButton = document.getElementById("computeButton")
const undoMaskButton = document.getElementById("undoMaskButton")
const resetMaskButton = document.getElementById("resetMaskButton")
const jessButton = document.getElementById("jessButton")
const timeDownButton = document.getElementById("timeDownButton")
const timeUpButton = document.getElementById("timeUpButton")
const freqDownButton = document.getElementById("freqDownButton")
const freqUpButton = document.getElementById("freqUpButton")
const toastStack = document.getElementById("toastStack")
const presetDefaults = new Map()
let syncingPresetSelection = false
const viewerDomains = {
  heatmap: { x: [0.0, 0.78], y: [0.0, 0.78] },
  time: { x: [0.0, 0.78], y: [0.82, 1.0] },
  spectrum: { x: [0.82, 1.0], y: [0.0, 0.78] },
}
const modeButtons = Array.from(document.querySelectorAll(".mode-button"))
const sessionControls = [
  setDmButton,
  optimizeDmButton,
  applyBestDmButton,
  resetViewButton,
  clearRegionsButton,
  computeButton,
  undoMaskButton,
  resetMaskButton,
  jessButton,
  timeDownButton,
  timeUpButton,
  freqDownButton,
  freqUpButton,
  ...modeButtons,
]
const busyLockControls = [
  loadButton,
  fileSelect,
  fileInput,
  dmInput,
  telescopeInput,
  sefdInput,
  readStartInput,
  initialCropInput,
  dmHalfRangeInput,
  dmStepInput,
]

document.addEventListener("DOMContentLoaded", async () => {
  rememberButtonLabels()
  bindControls()
  setMode(state.mode)
  setStatus("Idle", "neutral")
  updateControlStates()
  await loadPresets()
  await loadFiles()
  if (fileSelect.options.length > 0) {
    fileInput.value = fileSelect.value
    await detectSelectedFile()
    await loadSession({ silent: true })
  }
})

function bindControls() {
  fileInput.addEventListener("input", () => {
    updateControlStates()
  })

  fileSelect.addEventListener("change", async () => {
    fileInput.value = fileSelect.value
    state.userSelectedPreset = false
    await detectSelectedFile()
    updateControlStates()
  })

  fileInput.addEventListener("change", async () => {
    state.userSelectedPreset = false
    await detectSelectedFile()
    updateControlStates()
  })

  telescopeInput.addEventListener("change", () => {
    if (!syncingPresetSelection) {
      state.userSelectedPreset = true
    }
    syncPresetDefaults()
    renderDetectionHint()
  })

  loadButton.addEventListener("click", () => loadSession())
  setDmButton.addEventListener("click", () => {
    postAction("set_dm", { dm: Number(dmInput.value) })
  })
  optimizeDmButton.addEventListener("click", () => {
    postAction("optimize_dm", {
      center_dm: Number(dmInput.value),
      half_range: Number(dmHalfRangeInput.value),
      step: Number(dmStepInput.value),
    })
  })
  applyBestDmButton.addEventListener("click", () => {
    const bestDm = state.view?.dm_optimization?.best_dm
    if (!Number.isFinite(Number(bestDm))) {
      return
    }
    dmInput.value = String(bestDm)
    postAction("set_dm", { dm: Number(bestDm) })
  })
  resetViewButton.addEventListener("click", () => postAction("reset_view"))
  clearRegionsButton.addEventListener("click", () => postAction("clear_regions"))
  computeButton.addEventListener("click", () => postAction("compute_properties"))
  undoMaskButton.addEventListener("click", () => postAction("undo_mask"))
  resetMaskButton.addEventListener("click", () => postAction("reset_mask"))
  jessButton.addEventListener("click", () => postAction("auto_mask_jess"))

  timeDownButton.addEventListener("click", () => scaleFactor("time", 0.5))
  timeUpButton.addEventListener("click", () => scaleFactor("time", 2))
  freqDownButton.addEventListener("click", () => scaleFactor("freq", 0.5))
  freqUpButton.addEventListener("click", () => scaleFactor("freq", 2))

  modeButtons.forEach((button) => {
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
    updateControlStates()
  } catch (error) {
    setStatus(error.message, "error")
    showToast(error.message, "error")
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
    updateControlStates()
    return payload
  } catch (error) {
    state.detection = null
    renderDetectionHint(error.message)
    updateControlStates()
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
    updateControlStates()
  } catch (error) {
    setStatus(error.message, "error")
    showToast(error.message, "error")
  }
}

async function loadSession(options = {}) {
  const { silent = false } = options
  const bfile = fileInput.value.trim() || fileSelect.value
  if (!bfile) {
    setStatus("Pick a filterbank first", "error")
    if (!silent) {
      showToast("Pick a filterbank first", "error")
    }
    return
  }

  setBusy("load")
  setStatus("Loading", "info")
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
    setStatus("Loaded", "success")
    if (!silent) {
      showToast("Session loaded", "success")
    }
  } catch (error) {
    setStatus(error.message, "error")
    if (!silent) {
      showToast(error.message, "error")
    }
  } finally {
    setBusy(null)
  }
}

async function postAction(type, payload = {}) {
  if (!state.sessionId) {
    setStatus("Load a session first", "error")
    showToast("Load a session first", "error")
    return
  }

  setBusy(type)
  setStatus(actionBusyText(type), "info")
  try {
    const response = await api(`/api/sessions/${state.sessionId}/actions`, {
      method: "POST",
      body: JSON.stringify({ type, payload }),
    })
    applyView(response.view)
    setStatus("Ready", "success")
    const message = actionSuccessText(type)
    if (message) {
      showToast(message, "success")
    }
  } catch (error) {
    setStatus(error.message, "error")
    showToast(error.message, "error")
  } finally {
    setBusy(null)
  }
}

function applyView(view) {
  state.view = view
  resolutionLabel.textContent = `t x${view.state.time_factor} / f x${view.state.freq_factor}`
  burstTitle.textContent = view.meta.burst_name
  burstSubtitle.textContent =
    `${fmt(view.meta.shape[0], 0)} channels x ${fmt(view.meta.shape[1], 0)} time bins. ` +
    `Refine the event window, burst regions, masking, and spectral extent directly in the viewer.`
  renderHero(view)
  renderSessionFacts(view)
  renderResults(view.results)
  renderDmOptimization(view)
  renderPlots(view)
  updateControlStates()
}

function renderHero(view) {
  const maskedCount = view.state.masked_channels.length
  const usesDetectedProfile = view.meta.preset_key === view.meta.detected_preset_key
  hero.classList.toggle("is-loaded", true)
  heroTags.innerHTML = [
    infoChip("Detected", view.meta.detected_telescope, "neutral"),
    infoChip("Using", view.meta.telescope, usesDetectedProfile ? "success" : "warning"),
    infoChip(usesDetectedProfile ? "Profile" : "Override", usesDetectedProfile ? "Auto" : "Manual", usesDetectedProfile ? "neutral" : "warning"),
  ].join("")
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
  sessionSummary.innerHTML = [
    summaryCard("Profile", view.meta.telescope),
    summaryCard("Detection", view.meta.detected_telescope),
    summaryCard("Crop", `${fmt(view.state.crop_ms[0], 2)} to ${fmt(view.state.crop_ms[1], 2)} ms`),
    summaryCard("Mask", `${view.state.masked_channels.length} channel${view.state.masked_channels.length === 1 ? "" : "s"}`),
  ].join("")
  sessionBadge.textContent = "Loaded"
  sessionBadge.dataset.tone = "success"

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

  const primaryTiles = [
    resultTile("Fluence", results.fluence_jyms === null ? "n/a" : `${fmt(results.fluence_jyms, 3)} Jy ms`, "primary"),
    resultTile("Peak Flux", results.peak_flux_jy === null ? "n/a" : `${fmt(results.peak_flux_jy, 3)} Jy`, "primary"),
    resultTile("Duration", `${fmt(results.event_duration_ms, 3)} ms`, "primary"),
  ]

  const secondaryTiles = [
    resultTile("MJD @ Peak", fmt(results.mjd_at_peak, 8), "secondary"),
    resultTile("Spectral Extent", `${fmt(results.spectral_extent_mhz, 2)} MHz`, "secondary"),
    resultTile("Mask Count", String(results.mask_count), "secondary"),
    resultTile("Peak Positions", results.peak_positions_ms.length ? results.peak_positions_ms.map((value) => `${fmt(value, 2)} ms`).join(", ") : "n/a", "secondary"),
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
    <div class="results-primary">
      ${primaryTiles.join("")}
    </div>
    <div class="results-secondary">
      ${secondaryTiles.join("")}
    </div>
    <details class="details-card results-details">
      <summary>Gaussian fit details (${results.gaussian_fits.length} region${results.gaussian_fits.length === 1 ? "" : "s"})</summary>
      <div class="results-fit-panel">
        ${fitList}
      </div>
    </details>
  `
}

function renderDmOptimization(view) {
  const optimization = view.dm_optimization
  if (!optimization) {
    dmOptimizeBadge.textContent = "Idle"
    dmOptimizeBadge.dataset.tone = "neutral"
    dmOptimizationContent.innerHTML =
      '<div class="empty-state">No DM sweep yet. Run Optimize DM to inspect the S/N-vs-DM curve.</div>'
    dmOptimizationPlot.classList.add("is-empty")
    Plotly.purge(dmOptimizationPlot)
    dmOptimizationPlot.replaceChildren()
    return
  }

  const fitTone = fitStatusTone(optimization.fit_status)
  dmOptimizeBadge.textContent = fitTone === "success" ? "Fit Ready" : "Sweep Ready"
  dmOptimizeBadge.dataset.tone = fitTone

  const primaryTiles = [
    resultTile("Best DM", fmt(optimization.best_dm, 6), "primary"),
    resultTile("Uncertainty", optimization.best_dm_uncertainty === null ? "n/a" : `±${fmt(optimization.best_dm_uncertainty, 6)}`, "primary"),
    resultTile("Best S/N", fmt(optimization.best_sn, 3), "primary"),
  ]

  const secondaryTiles = [
    resultTile("Sweep Center", fmt(optimization.center_dm, 6), "secondary"),
    resultTile("Sampled Best", fmt(optimization.sampled_best_dm, 6), "secondary"),
    resultTile("Sampled S/N", fmt(optimization.sampled_best_sn, 3), "secondary"),
    resultTile("Half-range", fmt(optimization.actual_half_range, 3), "secondary"),
    resultTile("Step", fmt(optimization.step, 3), "secondary"),
    resultTile("Applied DM", fmt(view.meta.dm, 6), "secondary"),
  ]

  dmOptimizationContent.innerHTML = `
    <div class="results-primary">
      ${primaryTiles.join("")}
    </div>
    <div class="results-secondary">
      ${secondaryTiles.join("")}
    </div>
    <div class="dm-fit-note" data-tone="${escapeHtml(fitTone)}">
      <strong>${escapeHtml(fitStatusLabel(optimization.fit_status))}</strong>
      <span>${escapeHtml(fitStatusCopy(optimization.fit_status))}</span>
    </div>
  `

  dmOptimizationPlot.classList.remove("is-empty")
  renderDmOptimizationPlot(optimization, view.meta.dm)
}

function resultTile(label, value, variant) {
  return `<div class="result-tile ${variant}"><span class="results-label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
}

async function renderPlots(view) {
  await Plotly.react(
    "viewerPlot",
    [
      {
        x: view.plot.time_profile.x_ms,
        y: view.plot.time_profile.y,
        xaxis: "x2",
        yaxis: "y2",
        mode: "lines",
        type: "scattergl",
        line: { color: "#162e3a", width: 2.2 },
        hovertemplate: "%{x:.3f} ms<br>%{y:.3f}<extra></extra>",
      },
      {
        x: view.plot.heatmap.x_ms,
        y: view.plot.heatmap.y_mhz,
        z: view.plot.heatmap.z,
        xaxis: "x",
        yaxis: "y",
        type: "heatmap",
        colorscale: "Viridis",
        zmin: view.plot.heatmap.zmin,
        zmax: view.plot.heatmap.zmax,
        hovertemplate: "%{x:.3f} ms<br>%{y:.3f} MHz<br>%{z:.3f}<extra></extra>",
        colorbar: {
          title: { text: "I" },
          x: 1.08,
          y: 0.39,
          len: 0.78,
          thickness: 14,
        },
      },
      {
        x: view.plot.spectrum.x,
        y: view.plot.spectrum.y_mhz,
        xaxis: "x3",
        yaxis: "y3",
        mode: "lines",
        type: "scattergl",
        line: { color: "#8b4513", width: 2.2 },
        hovertemplate: "%{x:.3f}<br>%{y:.3f} MHz<extra></extra>",
      },
    ],
    {
      margin: { l: 76, r: 118, t: 18, b: 54 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(255,255,255,0.55)",
      showlegend: false,
      hovermode: "closest",
      xaxis: {
        domain: viewerDomains.heatmap.x,
        anchor: "y",
        title: "Time (ms)",
        automargin: true,
      },
      yaxis: {
        domain: viewerDomains.heatmap.y,
        anchor: "x",
        title: "Frequency (MHz)",
        automargin: true,
      },
      xaxis2: {
        domain: viewerDomains.time.x,
        anchor: "y2",
        matches: "x",
        showticklabels: false,
        gridcolor: "rgba(24,33,38,0.08)",
      },
      yaxis2: {
        domain: viewerDomains.time.y,
        anchor: "x2",
        title: "Summed Intensity",
        automargin: true,
        gridcolor: "rgba(24,33,38,0.08)",
      },
      xaxis3: {
        domain: viewerDomains.spectrum.x,
        anchor: "y3",
        title: "Summed Intensity",
        automargin: true,
        gridcolor: "rgba(24,33,38,0.08)",
      },
      yaxis3: {
        domain: viewerDomains.spectrum.y,
        anchor: "x3",
        matches: "y",
        showticklabels: false,
        gridcolor: "rgba(24,33,38,0.08)",
      },
      shapes: buildViewerShapes(view),
      annotations: buildViewerAnnotations(),
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )

  bindPlotEvents()
}

async function renderDmOptimizationPlot(optimization, appliedDm) {
  const yRange = dmOptimizationYRange(optimization.snr)
  const traces = []

  if (optimization.best_dm_uncertainty !== null) {
    traces.push({
      x: [
        optimization.best_dm - optimization.best_dm_uncertainty,
        optimization.best_dm + optimization.best_dm_uncertainty,
        optimization.best_dm + optimization.best_dm_uncertainty,
        optimization.best_dm - optimization.best_dm_uncertainty,
      ],
      y: [yRange[0], yRange[0], yRange[1], yRange[1]],
      type: "scatter",
      mode: "lines",
      fill: "toself",
      line: { width: 0, color: "rgba(15, 118, 110, 0)" },
      fillcolor: "rgba(15, 118, 110, 0.12)",
      hoverinfo: "skip",
      showlegend: false,
    })
  }

  traces.push({
    x: optimization.trial_dms,
    y: optimization.snr,
    mode: "lines+markers",
    type: "scattergl",
    line: { color: "#0f766e", width: 2.5 },
    marker: { color: "#0c5f58", size: 8 },
    hovertemplate: "DM %{x:.6f}<br>S/N %{y:.3f}<extra></extra>",
    name: "S/N sweep",
  })

  await Plotly.react(
    "dmOptimizationPlot",
    traces,
    {
      margin: { l: 70, r: 24, t: 18, b: 54 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(255,255,255,0.55)",
      showlegend: false,
      hovermode: "closest",
      xaxis: {
        title: "Dispersion Measure",
        automargin: true,
        gridcolor: "rgba(24,33,38,0.08)",
      },
      yaxis: {
        title: "Event S/N",
        automargin: true,
        range: yRange,
        gridcolor: "rgba(24,33,38,0.08)",
      },
      shapes: buildDmOptimizationShapes(optimization, appliedDm, yRange),
      annotations: buildDmOptimizationAnnotations(optimization, yRange),
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )
}

function bindPlotEvents() {
  bindPlotEvent("viewerPlot", handleViewerPlotClick)
}

function bindPlotEvent(elementId, handler) {
  const plot = document.getElementById(elementId)
  if (plot.dataset.bound === "true") {
    return
  }
  plot.on("plotly_click", handler)
  plot.dataset.bound = "true"
}

function handleViewerPlotClick(event) {
  const point = event.points?.[0]
  if (!point) return
  const panel = panelFromPoint(point)

  if (panel === "time") {
    handleTimePlotClick(Number(point.x))
  } else if (panel === "heatmap") {
    handleFreqPlotClick(Number(point.y), false)
  } else if (panel === "spectrum") {
    handleFreqPlotClick(Number(point.y), true)
  }
}

function panelFromPoint(point) {
  const xaxisId = point.data?.xaxis || point.fullData?.xaxis || point.xaxis?._id || "x"
  const yaxisId = point.data?.yaxis || point.fullData?.yaxis || point.yaxis?._id || "y"

  if (xaxisId === "x2" || yaxisId === "y2") {
    return "time"
  }
  if (xaxisId === "x3" || yaxisId === "y3") {
    return "spectrum"
  }
  return "heatmap"
}

function handleTimePlotClick(timeMs) {
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

function handleFreqPlotClick(freqMHz, allowSpectralExtent) {
  if (state.mode === "mask-channel") {
    postAction("mask_channel", { freq_mhz: freqMHz })
  } else if (state.mode === "mask-range") {
    handlePair("mask_range", freqMHz, "MHz")
  } else if (state.mode === "spec-extent" && allowSpectralExtent) {
    handlePair("set_spectral_extent", freqMHz, "MHz")
  }
}

function handlePair(action, value, unit) {
  if (!state.pending || state.pending.action !== action) {
    state.pending = { action, value, unit }
    pendingBox.textContent = `First point set at ${fmt(value, 3)} ${unit}. Click the second point.`
    pendingBox.classList.add("is-active")
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
  pendingBox.classList.remove("is-active")
}

function setMode(mode) {
  state.mode = mode
  clearPending()
  modeChip.textContent = `Mode: ${modeLabels[mode]}`
  modeHelpEl.textContent = modeHelp[mode]
  modeButtons.forEach((button) => {
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

function buildViewerShapes(view) {
  return [...buildTimeShapes(view), ...buildHeatmapShapes(view), ...buildSpectrumShapes(view)]
}

function buildViewerAnnotations() {
  return [
    panelLabel("Time Profile", viewerDomains.time.x[0], viewerDomains.time.y[1]),
    panelLabel("Dynamic Spectrum", viewerDomains.heatmap.x[0], viewerDomains.heatmap.y[1]),
    panelLabel("Frequency Profile", viewerDomains.spectrum.x[0], viewerDomains.spectrum.y[1]),
  ]
}

function buildDmOptimizationShapes(optimization, appliedDm, yRange) {
  return [
    verticalLine(optimization.center_dm, yRange[0], yRange[1], "#475569", "dot"),
    verticalLine(optimization.sampled_best_dm, yRange[0], yRange[1], "#d97706", "dash"),
    verticalLine(optimization.best_dm, yRange[0], yRange[1], "#15803d", "solid"),
    verticalLine(appliedDm, yRange[0], yRange[1], "#b91c1c", "dot"),
  ]
}

function buildDmOptimizationAnnotations(optimization, yRange) {
  const y = yRange[1]
  return [
    dmOptimizationLabel("Center", optimization.center_dm, y, "#475569"),
    dmOptimizationLabel("Sampled", optimization.sampled_best_dm, y, "#d97706"),
    dmOptimizationLabel("Best", optimization.best_dm, y, "#15803d"),
  ]
}

function dmOptimizationLabel(text, x, y, color) {
  return {
    x,
    y,
    xref: "x",
    yref: "y",
    yanchor: "bottom",
    xanchor: "center",
    yshift: 4,
    showarrow: false,
    text: `<b>${escapeHtml(text)}</b>`,
    font: {
      family: '"IBM Plex Mono", monospace',
      size: 11,
      color,
    },
    bgcolor: "rgba(255,255,255,0.78)",
    bordercolor: "rgba(24, 33, 38, 0.08)",
    borderpad: 3,
  }
}

function panelLabel(text, x, y) {
  return {
    x,
    y,
    xref: "paper",
    yref: "paper",
    xanchor: "left",
    yanchor: "bottom",
    xshift: 4,
    yshift: 4,
    showarrow: false,
    text: `<b>${text}</b>`,
    font: {
      family: '"IBM Plex Mono", monospace',
      size: 11,
      color: "#5c6c73",
    },
  }
}

function buildTimeShapes(view) {
  const yMin = minFinite(view.plot.time_profile.y)
  const yMax = maxFinite(view.plot.time_profile.y)
  const shapes = [
    verticalLine(view.state.event_ms[0], yMin, yMax, "#d97706", "solid", "x2", "y2"),
    verticalLine(view.state.event_ms[1], yMin, yMax, "#d97706", "solid", "x2", "y2"),
  ]

  for (const region of view.state.burst_regions_ms) {
    shapes.push({
      type: "rect",
      xref: "x2",
      yref: "y2",
      x0: region[0],
      x1: region[1],
      y0: yMin,
      y1: yMax,
      fillcolor: "rgba(22, 163, 74, 0.14)",
      line: { width: 0 },
    })
  }

  for (const peak of view.state.peak_ms) {
    shapes.push(verticalLine(peak, yMin, yMax, "#dc2626", "dot", "x2", "y2"))
  }
  return shapes
}

function buildHeatmapShapes(view) {
  const x0 = view.state.crop_ms[0]
  const x1 = view.state.crop_ms[1]
  const y0 = view.meta.freq_range_mhz[0]
  const y1 = view.meta.freq_range_mhz[1]
  const shapes = [
    verticalLine(view.state.event_ms[0], y0, y1, "#d97706", "solid", "x", "y"),
    verticalLine(view.state.event_ms[1], y0, y1, "#d97706", "solid", "x", "y"),
    horizontalLine(view.state.spectral_extent_mhz[0], x0, x1, "#7c3aed", "solid", "x", "y"),
    horizontalLine(view.state.spectral_extent_mhz[1], x0, x1, "#7c3aed", "solid", "x", "y"),
  ]

  for (const peak of view.state.peak_ms) {
    shapes.push(verticalLine(peak, y0, y1, "#dc2626", "dot", "x", "y"))
  }
  return shapes
}

function buildSpectrumShapes(view) {
  const xMin = minFinite(view.plot.spectrum.x)
  const xMax = maxFinite(view.plot.spectrum.x)
  return [
    horizontalLine(view.state.spectral_extent_mhz[0], xMin, xMax, "#7c3aed", "solid", "x3", "y3"),
    horizontalLine(view.state.spectral_extent_mhz[1], xMin, xMax, "#7c3aed", "solid", "x3", "y3"),
  ]
}

function verticalLine(x, y0, y1, color, dash = "solid", xref = "x", yref = "y") {
  return {
    type: "line",
    xref,
    yref,
    x0: x,
    x1: x,
    y0,
    y1,
    line: { color, width: 2, dash },
  }
}

function horizontalLine(y, x0, x1, color, dash = "solid", xref = "x", yref = "y") {
  return {
    type: "line",
    xref,
    yref,
    x0,
    x1,
    y0: y,
    y1: y,
    line: { color, width: 2, dash },
  }
}

function rememberButtonLabels() {
  document.querySelectorAll("button").forEach((button) => {
    button.dataset.idleText = button.textContent
  })
}

function setBusy(action) {
  state.busyAction = action
  updateControlStates()
}

function updateControlStates() {
  const hasSession = Boolean(state.sessionId && state.view)
  const isBusy = Boolean(state.busyAction)
  const hasBurstPath = Boolean(fileInput.value.trim() || fileSelect.value)
  const hasBestDm = Number.isFinite(Number(state.view?.dm_optimization?.best_dm))

  for (const control of sessionControls) {
    control.disabled = !hasSession || isBusy
  }

  for (const control of busyLockControls) {
    control.disabled = isBusy
  }

  loadButton.disabled = isBusy || !hasBurstPath
  applyBestDmButton.disabled = !hasSession || isBusy || !hasBestDm
  hero.classList.toggle("is-loaded", hasSession)

  if (!hasSession) {
    sessionBadge.textContent = "Not loaded"
    sessionBadge.dataset.tone = "neutral"
    dmOptimizeBadge.textContent = "Idle"
    dmOptimizeBadge.dataset.tone = "neutral"
  }

  syncBusyButtons()
}

function syncBusyButtons() {
  document.querySelectorAll("button").forEach((button) => {
    button.classList.remove("is-busy")
    if (button.dataset.idleText) {
      button.textContent = button.dataset.idleText
    }
  })

  const button = busyButtonForAction(state.busyAction)
  if (!button) {
    return
  }

  button.classList.add("is-busy")
  button.textContent = busyButtonText(state.busyAction)
}

function busyButtonForAction(action) {
  if (action === "load") return loadButton
  if (action === "compute_properties") return computeButton
  if (action === "auto_mask_jess") return jessButton
  if (action === "optimize_dm") return optimizeDmButton
  if (action === "set_dm") return setDmButton
  if (action === "reset_view") return resetViewButton
  return null
}

function busyButtonText(action) {
  if (action === "load") return "Loading..."
  if (action === "compute_properties") return "Computing..."
  if (action === "auto_mask_jess") return "Masking..."
  if (action === "optimize_dm") return "Sweeping DM..."
  if (action === "set_dm") return "Applying DM..."
  if (action === "reset_view") return "Resetting..."
  return actionBusyText(action)
}

function actionBusyText(action) {
  const labels = {
    time_factor: "Updating resolution",
    freq_factor: "Updating resolution",
    reset_view: "Resetting view",
    set_crop: "Updating crop window",
    set_event: "Updating event window",
    add_region: "Adding burst region",
    clear_regions: "Clearing regions",
    add_peak: "Adding peak",
    remove_peak: "Removing peak",
    mask_channel: "Masking channel",
    mask_range: "Masking range",
    undo_mask: "Undoing mask",
    reset_mask: "Resetting masks",
    set_spectral_extent: "Setting spectral extent",
    auto_mask_jess: "Auto masking",
    optimize_dm: "Sweeping DM",
    set_dm: "Applying DM",
    compute_properties: "Computing",
  }
  return labels[action] || "Updating"
}

function actionSuccessText(action) {
  const labels = {
    reset_view: "View reset",
    clear_regions: "Burst regions cleared",
    undo_mask: "Last mask removed",
    reset_mask: "All masks cleared",
    auto_mask_jess: "Auto mask applied",
    optimize_dm: "DM sweep completed",
    set_dm: "Dispersion measure updated",
    compute_properties: "Derived properties updated",
  }
  return labels[action] || null
}

function showToast(message, tone = "info") {
  if (!toastStack || !message) {
    return
  }

  const toast = document.createElement("div")
  toast.className = "toast"
  toast.dataset.tone = tone
  toast.textContent = String(message)
  toastStack.appendChild(toast)

  window.requestAnimationFrame(() => {
    toast.classList.add("is-visible")
  })

  window.setTimeout(() => {
    toast.classList.remove("is-visible")
    toast.classList.add("is-leaving")
  }, 3200)

  window.setTimeout(() => {
    toast.remove()
  }, 3600)

  while (toastStack.children.length > 4) {
    toastStack.firstElementChild?.remove()
  }
}

function setStatus(text, tone = "info") {
  statusChip.textContent = text
  statusChip.dataset.tone = tone
}

function setPresetSelection(presetKey) {
  syncingPresetSelection = true
  telescopeInput.value = presetKey
  syncingPresetSelection = false
  syncPresetDefaults()
}

function renderDetectionHint(errorMessage = null) {
  if (errorMessage) {
    detectionHint.innerHTML = `
      <div class="badge-row">
        ${infoChip("Detection", "Error", "error")}
      </div>
      <div class="detection-copy">Detection error: ${escapeHtml(errorMessage)}</div>
    `
    return
  }

  if (!state.detection) {
    detectionHint.innerHTML = `
      <div class="badge-row">
        ${infoChip("Detection", "Waiting", "neutral")}
      </div>
      <div class="detection-copy">Select or enter a filterbank path to inspect its telescope metadata.</div>
    `
    return
  }

  const detectedLabel = state.detection.detected_preset_label
  const selectedLabel = presetDefaults.get(telescopeInput.value)?.label || telescopeInput.value
  const overrideActive = state.userSelectedPreset && telescopeInput.value !== state.detection.detected_preset_key
  const detectedTone = state.detection.detected_preset_key === "generic" ? "warning" : "success"
  const selectedTone = overrideActive ? "warning" : "success"
  let copy = `${escapeHtml(state.detection.detection_basis)}. ${overrideActive ? "Manual override active." : "You can override this before loading."}`

  if (state.detection.detected_preset_key === "generic") {
    copy =
      `No known telescope match (${escapeHtml(state.detection.detection_basis)}). ` +
      `${overrideActive ? `Manual preset selected: ${escapeHtml(selectedLabel)}.` : "Generic Filterbank will be used by default."}`
  }

  detectionHint.innerHTML = `
    <div class="badge-row">
      ${infoChip("Detected", detectedLabel, detectedTone)}
      ${infoChip("Using", selectedLabel, selectedTone)}
      ${infoChip("Mode", overrideActive ? "Manual override" : "Auto profile", overrideActive ? "warning" : "neutral")}
    </div>
    <div class="detection-copy">${copy}</div>
  `
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

function infoChip(label, value, tone = "neutral") {
  return (
    `<span class="info-chip" data-tone="${escapeHtml(tone)}">` +
    `<span>${escapeHtml(label)}</span>` +
    `<strong>${escapeHtml(String(value))}</strong>` +
    `</span>`
  )
}

function summaryCard(label, value) {
  return (
    `<div class="summary-card">` +
    `<span>${escapeHtml(label)}</span>` +
    `<strong>${escapeHtml(String(value))}</strong>` +
    `</div>`
  )
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

function dmOptimizationYRange(values) {
  const yMin = minFinite(values)
  const yMax = maxFinite(values)
  const span = yMax - yMin
  const pad = span > 0 ? span * 0.12 : Math.max(0.5, Math.abs(yMax) * 0.12 || 0.5)
  return [yMin - pad, yMax + pad]
}

function fitStatusTone(status) {
  if (status === "quadratic_peak_fit") return "success"
  if (status === "quadratic_peak_fit_uncertainty_unavailable") return "warning"
  return "warning"
}

function fitStatusLabel(status) {
  const labels = {
    quadratic_peak_fit: "Quadratic peak fit accepted",
    quadratic_peak_fit_uncertainty_unavailable: "Quadratic fit accepted without uncertainty",
    peak_on_sweep_edge: "Sampled best DM is on the sweep edge",
    insufficient_peak_window: "Peak window is too sparse for a fit",
    quadratic_fit_failed: "Quadratic fit failed",
    quadratic_not_concave: "Local peak fit is not concave",
    fit_vertex_outside_peak_window: "Fitted peak moved outside the local window",
  }
  return labels[status] || "DM sweep completed"
}

function fitStatusCopy(status) {
  const labels = {
    quadratic_peak_fit: "The fitted best DM and uncertainty come from a local quadratic model around the sampled peak.",
    quadratic_peak_fit_uncertainty_unavailable: "The local fit found a best DM, but the sampled range was not sufficient to derive a stable uncertainty band.",
    peak_on_sweep_edge: "Expand the DM half-range so the peak is bracketed before trusting the best-DM estimate.",
    insufficient_peak_window: "Use a smaller step or a broader sweep so the local peak has enough neighboring samples for a fit.",
    quadratic_fit_failed: "The sampled S/N curve could not support a stable local quadratic fit.",
    quadratic_not_concave: "The sampled S/N curve near the discrete peak does not resemble a local maximum.",
    fit_vertex_outside_peak_window: "The local quadratic fit shifted the maximum outside the sampled peak window, so the discrete best DM was retained.",
  }
  return labels[status] || "Review the sampled S/N curve before applying a new DM."
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
