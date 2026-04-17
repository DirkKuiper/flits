const state = {
  sessionId: null,
  view: null,
  exportManifest: null,
  exportPreview: null,
  exportPreviewStale: false,
  exportPreviewPending: false,
  exportPreviewError: null,
  exportSelection: {
    include: [],
    plot_formats: [],
    window_formats: [],
    window_resolutions: [],
  },
  mode: "event",
  activeAnalysisTab: "prepare",
  pending: null,
  detection: null,
  userSelectedPreset: false,
  busyAction: null,
  knownFileDirectories: [],
}

const modeLabels = {
  event: "Event Window",
  crop: "Crop Window",
  offpulse: "Off-Pulse",
  region: "Component Region",
  "add-peak": "Add Peak",
  "remove-peak": "Remove Peak",
  "mask-channel": "Mask Channel",
  "mask-range": "Mask Range",
  "spec-extent": "Spectral Window",
}

const modeHelp = {
  event: "Click twice on the top time profile or center dynamic spectrum to mark the start and end of the event window.",
  crop: "Click twice on the top time profile to define the crop window you want to work in.",
  offpulse: "Click twice on the top time profile to define an explicit off-pulse window for noise estimation.",
  region: "Click twice on the top time profile to add an optional component region for Gaussian/component-DM work.",
  "add-peak": "Click once on the top time profile to place a peak marker.",
  "remove-peak": "Click once near an existing peak on the top time profile to remove it.",
  "mask-channel": "Click once on the center dynamic spectrum or right frequency profile to mask a single frequency channel.",
  "mask-range": "Click twice on the center dynamic spectrum or right frequency profile to mask a contiguous frequency range.",
  "spec-extent": "Click twice on the center dynamic spectrum or right frequency profile to set the spectral window used for measurements.",
}

const statusChip = document.getElementById("statusChip")
const modeChip = document.getElementById("modeChip")
const modeHelpEl = document.getElementById("modeHelp")
const pendingBox = document.getElementById("pendingBox")
const loadButton = document.getElementById("loadButton")
const directorySelect = document.getElementById("directorySelect")
const fileSelect = document.getElementById("fileSelect")
const fileInput = document.getElementById("fileInput")
const dmInput = document.getElementById("dmInput")
const telescopeInput = document.getElementById("telescopeInput")
const sefdInput = document.getElementById("sefdInput")
const npolInput = document.getElementById("npolInput")
const readStartInput = document.getElementById("readStartInput")
const readEndInput = document.getElementById("readEndInput")
const distanceInput = document.getElementById("distanceInput")
const redshiftInput = document.getElementById("redshiftInput")
const autoMaskProfileInput = document.getElementById("autoMaskProfileInput")
const dmMetricInput = document.getElementById("dmMetricInput")
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
const dmResidualContent = document.getElementById("dmResidualContent")
const dmResidualPlot = document.getElementById("dmResidualPlot")
const dmComponentsContent = document.getElementById("dmComponentsContent")
const fittingContent = document.getElementById("fittingContent")
const fittingSpectrumPlot = document.getElementById("fittingSpectrumPlot")
const fittingProfilePlot = document.getElementById("fittingProfilePlot")
const fitComponentsInput = document.getElementById("fitComponentsInput")
const fitFixedParamsContainer = document.getElementById("fitFixedParamsContainer")
const spectralContent = document.getElementById("spectralContent")
const temporalScalePlot = document.getElementById("temporalScalePlot")
const spectralPlot = document.getElementById("spectralPlot")
const spectralSegmentInput = document.getElementById("spectralSegmentInput")
const runSpectralButton = document.getElementById("runSpectralButton")
const exportIncludeJson = document.getElementById("exportIncludeJson")
const exportIncludeCsv = document.getElementById("exportIncludeCsv")
const exportIncludeNpz = document.getElementById("exportIncludeNpz")
const exportIncludePlots = document.getElementById("exportIncludePlots")
const exportIncludeWindow = document.getElementById("exportIncludeWindow")
const exportPlotFormatSection = document.getElementById("exportPlotFormatSection")
const exportPlotPng = document.getElementById("exportPlotPng")
const exportPlotSvg = document.getElementById("exportPlotSvg")
const exportWindowFormatSection = document.getElementById("exportWindowFormatSection")
const exportWindowResolutionSection = document.getElementById("exportWindowResolutionSection")
const exportWindowNpz = document.getElementById("exportWindowNpz")
const exportWindowFil = document.getElementById("exportWindowFil")
const exportWindowNative = document.getElementById("exportWindowNative")
const exportWindowView = document.getElementById("exportWindowView")
const exportSelectionSummary = document.getElementById("exportSelectionSummary")
const exportPreviewMeta = document.getElementById("exportPreviewMeta")
const exportPreviewThumbs = document.getElementById("exportPreviewThumbs")
const exportPreviewContent = document.getElementById("exportPreviewContent")
const exportManifestContent = document.getElementById("exportManifestContent")
const exportSessionButton = document.getElementById("exportSessionButton")
const importSessionInput = document.getElementById("importSessionInput")
const notesInput = document.getElementById("notesInput")
const saveNotesButton = document.getElementById("saveNotesButton")
const setDmButton = document.getElementById("setDmButton")
const optimizeDmButton = document.getElementById("optimizeDmButton")
const applyBestDmButton = document.getElementById("applyBestDmButton")
const fitScatteringButton = document.getElementById("fitScatteringButton")
const buildExportButton = document.getElementById("buildExportButton")
const resetViewButton = document.getElementById("resetViewButton")
const clearRegionsButton = document.getElementById("clearRegionsButton")
const clearOffpulseButton = document.getElementById("clearOffpulseButton")
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
let exportPreviewTimer = null
let exportPreviewRequestId = 0
const ROOT_DIRECTORY_VALUE = "__flits_root__"
const viewerDomains = {
  heatmap: { x: [0.0, 0.78], y: [0.0, 0.78] },
  time: { x: [0.0, 0.78], y: [0.82, 1.0] },
  spectrum: { x: [0.82, 1.0], y: [0.0, 0.78] },
}
const plotTheme = Object.freeze({
  ink: "#323232",
  muted: "#6A6A6A",
  accent: "#7235a2",
  accentStrong: "#5f2b88",
  accentAlt: "#327cbc",
  accentAltStrong: "#285f93",
  accentSoft: "rgba(114, 53, 162, 0.12)",
  accentAltSoft: "rgba(50, 124, 188, 0.14)",
  warning: "#8e6ebd",
  warningSoft: "rgba(142, 110, 189, 0.14)",
  alert: "#a23b61",
  neutral: "#6A6A6A",
  neutralSoft: "#7d8290",
  charcoal: "#191919",
  charcoalSoft: "#2e2e2e",
  paperBg: "rgba(0,0,0,0)",
  plotBg: "rgba(255,255,255,0.72)",
  grid: "rgba(50,50,50,0.08)",
  gridStrong: "rgba(50,50,50,0.15)",
  annotationBg: "rgba(255,255,255,0.84)",
  annotationBorder: "rgba(50,50,50,0.1)",
  heatmapScale: [
    [0.0, "#191919"],
    [0.16, "#2e2e2e"],
    [0.42, "#4d326e"],
    [0.68, "#7235a2"],
    [0.86, "#b287d0"],
    [1.0, "#f3eafb"],
  ],
  residualScale: [
    [0.0, "#2e2e2e"],
    [0.35, "#7d8290"],
    [0.5, "#fbfbfd"],
    [0.65, "#c5abd8"],
    [1.0, "#7235a2"],
  ],
})
const modeButtons = Array.from(document.querySelectorAll(".mode-button"))
const analysisTabButtons = Array.from(document.querySelectorAll("[data-analysis-tab]"))
const analysisPanels = Array.from(document.querySelectorAll("[data-tab-panel]"))
const sessionControls = [
  setDmButton,
  optimizeDmButton,
  applyBestDmButton,
  fitScatteringButton,
  runSpectralButton,
  buildExportButton,
  exportIncludeJson,
  exportIncludeCsv,
  exportIncludeNpz,
  exportIncludePlots,
  exportIncludeWindow,
  exportPlotPng,
  exportPlotSvg,
  exportWindowNpz,
  exportWindowFil,
  exportWindowNative,
  exportWindowView,
  exportSessionButton,
  saveNotesButton,
  resetViewButton,
  clearRegionsButton,
  clearOffpulseButton,
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
  directorySelect,
  fileSelect,
  fileInput,
  importSessionInput,
  dmInput,
  telescopeInput,
  sefdInput,
  readStartInput,
  readEndInput,
  distanceInput,
  redshiftInput,
  autoMaskProfileInput,
  dmMetricInput,
  notesInput,
  dmHalfRangeInput,
  dmStepInput,
  spectralSegmentInput,
  fitComponentsInput,
]

document.addEventListener("DOMContentLoaded", async () => {
  rememberButtonLabels()
  bindControls()
  setMode(state.mode)
  setAnalysisTab(initialAnalysisTab())
  setStatus("Idle", "neutral")
  updateControlStates()
  await loadPresets()
  await loadAutoMaskProfiles()
  await loadFiles()
  // Removed auto-loading logic. Waiting for explicit user action.
})

function initialAnalysisTab() {
  const fromHash = window.location.hash.replace(/^#/, "").trim().toLowerCase()
  const valid = new Set(["prepare", "dm", "fitting", "temporal", "export"])
  return valid.has(fromHash) ? fromHash : state.activeAnalysisTab
}

function bindControls() {
  fileInput.addEventListener("input", () => {
    syncKnownFileSelection(fileInput.value.trim())
    updateControlStates()
  })

  directorySelect.addEventListener("change", () => {
    renderFileOptions(directoryPathFromValue(directorySelect.value), { resetSelection: true })
    fileInput.value = ""
    state.detection = null
    renderDetectionHint()
    updateControlStates()
  })

  fileSelect.addEventListener("change", async () => {
    fileInput.value = fileSelect.value
    syncKnownFileSelection(fileSelect.value)
    state.userSelectedPreset = false
    await detectSelectedFile()
    updateControlStates()
  })

  fileInput.addEventListener("change", async () => {
    syncKnownFileSelection(fileInput.value.trim())
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
  exportSessionButton.addEventListener("click", () => downloadSessionSnapshot())
  importSessionInput.addEventListener("change", (event) => importSessionSnapshot(event))
  dmMetricInput.addEventListener("change", () => {
    if (state.view) {
      renderDmOptimization(state.view)
    }
  })
  saveNotesButton.addEventListener("click", () => {
    postAction("set_notes", { notes: notesInput.value })
  })
  setDmButton.addEventListener("click", () => {
    postAction("set_dm", { dm: Number(dmInput.value) })
  })
  optimizeDmButton.addEventListener("click", () => {
    postAction("optimize_dm", {
      center_dm: Number(dmInput.value),
      half_range: Number(dmHalfRangeInput.value),
      step: Number(dmStepInput.value),
      metric: dmMetricInput.value || "integrated_event_snr",
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
  fitScatteringButton.addEventListener("click", () => {
    const checkboxes = document.querySelectorAll("#fitFixedParamsContainer input[type='checkbox']:checked");
    const fixedParams = Array.from(checkboxes).map((cb) => cb.value);
    const numComponents = Number(fitComponentsInput.value) || 1;
    postAction("fit_scattering", {
      fixed_parameters: fixedParams,
      num_components: numComponents,
    });
  })
  runSpectralButton.addEventListener("click", () => {
    postAction("run_temporal_structure_analysis", {
      segment_length_ms: Number(spectralSegmentInput.value),
    })
  })
  exportIncludeJson.addEventListener("change", () => updateExportSelection("json", exportIncludeJson.checked))
  exportIncludeCsv.addEventListener("change", () => updateExportSelection("csv", exportIncludeCsv.checked))
  exportIncludeNpz.addEventListener("change", () => updateExportSelection("npz", exportIncludeNpz.checked))
  exportIncludePlots.addEventListener("change", () => updateExportSelection("plots", exportIncludePlots.checked))
  exportIncludeWindow.addEventListener("change", () => updateExportSelection("window", exportIncludeWindow.checked))
  exportPlotPng.addEventListener("change", () => updateExportPlotFormat("png", exportPlotPng.checked))
  exportPlotSvg.addEventListener("change", () => updateExportPlotFormat("svg", exportPlotSvg.checked))
  exportWindowNpz.addEventListener("change", () => updateExportWindowFormat("npz", exportWindowNpz.checked))
  exportWindowFil.addEventListener("change", () => updateExportWindowFormat("fil", exportWindowFil.checked))
  exportWindowNative.addEventListener("change", () => updateExportWindowResolution("native", exportWindowNative.checked))
  exportWindowView.addEventListener("change", () => updateExportWindowResolution("view", exportWindowView.checked))
  buildExportButton.addEventListener("click", () => postAction("export_results", exportRequestPayload()))
  resetViewButton.addEventListener("click", () => postAction("reset_view"))
  clearRegionsButton.addEventListener("click", () => postAction("clear_regions"))
  clearOffpulseButton.addEventListener("click", () => postAction("clear_offpulse"))
  computeButton.addEventListener("click", () => postAction("compute_properties"))
  undoMaskButton.addEventListener("click", () => postAction("undo_mask"))
  resetMaskButton.addEventListener("click", () => postAction("reset_mask"))
  jessButton.addEventListener("click", () =>
    postAction("auto_mask_jess", { profile: autoMaskProfileInput.value || "auto" }),
  )

  timeDownButton.addEventListener("click", () => scaleFactor("time", 0.5))
  timeUpButton.addEventListener("click", () => scaleFactor("time", 2))
  freqDownButton.addEventListener("click", () => scaleFactor("freq", 0.5))
  freqUpButton.addEventListener("click", () => scaleFactor("freq", 2))

  modeButtons.forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode))
  })

  analysisTabButtons.forEach((button) => {
    button.addEventListener("click", () => setAnalysisTab(button.dataset.analysisTab))
  })
}

function exportRequestPayload() {
  const includeOrder = ["json", "csv", "npz", "plots", "window"]
  const formatOrder = ["png", "svg"]
  const windowFormatOrder = ["npz", "fil"]
  const windowResolutionOrder = ["native", "view"]
  return {
    include: includeOrder.filter((item) => state.exportSelection.include.includes(item)),
    plot_formats: formatOrder.filter((item) => state.exportSelection.plot_formats.includes(item)),
    window_formats: windowFormatOrder.filter((item) => state.exportSelection.window_formats.includes(item)),
    window_resolutions: windowResolutionOrder.filter((item) => state.exportSelection.window_resolutions.includes(item)),
  }
}

function resetExportSelection() {
  state.exportSelection = {
    include: [],
    plot_formats: [],
    window_formats: [],
    window_resolutions: [],
  }
  state.exportPreview = null
  state.exportPreviewStale = false
  state.exportPreviewPending = false
  state.exportPreviewError = null
  if (exportPreviewTimer) {
    window.clearTimeout(exportPreviewTimer)
    exportPreviewTimer = null
  }
  syncExportPlannerControls()
}

function updateExportSelection(kind, enabled) {
  const include = new Set(state.exportSelection.include)
  if (enabled) {
    include.add(kind)
  } else {
    include.delete(kind)
  }

  const plotFormats = new Set(state.exportSelection.plot_formats)
  if (kind === "plots") {
    if (enabled && plotFormats.size === 0) {
      plotFormats.add("png")
    }
    if (!enabled) {
      plotFormats.clear()
    }
  }

  const windowFormats = new Set(state.exportSelection.window_formats)
  const windowResolutions = new Set(state.exportSelection.window_resolutions)
  if (kind === "window") {
    if (enabled && windowFormats.size === 0) {
      windowFormats.add("npz")
    }
    if (enabled && windowResolutions.size === 0) {
      windowResolutions.add("native")
    }
    if (!enabled) {
      windowFormats.clear()
      windowResolutions.clear()
    }
  }

  state.exportSelection = {
    include: Array.from(include),
    plot_formats: Array.from(plotFormats),
    window_formats: Array.from(windowFormats),
    window_resolutions: Array.from(windowResolutions),
  }
  state.exportPreviewStale = false
  state.exportPreviewError = null
  syncExportPlannerControls()
  scheduleExportPreview()
}

function updateExportPlotFormat(format, enabled) {
  const plotFormats = new Set(state.exportSelection.plot_formats)
  if (enabled) {
    plotFormats.add(format)
  } else {
    plotFormats.delete(format)
  }

  state.exportSelection = {
    include: state.exportSelection.include.filter((item) => item !== "plots" || plotFormats.size > 0),
    plot_formats: Array.from(plotFormats),
    window_formats: Array.from(state.exportSelection.window_formats),
    window_resolutions: Array.from(state.exportSelection.window_resolutions),
  }
  state.exportPreviewStale = false
  state.exportPreviewError = null
  syncExportPlannerControls()
  scheduleExportPreview()
}

function updateExportWindowFormat(format, enabled) {
  const windowFormats = new Set(state.exportSelection.window_formats)
  if (enabled) {
    windowFormats.add(format)
  } else {
    windowFormats.delete(format)
  }

  const include = state.exportSelection.include.filter((item) =>
    item !== "window" || (windowFormats.size > 0 && state.exportSelection.window_resolutions.length > 0),
  )

  state.exportSelection = {
    include,
    plot_formats: Array.from(state.exportSelection.plot_formats),
    window_formats: Array.from(windowFormats),
    window_resolutions: Array.from(state.exportSelection.window_resolutions),
  }
  state.exportPreviewStale = false
  state.exportPreviewError = null
  syncExportPlannerControls()
  scheduleExportPreview()
}

function updateExportWindowResolution(mode, enabled) {
  const windowResolutions = new Set(state.exportSelection.window_resolutions)
  if (enabled) {
    windowResolutions.add(mode)
  } else {
    windowResolutions.delete(mode)
  }

  const include = state.exportSelection.include.filter((item) =>
    item !== "window" || (state.exportSelection.window_formats.length > 0 && windowResolutions.size > 0),
  )

  state.exportSelection = {
    include,
    plot_formats: Array.from(state.exportSelection.plot_formats),
    window_formats: Array.from(state.exportSelection.window_formats),
    window_resolutions: Array.from(windowResolutions),
  }
  state.exportPreviewStale = false
  state.exportPreviewError = null
  syncExportPlannerControls()
  scheduleExportPreview()
}

function syncExportPlannerControls() {
  const include = new Set(state.exportSelection.include)
  const plotFormats = new Set(state.exportSelection.plot_formats)
  const windowFormats = new Set(state.exportSelection.window_formats)
  const windowResolutions = new Set(state.exportSelection.window_resolutions)
  exportIncludeJson.checked = include.has("json")
  exportIncludeCsv.checked = include.has("csv")
  exportIncludeNpz.checked = include.has("npz")
  exportIncludePlots.checked = include.has("plots")
  exportIncludeWindow.checked = include.has("window")
  exportPlotPng.checked = plotFormats.has("png")
  exportPlotSvg.checked = plotFormats.has("svg")
  exportWindowNpz.checked = windowFormats.has("npz")
  exportWindowFil.checked = windowFormats.has("fil")
  exportWindowNative.checked = windowResolutions.has("native")
  exportWindowView.checked = windowResolutions.has("view")
  exportPlotFormatSection.hidden = !include.has("plots")
  exportWindowFormatSection.hidden = !include.has("window")
  exportWindowResolutionSection.hidden = !include.has("window")
}

function exportSelectionCount() {
  return state.exportSelection.include.length
}

function markExportPreviewStale() {
  if (!state.exportPreview && exportSelectionCount() === 0) {
    return
  }
  state.exportPreviewStale = true
  state.exportPreviewPending = false
  state.exportPreviewError = null
  if (exportPreviewTimer) {
    window.clearTimeout(exportPreviewTimer)
    exportPreviewTimer = null
  }
}

function scheduleExportPreview({ immediate = false } = {}) {
  if (exportPreviewTimer) {
    window.clearTimeout(exportPreviewTimer)
    exportPreviewTimer = null
  }

  if (!state.sessionId || exportSelectionCount() === 0) {
    state.exportPreview = null
    state.exportPreviewStale = false
    state.exportPreviewPending = false
    state.exportPreviewError = null
    renderExportPlanner()
    updateControlStates()
    return
  }

  state.exportPreviewPending = true
  state.exportPreviewError = null
  renderExportPlanner()
  const delay = immediate ? 0 : 220
  exportPreviewTimer = window.setTimeout(() => {
    requestExportPreview()
  }, delay)
}

async function requestExportPreview() {
  if (!state.sessionId || exportSelectionCount() === 0) {
    return
  }

  const requestId = ++exportPreviewRequestId
  state.exportPreviewPending = true
  renderExportPlanner()
  try {
    const response = await api(`/api/sessions/${state.sessionId}/actions`, {
      method: "POST",
      body: JSON.stringify({ type: "preview_export_results", payload: exportRequestPayload() }),
    })
    if (requestId !== exportPreviewRequestId) {
      return
    }
    state.exportPreview = response.export_preview || null
    state.exportPreviewPending = false
    state.exportPreviewStale = false
    state.exportPreviewError = null
    renderExportPlanner()
    updateControlStates()
  } catch (error) {
    if (requestId !== exportPreviewRequestId) {
      return
    }
    state.exportPreviewPending = false
    state.exportPreviewStale = true
    state.exportPreviewError = error.message
    renderExportPlanner()
  }
}

async function downloadSessionSnapshot() {
  if (!state.sessionId) {
    setStatus("Load a session first", "error")
    showToast("Load a session first", "error")
    return
  }

  setBusy("export_session")
  setStatus("Exporting session", "info")
  try {
    const response = await fetch(`/api/sessions/${state.sessionId}/snapshot`)
    if (!response.ok) {
      const payload = await response.json()
      throw new Error(payload.detail || "Snapshot export failed")
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    const disposition = response.headers.get("Content-Disposition") || ""
    const match = disposition.match(/filename="([^"]+)"/)
    link.href = url
    link.download = match ? match[1] : "flits_session.json"
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    setStatus("Session exported", "success")
    showToast("Session snapshot exported", "success")
  } catch (error) {
    setStatus(error.message, "error")
    showToast(error.message, "error")
  } finally {
    setBusy(null)
  }
}

async function importSessionSnapshot(event) {
  const file = event.target.files?.[0]
  if (!file) {
    return
  }

  const previousSessionId = state.sessionId
  setBusy("import_session")
  setStatus("Importing session", "info")
  clearPending()
  try {
    const text = await file.text()
    const snapshot = JSON.parse(text)
    const payload = await api("/api/sessions/import", {
      method: "POST",
      body: JSON.stringify({ snapshot }),
    })
    state.sessionId = payload.session_id
    state.exportManifest = null
    resetExportSelection()
    applyView(payload.view)
    if (previousSessionId && previousSessionId !== payload.session_id) {
      try {
        await api(`/api/sessions/${previousSessionId}`, { method: "DELETE" })
      } catch (cleanupError) {
        console.warn("Failed to delete previous session", cleanupError)
      }
    }
    setStatus("Imported", "success")
    showToast("Session imported", "success")
  } catch (error) {
    setStatus(error.message, "error")
    showToast(error.message, "error")
  } finally {
    importSessionInput.value = ""
    setBusy(null)
  }
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

async function loadAutoMaskProfiles() {
  try {
    const payload = await api("/api/auto-mask-profiles")
    autoMaskProfileInput.innerHTML = ""
    for (const profile of payload.profiles) {
      const option = document.createElement("option")
      option.value = profile.key
      option.textContent = profile.label
      option.title = `${profile.description} (${profile.memory_budget_mb} MB budget)`
      autoMaskProfileInput.appendChild(option)
    }
    autoMaskProfileInput.value = payload.profiles.some((profile) => profile.key === "auto")
      ? "auto"
      : payload.profiles[0]?.key || ""
  } catch (error) {
    setStatus(error.message, "error")
    showToast(error.message, "error")
  }
}

function normalizeKnownFilePath(path) {
  const normalized = String(path || "").trim().replaceAll("\\", "/")
  if (!normalized || normalized.startsWith("/") || /^[A-Za-z]:\//.test(normalized)) {
    return normalized
  }
  return normalized.replace(/^\.\//, "")
}

function directoryOptionValue(path) {
  return path === "" ? ROOT_DIRECTORY_VALUE : path
}

function directoryPathFromValue(value) {
  return value === ROOT_DIRECTORY_VALUE ? "" : value
}

function findKnownDirectory(path) {
  return state.knownFileDirectories.find((directory) => directory.path === path) || null
}

function groupKnownFiles(files) {
  const grouped = new Map()
  for (const rawPath of files) {
    const path = normalizeKnownFilePath(rawPath)
    const segments = path.split("/")
    const name = segments[segments.length - 1]
    const directoryPath = segments.length > 1 ? segments.slice(0, -1).join("/") : ""
    if (!grouped.has(directoryPath)) {
      grouped.set(directoryPath, [])
    }
    grouped.get(directoryPath).push({ path, name })
  }

  return Array.from(grouped.entries())
    .sort(([left], [right]) => {
      if (left === "") return -1
      if (right === "") return 1
      return left.localeCompare(right)
    })
    .map(([path, directoryFiles]) => ({
      path,
      label: path || "Data root",
      file_count: directoryFiles.length,
      files: directoryFiles.sort((left, right) => left.path.localeCompare(right.path)),
    }))
}

function renderDirectoryOptions(selectedPath = null) {
  directorySelect.innerHTML = ""

  const placeholder = document.createElement("option")
  placeholder.value = ""
  placeholder.selected = selectedPath == null
  placeholder.disabled = state.knownFileDirectories.length > 0
  placeholder.textContent = state.knownFileDirectories.length > 0
    ? "Choose a folder..."
    : "No mounted filterbanks found"
  directorySelect.appendChild(placeholder)

  for (const directory of state.knownFileDirectories) {
    const option = document.createElement("option")
    option.value = directoryOptionValue(directory.path)
    option.textContent = `${directory.label} (${directory.file_count})`
    option.selected = directory.path === selectedPath
    directorySelect.appendChild(option)
  }
}

function renderFileOptions(directoryPath, options = {}) {
  const { selectedFile = "", resetSelection = false } = options
  const normalizedSelection = normalizeKnownFilePath(selectedFile)
  const directory = findKnownDirectory(directoryPath)
  const files = directory?.files || []

  fileSelect.innerHTML = ""

  const placeholder = document.createElement("option")
  placeholder.value = ""
  placeholder.selected = true
  placeholder.disabled = true
  placeholder.textContent = directory == null
    ? (state.knownFileDirectories.length > 0 ? "Choose a folder first" : "No mounted filterbanks found")
    : "Choose a filterbank..."
  fileSelect.appendChild(placeholder)

  for (const file of files) {
    const option = document.createElement("option")
    option.value = file.path
    option.textContent = file.name
    option.title = file.path
    option.selected = !resetSelection && file.path === normalizedSelection
    fileSelect.appendChild(option)
  }
}

function syncKnownFileSelection(path) {
  const normalizedPath = normalizeKnownFilePath(path)
  const directory = state.knownFileDirectories.find((candidate) =>
    candidate.files.some((file) => file.path === normalizedPath),
  )

  if (!directory) {
    const defaultDirectory = state.knownFileDirectories.length === 1 ? state.knownFileDirectories[0].path : null
    renderDirectoryOptions(defaultDirectory)
    renderFileOptions(defaultDirectory, { resetSelection: true })
    if (defaultDirectory != null) {
      directorySelect.value = directoryOptionValue(defaultDirectory)
    }
    return
  }

  renderDirectoryOptions(directory.path)
  renderFileOptions(directory.path, { selectedFile: normalizedPath })
  directorySelect.value = directoryOptionValue(directory.path)
  fileSelect.value = normalizedPath
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
    state.knownFileDirectories = Array.isArray(payload.directories)
      ? payload.directories
      : groupKnownFiles(Array.isArray(payload.files) ? payload.files : [])
    const defaultDirectory = state.knownFileDirectories.length === 1 ? state.knownFileDirectories[0].path : null
    renderDirectoryOptions(defaultDirectory)
    renderFileOptions(defaultDirectory, { resetSelection: true })
    if (defaultDirectory != null) {
      directorySelect.value = directoryOptionValue(defaultDirectory)
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
  const previousSessionId = state.sessionId
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
        npol_override: parseOptionalInteger(npolInput.value),
        read_start_sec: parseOptionalNumber(readStartInput.value),
        read_end_sec: parseOptionalNumber(readEndInput.value),
        distance_mpc: parseOptionalNumber(distanceInput.value),
        redshift: parseOptionalNumber(redshiftInput.value),
        auto_mask_profile: autoMaskProfileInput.value || "auto",
      }),
    })
    state.sessionId = payload.session_id
    state.exportManifest = null
    resetExportSelection()
    applyView(payload.view)
    if (previousSessionId && previousSessionId !== payload.session_id) {
      try {
        await api(`/api/sessions/${previousSessionId}`, { method: "DELETE" })
      } catch (cleanupError) {
        console.warn("Failed to delete previous session", cleanupError)
      }
    }
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
    if (type === "optimize_dm") {
      state.activeAnalysisTab = "dm"
    } else if (type === "compute_properties" || type === "compute_widths" || type === "accept_width_result") {
      state.activeAnalysisTab = "prepare"
    } else if (type === "fit_scattering") {
      state.activeAnalysisTab = "fitting"
    } else if (type === "run_temporal_structure_analysis" || type === "run_spectral_analysis") {
      state.activeAnalysisTab = "temporal"
    } else if (type === "export_results") {
      state.activeAnalysisTab = "export"
    }
    if (response.export_manifest) {
      state.exportManifest = response.export_manifest
    }
    if (type === "export_results") {
      state.exportPreviewStale = false
    } else {
      markExportPreviewStale()
    }
    applyView(response.view)
    setStatus("Ready", "success")
    const message = type === "auto_mask_jess"
      ? autoMaskToastText(response.view.state.last_auto_mask)
      : actionSuccessText(type)
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
  if (view.meta.auto_mask_profile) {
    autoMaskProfileInput.value = view.meta.auto_mask_profile
  }
  if (view.meta.preset_key) {
    setPresetSelection(view.meta.preset_key)
  }
  syncDmMetricOptions(view)
  dmInput.value = String(view.meta.dm)
  sefdInput.value = view.meta.sefd_jy === null || view.meta.sefd_jy === undefined ? "" : String(view.meta.sefd_jy)
  npolInput.value = view.meta.npol_override === null || view.meta.npol_override === undefined ? "" : String(view.meta.npol_override)
  distanceInput.value = view.meta.distance_mpc === null || view.meta.distance_mpc === undefined ? "" : String(view.meta.distance_mpc)
  redshiftInput.value = view.meta.redshift === null || view.meta.redshift === undefined ? "" : String(view.meta.redshift)
  notesInput.value = view.state.notes || ""
  syncSpectralSegmentInput(view)
  resolutionLabel.textContent = `t x${view.state.time_factor} / f x${view.state.freq_factor}`
  burstTitle.textContent = view.meta.burst_name
  burstSubtitle.textContent =
    `${fmt(view.meta.shape[0], 0)} channels x ${fmt(view.meta.shape[1], 0)} time bins. ` +
    `Refine the event window, off-pulse windows, component regions, masking, and spectral window directly in the viewer.`
  renderHero(view)
  renderSessionFacts(view)
  renderPrepare(view)
  renderDmOptimization(view)
  renderFitting(view.results)
  renderSpectral(view)
  renderExportPlanner()
  renderExportManifest()
  setAnalysisTab(state.activeAnalysisTab)
  renderPlots(view)
  updateControlStates()
}

function dmMetricOptions(view) {
  const options = Array.isArray(view?.meta?.dm_metrics) ? view.meta.dm_metrics : []
  if (options.length) {
    return options
  }
  return [
    { key: "integrated_event_snr", label: "Integrated-event S/N", summary: "Legacy sweep metric.", formula: "", origin: "", references: [] },
    { key: "dm_phase", label: "DMphase", summary: "Use the automatic DMphase coherent-power curve on the selected reduced waterfall.", formula: "", origin: "", references: [] },
  ]
}

function syncDmMetricOptions(view) {
  const options = dmMetricOptions(view)
  const currentValue = view?.dm_optimization?.settings?.metric || dmMetricInput.value || "integrated_event_snr"
  dmMetricInput.innerHTML = options
    .map(
      (option) =>
        `<option value="${escapeHtml(option.key)}" title="${escapeHtml(option.summary || "")}">${escapeHtml(option.label || option.key)}</option>`,
    )
    .join("")
  const availableValues = new Set(options.map((option) => option.key))
  dmMetricInput.value = availableValues.has(currentValue) ? currentValue : "integrated_event_snr"
}

function findDmMetricDefinition(view, metricKey) {
  const options = dmMetricOptions(view)
  return options.find((option) => option.key === metricKey) || options[0] || null
}

function tsampMs(view) {
  const value = Number(view?.meta?.tsamp_us)
  return Number.isFinite(value) && value > 0 ? value / 1000 : null
}

function eventBinCount(view) {
  const sampleMs = tsampMs(view)
  if (!sampleMs) {
    return 0
  }
  const eventMs = Array.isArray(view?.state?.event_ms) ? view.state.event_ms : []
  if (eventMs.length !== 2) {
    return 0
  }
  return Math.max(0, Math.round((Number(eventMs[1]) - Number(eventMs[0])) / sampleMs))
}

function defaultSpectralSegmentMs(view) {
  const sampleMs = tsampMs(view)
  const bins = eventBinCount(view)
  if (!sampleMs || bins <= 0) {
    return null
  }
  return bins * sampleMs
}

function spectralSegmentIsValid(view, segmentMs) {
  const sampleMs = tsampMs(view)
  const bins = eventBinCount(view)
  if (!sampleMs || bins <= 0 || !Number.isFinite(Number(segmentMs)) || Number(segmentMs) <= 0) {
    return false
  }
  const segmentBins = Math.max(1, Math.round(Number(segmentMs) / sampleMs))
  return Math.floor(bins / segmentBins) >= 1
}

function syncSpectralSegmentInput(view) {
  const sampleMs = tsampMs(view)
  if (!sampleMs) {
    spectralSegmentInput.value = ""
    return
  }
  spectralSegmentInput.step = String(sampleMs)
  spectralSegmentInput.min = String(sampleMs)
  const currentValue = Number(spectralSegmentInput.value)
  const nextValue = spectralSegmentIsValid(view, currentValue)
    ? currentValue
    : Number(view?.spectral_analysis?.segment_length_ms) || defaultSpectralSegmentMs(view) || sampleMs
  spectralSegmentInput.value = fmt(nextValue, 3)
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
    ["Auto mask profile", view.meta.auto_mask_profile_label],
    ["npol", view.meta.npol === null || view.meta.npol === undefined ? "missing" : String(view.meta.npol)],
    ["Header npol", view.meta.header_npol === null || view.meta.header_npol === undefined ? "missing" : String(view.meta.header_npol)],
    ["npol override", view.meta.npol_override === null || view.meta.npol_override === undefined ? "none" : String(view.meta.npol_override)],
    ["Telescope ID", view.meta.telescope_id === null ? "missing" : String(view.meta.telescope_id)],
    ["Machine ID", view.meta.machine_id === null ? "missing" : String(view.meta.machine_id)],
    ["Detection", view.meta.detection_basis],
    ["Raw shape", `${view.meta.shape[0]} x ${view.meta.shape[1]}`],
    ["Displayed shape", `${view.meta.view_shape[0]} x ${view.meta.view_shape[1]}`],
    ["SEFD", view.meta.sefd_jy === null ? "not set" : `${fmt(view.meta.sefd_jy, 2)} Jy`],
    ["Distance", view.meta.distance_mpc === null ? "not set" : `${fmt(view.meta.distance_mpc, 3)} Mpc`],
    ["Redshift", view.meta.redshift === null ? "not set" : fmt(view.meta.redshift, 5)],
    ["Crop", `${fmt(view.state.crop_ms[0], 2)} to ${fmt(view.state.crop_ms[1], 2)} ms`],
    ["Event", `${fmt(view.state.event_ms[0], 2)} to ${fmt(view.state.event_ms[1], 2)} ms`],
    ["Off-pulse", Array.isArray(view.state.offpulse_ms) && view.state.offpulse_ms.length ? view.state.offpulse_ms.map((window) => `${fmt(window[0], 2)} to ${fmt(window[1], 2)} ms`).join(", ") : "implicit event complement"],
    ["Spectral window", `${fmt(view.state.spectral_extent_mhz[0], 1)} to ${fmt(view.state.spectral_extent_mhz[1], 1)} MHz`],
    ["Peaks", view.state.peak_ms.length ? view.state.peak_ms.map((value) => `${fmt(value, 2)} ms`).join(", ") : "auto"],
    ["Last auto mask", formatAutoMaskSummary(view.state.last_auto_mask)],
    ["Notes", view.state.notes || "none"],
    ["Masked channels", compactList(view.state.masked_channels)],
  ]

  sessionFacts.innerHTML = facts
    .map(
      ([label, value]) =>
        `<div class="kv-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`,
    )
    .join("")
}

function renderPrepare(view) {
  const results = view.results
  const widthAnalysis = view.width_analysis
  if (!results && !widthAnalysis) {
    resultsContent.innerHTML =
      `
        ${renderPrepareChecklist(view, null)}
        <div class="empty-state">No measurements yet. Click Compute after marking the burst.</div>
        ${renderPrepareDiagnostics(null, widthAnalysis)}
      `
    return
  }

  const acceptedWidth = results?.accepted_width || widthAnalysis?.accepted_width || null
  const hasAcceptedWidth = acceptedWidth?.value !== null && acceptedWidth?.value !== undefined
  const hasAcfFallback = !hasAcceptedWidth && results?.width_ms_acf !== null && results?.width_ms_acf !== undefined
  const acceptedWidthTitle = hasAcceptedWidth ? "Accepted Width" : "Temporal Correlation Width (ACF)"
  const acceptedMethod = hasAcceptedWidth
    ? formatWidthMethod(acceptedWidth.method)
    : (hasAcfFallback ? "ACF half max" : "n/a")
  const acceptedWidthValue = hasAcceptedWidth
    ? `${fmt(acceptedWidth.value, 3)} ${acceptedWidth.units || "ms"}`
    : (hasAcfFallback ? `${fmt(results.width_ms_acf, 3)} ms` : "n/a")
  const acceptedWidthUncertainty = hasAcceptedWidth
    ? (acceptedWidth.uncertainty === null || acceptedWidth.uncertainty === undefined ? "n/a" : `±${fmt(acceptedWidth.uncertainty, 3)} ${acceptedWidth.units || "ms"}`)
    : (results?.uncertainties?.width_ms_acf === null || results?.uncertainties?.width_ms_acf === undefined ? "n/a" : `±${fmt(results.uncertainties.width_ms_acf, 3)} ms`)

  const cards = [
    renderMeasurementCard("TOA (Topo MJD)", results?.toa_topo_mjd === null || results?.toa_topo_mjd === undefined ? "n/a" : fmt(results.toa_topo_mjd, 8), {
      uncertainty: results?.uncertainties?.toa_topo_mjd ? formatToaUncertainty(results.uncertainties.toa_topo_mjd) : null,
      method: "peak bin",
      flags: results?.measurement_flags || [],
      details: renderMeasurementDetails(results?.provenance),
    }),
    renderMeasurementCard("Peak Bin S/N", results?.snr_peak === null || results?.snr_peak === undefined ? "n/a" : fmt(results.snr_peak, 3), {
      method: "event peak",
      flags: results?.measurement_flags || [],
      details: renderMeasurementDetails(results?.provenance),
    }),
    renderMeasurementCard("Integrated Event S/N", results?.snr_integrated === null || results?.snr_integrated === undefined ? "n/a" : fmt(results.snr_integrated, 3), {
      method: "selected event window",
      flags: results?.measurement_flags || [],
      details: renderMeasurementDetails(results?.provenance),
    }),
    renderMeasurementCard(acceptedWidthTitle, acceptedWidthValue, {
      uncertainty: acceptedWidthUncertainty === "n/a" ? null : acceptedWidthUncertainty,
      method: acceptedMethod,
      flags: acceptedWidthFlags(widthAnalysis, acceptedWidth),
      details: renderAcceptedWidthDetails(widthAnalysis, acceptedWidth, results?.provenance, hasAcfFallback),
    }),
    renderMeasurementCard("Fluence", results?.fluence_jyms === null || results?.fluence_jyms === undefined ? "n/a" : `${fmt(results.fluence_jyms, 3)} Jy ms`, {
      uncertainty: results?.uncertainties?.fluence_jyms ? `±${fmt(results.uncertainties.fluence_jyms, 3)} Jy ms` : null,
      method: results?.provenance?.calibration_method || "n/a",
      flags: results?.measurement_flags || [],
      details: renderMeasurementDetails(results?.provenance),
    }),
    renderMeasurementCard("Peak Flux Density", results?.peak_flux_jy === null || results?.peak_flux_jy === undefined ? "n/a" : `${fmt(results.peak_flux_jy, 3)} Jy`, {
      uncertainty: results?.uncertainties?.peak_flux_jy ? `±${fmt(results.uncertainties.peak_flux_jy, 3)} Jy` : null,
      method: results?.provenance?.calibration_method || "n/a",
      flags: results?.measurement_flags || [],
      details: renderMeasurementDetails(results?.provenance),
    }),
  ]

  const secondaryTiles = [
    resultTile("Spectral Correlation Width (ACF)", results?.spectral_width_mhz_acf === null || results?.spectral_width_mhz_acf === undefined ? "n/a" : `${fmt(results.spectral_width_mhz_acf, 3)} MHz`, "secondary"),
    resultTile("Spectral Window", results?.spectral_extent_mhz === null || results?.spectral_extent_mhz === undefined ? "n/a" : `${fmt(results.spectral_extent_mhz, 2)} MHz`, "secondary"),
    resultTile("Event Duration", results?.event_duration_ms === null || results?.event_duration_ms === undefined ? "n/a" : `${fmt(results.event_duration_ms, 3)} ms`, "secondary"),
    resultTile("Isotropic Energy", formatIsoEnergy(results?.iso_e, results?.provenance?.energy_unit), "secondary"),
    resultTile("Mask Count", results ? String(results.mask_count) : String(view.state.masked_channels.length), "secondary"),
    resultTile("Peak Positions", results?.peak_positions_ms?.length ? results.peak_positions_ms.map((value) => `${fmt(value, 2)} ms`).join(", ") : "n/a", "secondary"),
  ]

  resultsContent.innerHTML = `
    ${renderPrepareChecklist(view, results)}
    <div class="measurement-grid">
      ${cards.join("")}
    </div>
    <div class="results-secondary">
      ${secondaryTiles.join("")}
    </div>
    ${renderPrepareDiagnostics(results, widthAnalysis)}
  `
}

function renderPrepareChecklist(view, results) {
  const regions = Array.isArray(view.state.burst_regions_ms) ? view.state.burst_regions_ms.length : 0
  const peaks = Array.isArray(view.state.peak_ms) ? view.state.peak_ms.length : 0
  const offpulse = Array.isArray(view.state.offpulse_ms) ? view.state.offpulse_ms.length : 0
  const readinessTiles = [
    resultTile("Crop", `${fmt(view.state.crop_ms[0], 2)} to ${fmt(view.state.crop_ms[1], 2)} ms`, "secondary"),
    resultTile("Event", `${fmt(view.state.event_ms[0], 2)} to ${fmt(view.state.event_ms[1], 2)} ms`, "secondary"),
    resultTile("Off-pulse", offpulse ? `${offpulse} window${offpulse === 1 ? "" : "s"}` : "implicit complement", "secondary"),
    resultTile("Components", String(regions), "secondary"),
    resultTile("Peaks", peaks ? `${peaks} manual` : "auto", "secondary"),
    resultTile("Mask", `${view.state.masked_channels.length} channel${view.state.masked_channels.length === 1 ? "" : "s"}`, "secondary"),
    resultTile("Spectral Window", `${fmt(view.state.spectral_extent_mhz[0], 1)} to ${fmt(view.state.spectral_extent_mhz[1], 1)} MHz`, "secondary"),
    resultTile("Status", results ? "Measurements ready" : "Selection only", "secondary"),
  ]

  return `
    <section class="results-section">
      <div class="analysis-panel-head compact">
        <h5>Readiness</h5>
        <p>Everything in DM, Fitting, and Temporal uses this same crop, event/off-pulse selection, mask, spectral window, and applied DM.</p>
      </div>
      <div class="results-secondary">
        ${readinessTiles.join("")}
      </div>
    </section>
  `
}

function renderPrepareDiagnostics(results, widthAnalysis) {
  const uncertainties = results?.uncertainties || {}
  const provenance = results?.provenance || {}
  const measurementFlags = Array.isArray(results?.measurement_flags) ? results.measurement_flags : []
  const widthFlags = Array.isArray(widthAnalysis?.noise_summary?.warning_flags) ? widthAnalysis.noise_summary.warning_flags : []
  const combinedFlags = Array.from(new Set([...measurementFlags, ...widthFlags]))
  const flagMarkup = combinedFlags.length
    ? combinedFlags.map((flag) => infoChip("Flag", formatMeasurementFlag(flag), flagTone(flag))).join("")
    : infoChip("Flag", "None", "neutral")
  const uncertaintyTiles = []
  if (uncertainties.toa_topo_mjd !== null && uncertainties.toa_topo_mjd !== undefined) {
    uncertaintyTiles.push(resultTile("TOA", formatToaUncertainty(uncertainties.toa_topo_mjd), "secondary"))
  }
  if (uncertainties.width_ms_acf !== null && uncertainties.width_ms_acf !== undefined) {
    uncertaintyTiles.push(resultTile("Width", `±${fmt(uncertainties.width_ms_acf, 3)} ms`, "secondary"))
  }
  if (uncertainties.spectral_width_mhz_acf !== null && uncertainties.spectral_width_mhz_acf !== undefined) {
    uncertaintyTiles.push(resultTile("Spectral Width", `±${fmt(uncertainties.spectral_width_mhz_acf, 3)} MHz`, "secondary"))
  }
  if (uncertainties.peak_flux_jy !== null && uncertainties.peak_flux_jy !== undefined) {
    uncertaintyTiles.push(resultTile("Peak Flux", `±${fmt(uncertainties.peak_flux_jy, 3)} Jy`, "secondary"))
  }
  if (uncertainties.fluence_jyms !== null && uncertainties.fluence_jyms !== undefined) {
    uncertaintyTiles.push(resultTile("Fluence", `±${fmt(uncertainties.fluence_jyms, 3)} Jy ms`, "secondary"))
  }
  if (uncertainties.iso_e !== null && uncertainties.iso_e !== undefined) {
    uncertaintyTiles.push(
      resultTile(
        "Isotropic Energy",
        `±${formatScientific(uncertainties.iso_e, 3)} ${provenance.energy_unit || ""}`.trim(),
        "secondary",
      ),
    )
  }

  const widthNoiseDetails = widthAnalysis?.noise_summary
    ? `
      <details class="details-card results-details">
        <summary>Width Noise Basis</summary>
        <div class="kv-list">
          <div class="kv-item"><span>Estimator</span><strong>${escapeHtml(widthAnalysis.noise_summary.estimator || "n/a")}</strong></div>
          <div class="kv-item"><span>Basis</span><strong>${escapeHtml(widthAnalysis.noise_summary.basis || "n/a")}</strong></div>
          <div class="kv-item"><span>Baseline</span><strong>${escapeHtml(fmt(widthAnalysis.noise_summary.baseline, 4))}</strong></div>
          <div class="kv-item"><span>Sigma</span><strong>${escapeHtml(fmt(widthAnalysis.noise_summary.sigma, 4))}</strong></div>
          <div class="kv-item"><span>Off-pulse bins</span><strong>${escapeHtml(String(widthAnalysis.noise_summary.offpulse_bin_count || 0))}</strong></div>
        </div>
      </details>
    `
    : ""

  return `
    <section class="results-section">
      <div class="analysis-panel-head compact">
        <h5>Warnings and Provenance</h5>
        <p>Review flags, uncertainty estimates, and why the accepted values were produced before moving into specialist analysis.</p>
      </div>
    </section>
    <div class="hero-tags">
      ${flagMarkup}
    </div>
    ${uncertaintyTiles.length
      ? `<div class="results-secondary">${uncertaintyTiles.join("")}</div>`
      : '<div class="empty-state">No reported uncertainties for the current measurement set.</div>'}
    <details class="details-card results-details">
      <summary>Selection and Provenance</summary>
      <div class="kv-list">
        ${renderProvenanceItems(provenance)}
      </div>
    </details>
    ${widthNoiseDetails}
  `
}

function renderDmOptimization(view) {
  const optimization = view.dm_optimization
  const selectedMetricKey = optimization?.snr_metric || dmMetricInput.value || "integrated_event_snr"
  const definition = findDmMetricDefinition(view, selectedMetricKey)
  if (!optimization) {
    dmOptimizeBadge.textContent = "Idle"
    dmOptimizeBadge.dataset.tone = "neutral"
    dmOptimizationContent.innerHTML =
      '<div class="empty-state">No DM sweep yet. Run Optimize DM to inspect the selected metric curve.</div>' +
      renderDmMetricDefinition(definition)
    dmResidualContent.innerHTML =
      '<div class="empty-state">No residual diagnostics yet. Run Optimize DM to compare sub-band arrival times.</div>'
    dmComponentsContent.innerHTML =
      '<div class="empty-state">No component DM summary yet. Add multiple component regions or manual peaks before running a sweep if the burst is complex.</div>'
    dmOptimizationPlot.classList.add("is-empty")
    dmResidualPlot.classList.add("is-empty")
    Plotly.purge(dmOptimizationPlot)
    dmOptimizationPlot.replaceChildren()
    Plotly.purge(dmResidualPlot)
    dmResidualPlot.replaceChildren()
    return
  }

  const fitTone = fitStatusTone(optimization.fit_status)
  dmOptimizeBadge.textContent = fitTone === "success" ? "Fit Ready" : "Sweep Ready"
  dmOptimizeBadge.dataset.tone = fitTone
  const snrLabel = snrMetricLabel(optimization.snr_metric)
  const scoreLabel = optimization.snr_metric === "dm_phase" ? "DMphase Score" : snrLabel
  const metricSummary = definition?.summary || snrMetricSummary(optimization.snr_metric)
  const residualTone = residualStatusTone(optimization.residual_status)
  const residualBandCount = Array.isArray(optimization.subband_freqs_mhz) ? optimization.subband_freqs_mhz.length : 0
  const metricContextNote = optimization.snr_metric === "dm_phase"
    ? "Sampled Peak DM is the discrete argmax of the DMphase curve. Best DM is the upstream-style weighted polynomial refinement used for comparison with DM_phase."
    : "Sampled Peak DM is the best discrete grid point. Best DM is the local quadratic refinement around that sampled peak."

  const primaryTiles = [
    resultTile("Best DM", fmt(optimization.best_dm, 6), "primary", "Refined best-fit DM reported by the selected metric model."),
    resultTile("DM Uncertainty", optimization.best_dm_uncertainty === null ? "n/a" : `±${fmt(optimization.best_dm_uncertainty, 6)}`, "primary", "Reported 1-sigma uncertainty on the refined best DM when the fit model can provide one."),
    resultTile(`Best ${scoreLabel}`, fmt(optimization.best_sn, 3), "primary", `Best fitted ${scoreLabel.toLowerCase()} at the refined DM solution.`),
  ]

  const secondaryTiles = [
    resultTile("Selected Metric", snrLabel, "secondary", "Metric currently used to score every trial DM in this sweep."),
    resultTile("Sweep Center DM", fmt(optimization.center_dm, 6), "secondary", "DM around which the symmetric trial grid was generated."),
    resultTile("Sampled Peak DM", fmt(optimization.sampled_best_dm, 6), "secondary", "Best discrete DM bin before any peak refinement is applied."),
    resultTile(`Sampled Peak ${scoreLabel}`, fmt(optimization.sampled_best_sn, 3), "secondary", `Raw ${scoreLabel.toLowerCase()} at the best discrete trial DM.`),
    resultTile("Half-range", fmt(optimization.actual_half_range, 3), "secondary", "Actual symmetric search half-range covered by the discrete trial grid."),
    resultTile("Step", fmt(optimization.step, 3), "secondary", "Spacing between adjacent trial DMs in the sweep."),
    resultTile("Applied DM During Sweep", fmt(optimization.applied_dm, 6), "secondary", "DM that was applied to the session data before the sweep was run."),
  ]

  dmOptimizationContent.innerHTML = `
    <div class="results-primary">
      ${primaryTiles.join("")}
    </div>
    <div class="results-secondary">
      ${secondaryTiles.join("")}
    </div>
    <div class="dm-fit-note" data-tone="neutral">
      <strong>${escapeHtml(snrLabel)}</strong>
      <span>${escapeHtml(metricSummary)}</span>
    </div>
    <div class="dm-fit-note" data-tone="neutral">
      <strong>${escapeHtml(optimization.snr_metric === "dm_phase" ? "How To Read DMphase" : "How To Read This Sweep")}</strong>
      <span>${escapeHtml(metricContextNote)}</span>
    </div>
    <div class="dm-fit-note" data-tone="${escapeHtml(fitTone)}">
      <strong>${escapeHtml(fitStatusLabel(optimization.fit_status))}</strong>
      <span>${escapeHtml(fitStatusCopy(optimization.fit_status))}</span>
    </div>
    ${renderDmMetricDefinition(definition)}
    ${optimization.provenance ? `
      <details class="details-card results-details">
        <summary>DM Sweep Details</summary>
        <div class="kv-list">
          ${renderProvenanceItems(optimization.provenance)}
        </div>
      </details>
    ` : ""}
  `
  const residualSummaryTiles = [
    resultTile("Residual Status", residualStatusLabel(optimization.residual_status), "secondary"),
    resultTile("Usable Sub-bands", String(residualBandCount), "secondary"),
    resultTile("Current Applied DM", fmt(view.meta.dm, 6), "secondary"),
    resultTile("Best-fit DM", fmt(optimization.best_dm, 6), "secondary"),
    resultTile("Applied Drift RMS", optimization.residual_rms_applied_ms === null ? "n/a" : `${fmt(optimization.residual_rms_applied_ms, 4)} ms`, "secondary"),
    resultTile("Best Drift RMS", optimization.residual_rms_best_ms === null ? "n/a" : `${fmt(optimization.residual_rms_best_ms, 4)} ms`, "secondary"),
    resultTile("Applied Drift Slope", optimization.residual_slope_applied_ms_per_mhz === null ? "n/a" : `${fmt(optimization.residual_slope_applied_ms_per_mhz, 6)} ms/MHz`, "secondary"),
    resultTile("Best Drift Slope", optimization.residual_slope_best_ms_per_mhz === null ? "n/a" : `${fmt(optimization.residual_slope_best_ms_per_mhz, 6)} ms/MHz`, "secondary"),
  ]

  dmResidualContent.innerHTML = `
    <div class="results-section">
      <div class="results-secondary">
        ${residualSummaryTiles.join("")}
      </div>
      <div class="dm-fit-note" data-tone="${escapeHtml(residualTone)}">
        <strong>${escapeHtml(residualStatusLabel(optimization.residual_status))}</strong>
        <span>${escapeHtml(residualStatusCopy(optimization.residual_status))}</span>
      </div>
      ${renderResidualTable(optimization)}
    </div>
  `
  dmComponentsContent.innerHTML = renderDmComponentSummary(optimization)
}

function renderDmMetricDefinition(definition) {
  if (!definition) {
    return ""
  }
  const references = Array.isArray(definition.references) ? definition.references : []
  const showReferences = definition.key === "dm_phase" && references.length
  const referencesMarkup = showReferences
    ? `<ul class="reference-list">${references.map(renderDmMetricReference).join("")}</ul>`
    : ""
  const referencesSection = showReferences
    ? `
      <div class="references-block">
        <h5>DMphase Reference</h5>
        ${referencesMarkup}
      </div>
    `
    : ""

  return `
    <details class="details-card results-details">
      <summary>Selected Metric</summary>
      <div class="kv-list">
        <div class="kv-item"><span>Metric</span><strong>${escapeHtml(definition.label || definition.key || "n/a")}</strong></div>
        <div class="kv-item"><span>Definition</span><strong>${escapeHtml(definition.summary || "n/a")}</strong></div>
        <div class="kv-item"><span>Formula</span><strong>${escapeHtml(definition.formula || "n/a")}</strong></div>
        <div class="kv-item"><span>Origin</span><strong>${escapeHtml(definition.origin || "n/a")}</strong></div>
      </div>
      ${referencesSection}
    </details>
  `
}

function renderDmMetricReference(reference) {
  const note = reference?.note ? `<div class="reference-note">${escapeHtml(reference.note)}</div>` : ""
  return `
    <li class="reference-item">
      <a href="${escapeHtml(reference?.url || "#")}" target="_blank" rel="noreferrer noopener">${escapeHtml(reference?.label || reference?.citation || "Reference")}</a>
      <div class="reference-citation">${escapeHtml(reference?.citation || "")}</div>
      ${note}
    </li>
  `
}

function renderDmComponentSummary(optimization) {
  const components = Array.isArray(optimization.component_results) ? optimization.component_results : []
  if (!components.length) {
    return '<div class="empty-state">No component DM summary for this sweep. Add multiple component regions or manual peaks to estimate per-component DM values.</div>'
  }

  const rows = components.map((component) => (
    `<tr>` +
    `<td>${escapeHtml(component.label || component.component_id || "Component")}</td>` +
    `<td>${escapeHtml(`${fmt(component.event_window_ms?.[0], 3)} to ${fmt(component.event_window_ms?.[1], 3)} ms`)}</td>` +
    `<td>${escapeHtml(fmt(component.best_dm, 6))}</td>` +
    `<td>${escapeHtml(component.best_dm_uncertainty === null ? "n/a" : `±${fmt(component.best_dm_uncertainty, 6)}`)}</td>` +
    `<td>${escapeHtml(fmt(component.best_value, 3))}</td>` +
    `<td>${escapeHtml(fitStatusLabel(component.fit_status))}</td>` +
    `</tr>`
  )).join("")

  return `
    <details class="details-card results-details" open>
      <summary>Component DM Summary (${components.length})</summary>
      <div class="residual-table-wrap">
        <table class="residual-table">
          <thead>
            <tr>
              <th>Component</th>
              <th>Window</th>
              <th>Best DM</th>
              <th>Uncertainty</th>
              <th>Best ${escapeHtml(snrMetricLabel(optimization.snr_metric))}</th>
              <th>Fit Status</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    </details>
  `
}

function renderFitting(results) {
  const diagnostics = results?.diagnostics || {}
  const scatteringFit = diagnostics?.scattering_fit

  if (!results) {
    fittingContent.innerHTML =
      '<div class="empty-state">No fitting diagnostics yet. Compute measurements or run a model fit on the current selection to inspect component and scattering behavior.</div>'
    fittingSpectrumPlot.classList.add("is-empty")
    Plotly.purge(fittingSpectrumPlot)
    fittingSpectrumPlot.replaceChildren()
    fittingProfilePlot.classList.add("is-empty")
    Plotly.purge(fittingProfilePlot)
    fittingProfilePlot.replaceChildren()
    return
  }

  if (!scatteringFit) {
    fittingContent.innerHTML = `
      <div class="empty-state">No 2D model fit yet. Run the fit on the current selection to inspect model width, scattering time, and residuals.</div>
    `
    fittingSpectrumPlot.classList.add("is-empty")
    Plotly.purge(fittingSpectrumPlot)
    fittingSpectrumPlot.replaceChildren()
    fittingProfilePlot.classList.add("is-empty")
    Plotly.purge(fittingProfilePlot)
    fittingProfilePlot.replaceChildren()
    return
  }

  const fitTone = scatteringStatusTone(scatteringFit.status)
  const fitStatistics = scatteringFit.fit_statistics || {}
  const bestfit = scatteringFit.bestfit_parameters || {}
  const bestfitUncertainties = scatteringFit.bestfit_uncertainties || {}
  const fitSummaryTiles = [
    resultTile("Model Width", results.width_ms_model === null ? "n/a" : `${fmt(results.width_ms_model, 3)} ms`, "primary"),
    resultTile("Scattering Tau", results.tau_sc_ms === null ? "n/a" : `${fmt(results.tau_sc_ms, 3)} ms`, "primary"),
    resultTile("Reduced Chi^2", fitStatistics.chisq_final_reduced === null || fitStatistics.chisq_final_reduced === undefined ? "n/a" : fmt(fitStatistics.chisq_final_reduced, 3), "primary"),
  ]
  const fitMetaTiles = [
    resultTile("Fitter", scatteringFit.fitter || "n/a", "secondary"),
    resultTile("Fit S/N", fitStatistics.snr === null || fitStatistics.snr === undefined ? "n/a" : fmt(fitStatistics.snr, 3), "secondary"),
    resultTile("Good Channels", fitStatistics.num_freq_good === null || fitStatistics.num_freq_good === undefined ? "n/a" : String(fitStatistics.num_freq_good), "secondary"),
    resultTile("Observations", fitStatistics.num_observations === null || fitStatistics.num_observations === undefined ? "n/a" : String(fitStatistics.num_observations), "secondary"),
    resultTile("Fitted Parameters", Array.isArray(scatteringFit.fit_parameters) && scatteringFit.fit_parameters.length ? scatteringFit.fit_parameters.join(", ") : "n/a", "secondary"),
    resultTile("Fixed Parameters", Array.isArray(scatteringFit.fixed_parameters) && scatteringFit.fixed_parameters.length ? scatteringFit.fixed_parameters.join(", ") : "n/a", "secondary"),
  ]

  fittingContent.innerHTML = `
    <div class="results-primary">
      ${fitSummaryTiles.join("")}
    </div>
    <div class="results-secondary">
      ${fitMetaTiles.join("")}
    </div>
    <div class="dm-fit-note" data-tone="${escapeHtml(fitTone)}">
      <strong>${escapeHtml(scatteringStatusLabel(scatteringFit.status))}</strong>
      <span>${escapeHtml(scatteringFit.status === "ok" ? scatteringStatusCopy(scatteringFit.status) : (scatteringFit.message || scatteringStatusCopy(scatteringFit.status)))}</span>
    </div>
    ${renderScatteringParameterTable(bestfit, bestfitUncertainties)}
    ${results?.provenance ? `
      <details class="details-card results-details">
        <summary>Fit Selection Context</summary>
        <div class="kv-list">
          ${renderProvenanceItems(results.provenance)}
        </div>
      </details>
    ` : ""}
  `
}

function renderSpectral(view) {
  const temporalStructure = view?.temporal_structure
  const widthAnalysis = view?.width_analysis
  const results = view?.results
  const sharedStateTiles = [
    resultTile("Applied DM", fmt(view.meta.dm, 6), "secondary"),
    resultTile("Crop", `${fmt(view.state.crop_ms[0], 2)} to ${fmt(view.state.crop_ms[1], 2)} ms`, "secondary"),
    resultTile("Event", `${fmt(view.state.event_ms[0], 2)} to ${fmt(view.state.event_ms[1], 2)} ms`, "secondary"),
    resultTile("Off-pulse", Array.isArray(view.state.offpulse_ms) && view.state.offpulse_ms.length ? `${view.state.offpulse_ms.length} explicit` : "implicit complement", "secondary"),
    resultTile("Spectral Window", `${fmt(view.state.spectral_extent_mhz[0], 1)} to ${fmt(view.state.spectral_extent_mhz[1], 1)} MHz`, "secondary"),
    resultTile("Masked Channels", String(view.state.masked_channels.length), "secondary"),
  ]

  let spectralBody = '<div class="empty-state">No temporal-structure diagnostics yet. Choose a segment length and run the analysis on the current event window.</div>'
  if (temporalStructure) {
    const summaryTiles = [
      resultTile("Min Structure", temporalStructure.min_structure_ms_primary === null || temporalStructure.min_structure_ms_primary === undefined ? "n/a" : `${fmt(temporalStructure.min_structure_ms_primary, 3)} ms`, "primary"),
      resultTile("Wavelet Scale", temporalStructure.min_structure_ms_wavelet === null || temporalStructure.min_structure_ms_wavelet === undefined ? "n/a" : `${fmt(temporalStructure.min_structure_ms_wavelet, 3)} ms`, "primary"),
      resultTile("PSD Slope", temporalStructure.power_law_alpha === null || temporalStructure.power_law_alpha === undefined ? "n/a" : `${fmt(temporalStructure.power_law_alpha, 3)} ± ${fmt(temporalStructure.power_law_alpha_err, 3)}`, "primary"),
      resultTile("fitburst Cross-Check", temporalStructure.fitburst_min_component_ms === null || temporalStructure.fitburst_min_component_ms === undefined ? "n/a" : `${fmt(temporalStructure.fitburst_min_component_ms, 3)} ms`, "primary"),
    ]
    const resultTiles = [
      resultTile("Status", spectralStatusLabel(temporalStructure.status), "secondary"),
      resultTile("Segment Length", temporalStructure.segment_length_ms === null || temporalStructure.segment_length_ms === undefined ? "n/a" : `${fmt(temporalStructure.segment_length_ms, 3)} ms`, "secondary"),
      resultTile("Segments", temporalStructure.segment_count === null || temporalStructure.segment_count === undefined ? "n/a" : String(temporalStructure.segment_count), "secondary"),
      resultTile("Resolution", temporalStructure.frequency_resolution_hz === null || temporalStructure.frequency_resolution_hz === undefined ? "n/a" : `${fmt(temporalStructure.frequency_resolution_hz, 3)} Hz`, "secondary"),
      resultTile("Nyquist", temporalStructure.nyquist_hz === null || temporalStructure.nyquist_hz === undefined ? "n/a" : `${fmt(temporalStructure.nyquist_hz, 3)} Hz`, "secondary"),
      resultTile("Raw Bins", Array.isArray(temporalStructure.raw_periodogram_freq_hz) ? String(temporalStructure.raw_periodogram_freq_hz.length) : String(temporalStructure.raw_periodogram_freq_hz?.length || 0), "secondary"),
      resultTile("Averaged Bins", Array.isArray(temporalStructure.averaged_psd_freq_hz) ? String(temporalStructure.averaged_psd_freq_hz.length) : String(temporalStructure.averaged_psd_freq_hz?.length || 0), "secondary"),
      resultTile("PSD Fit", formatPowerLawFitStatus(temporalStructure.power_law_fit_status), "secondary"),
      resultTile("Noise Floor (C)", temporalStructure.power_law_c === null || temporalStructure.power_law_c === undefined ? "n/a" : `${fmt(temporalStructure.power_law_c, 3)} ± ${fmt(temporalStructure.power_law_c_err, 3)}`, "secondary"),
      resultTile("Scattering Tau", results?.tau_sc_ms === null || results?.tau_sc_ms === undefined ? "n/a" : `${fmt(results.tau_sc_ms, 3)} ms`, "secondary"),
    ]
    spectralBody = `
      <div class="results-section">
        <div class="analysis-panel-head compact">
          <h5>Temporal Structure Summary</h5>
          <p>Use the current selected-band event profile to estimate the shortest significant structure and compare raw and averaged power-spectrum diagnostics.</p>
        </div>
        <div class="results-primary">
          ${summaryTiles.join("")}
        </div>
        <div class="results-secondary">
          ${resultTiles.join("")}
        </div>
        <div class="dm-fit-note" data-tone="neutral" style="margin-top: 1rem;">
          <strong>How To Read This Tab</strong>
          <span>The primary minimum-timescale metric is data-driven. The PSD slope is fit only on the averaged periodogram, while fitburst widths are shown as a model-based cross-check when available.</span>
        </div>
      </div>
      ${temporalStructure.message ? `<div class="empty-state">${escapeHtml(temporalStructure.message)}</div>` : ""}
      ${renderWidthAnalysisSection(widthAnalysis)}
    `
  } else if (widthAnalysis) {
    spectralBody = renderWidthAnalysisSection(widthAnalysis)
  }

  spectralContent.innerHTML = `
    <div class="results-section">
      <div class="analysis-panel-head compact">
        <h5>Shared Session State</h5>
        <p>The temporal-structure workflow uses the same current session state as Prepare, DM, and Fitting.</p>
      </div>
      <div class="results-secondary">
        ${sharedStateTiles.join("")}
      </div>
    </div>
    ${spectralBody}
  `
  bindWidthActionButtons()
  if (temporalStructure?.status === "ok") {
    temporalScalePlot.classList.remove("is-empty")
    spectralPlot.classList.remove("is-empty")
    if (state.activeAnalysisTab === "temporal") {
      renderTemporalScalePlot(temporalStructure)
      renderSpectralPlot(temporalStructure)
    }
  } else {
    temporalScalePlot.classList.add("is-empty")
    Plotly.purge(temporalScalePlot)
    temporalScalePlot.replaceChildren()
    spectralPlot.classList.add("is-empty")
    Plotly.purge(spectralPlot)
    spectralPlot.replaceChildren()
  }
}

async function renderTemporalScalePlot(temporalStructure) {
  if (!temporalStructure || temporalStructure.status !== "ok") {
    temporalScalePlot.classList.add("is-empty")
    Plotly.purge(temporalScalePlot)
    temporalScalePlot.replaceChildren()
    return
  }

  temporalScalePlot.classList.remove("is-empty")
  const traces = [
    {
      x: temporalStructure.matched_filter_scales_ms,
      y: temporalStructure.matched_filter_boxcar_sigma,
      mode: "lines+markers",
      type: "scattergl",
      line: { color: plotTheme.accent, width: 2 },
      name: "Boxcar matched filter",
      hovertemplate: "Scale %{x:.4f} ms<br>Significance %{y:.3f} sigma<extra></extra>",
    },
    {
      x: temporalStructure.matched_filter_scales_ms,
      y: temporalStructure.matched_filter_gaussian_sigma,
      mode: "lines+markers",
      type: "scattergl",
      line: { color: plotTheme.accentAlt, width: 2 },
      name: "Gaussian matched filter",
      hovertemplate: "Scale %{x:.4f} ms<br>Significance %{y:.3f} sigma<extra></extra>",
    },
    {
      x: temporalStructure.wavelet_scales_ms,
      y: temporalStructure.wavelet_sigma,
      mode: "lines+markers",
      type: "scattergl",
      line: { color: plotTheme.muted, width: 2, dash: "dot" },
      name: "Wavelet scan",
      hovertemplate: "Scale %{x:.4f} ms<br>Significance %{y:.3f} sigma<extra></extra>",
    },
  ]
  if (typeof temporalStructure.matched_filter_threshold_sigma === "number" && Array.isArray(temporalStructure.matched_filter_scales_ms) && temporalStructure.matched_filter_scales_ms.length > 0) {
    traces.push({
      x: temporalStructure.matched_filter_scales_ms,
      y: temporalStructure.matched_filter_scales_ms.map(() => temporalStructure.matched_filter_threshold_sigma),
      mode: "lines",
      type: "scattergl",
      line: { color: plotTheme.warning, width: 1.5, dash: "dash" },
      name: "Matched threshold",
      hoverinfo: "skip",
    })
  }
  if (typeof temporalStructure.wavelet_threshold_sigma === "number" && Array.isArray(temporalStructure.wavelet_scales_ms) && temporalStructure.wavelet_scales_ms.length > 0) {
    traces.push({
      x: temporalStructure.wavelet_scales_ms,
      y: temporalStructure.wavelet_scales_ms.map(() => temporalStructure.wavelet_threshold_sigma),
      mode: "lines",
      type: "scattergl",
      line: { color: plotTheme.alert, width: 1.5, dash: "dash" },
      name: "Wavelet threshold",
      hoverinfo: "skip",
    })
  }

  await Plotly.react(
    "temporalScalePlot",
    traces,
    {
      margin: { l: 70, r: 24, t: 18, b: 54 },
      paper_bgcolor: plotTheme.paperBg,
      plot_bgcolor: plotTheme.plotBg,
      showlegend: true,
      legend: { orientation: "h" },
      xaxis: {
        title: "Scale (ms)",
        type: "log",
        gridcolor: plotTheme.grid,
      },
      yaxis: {
        title: "Detection Significance (sigma)",
        gridcolor: plotTheme.grid,
      },
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )
}

async function renderSpectralPlot(temporalStructure) {
  if (!temporalStructure || temporalStructure.status !== "ok") {
    spectralPlot.classList.add("is-empty")
    Plotly.purge(spectralPlot)
    spectralPlot.replaceChildren()
    return
  }

  spectralPlot.classList.remove("is-empty")
  const traces = [
    {
      x: temporalStructure.raw_periodogram_freq_hz,
      y: temporalStructure.raw_periodogram_power,
      mode: "lines",
      type: "scattergl",
      line: { color: plotTheme.neutral, width: 1.25 },
      name: "Raw periodogram",
      opacity: 0.7,
      hovertemplate: "Raw %{x:.3f} Hz<br>Power %{y:.4g}<extra></extra>",
    },
    {
      x: temporalStructure.averaged_psd_freq_hz,
      y: temporalStructure.averaged_psd_power,
      mode: "lines",
      type: "scattergl",
      line: { color: plotTheme.accent, width: 2 },
      name: "Averaged PSD",
      hovertemplate: "Frequency %{x:.3f} Hz<br>Power %{y:.4g}<extra></extra>",
    },
  ]
  if (temporalStructure.power_law_a !== null && temporalStructure.power_law_a !== undefined && temporalStructure.power_law_alpha !== null && temporalStructure.power_law_alpha !== undefined && Array.isArray(temporalStructure.averaged_psd_freq_hz) && temporalStructure.averaged_psd_freq_hz.length > 0) {
    const powerC = typeof temporalStructure.power_law_c === "number" ? temporalStructure.power_law_c : 0
    const fitPower = temporalStructure.averaged_psd_freq_hz.map(f => f > 0 ? temporalStructure.power_law_a * Math.pow(f, -temporalStructure.power_law_alpha) + powerC : null)
    traces.push({
      x: temporalStructure.averaged_psd_freq_hz,
      y: fitPower,
      mode: "lines",
      type: "scattergl",
      line: { color: plotTheme.accentAlt, width: 2, dash: "dash" },
      name: "Averaged PSD fit",
      hovertemplate: "Fit Power %{y:.4g}<extra></extra>",
    })
  }

  await Plotly.react(
    "spectralPlot",
    traces,
    {
      margin: { l: 70, r: 24, t: 18, b: 54 },
      paper_bgcolor: plotTheme.paperBg,
      plot_bgcolor: plotTheme.plotBg,
      showlegend: true,
      legend: { orientation: "h" },
      xaxis: {
        title: "Frequency (Hz)",
        type: "log",
        gridcolor: plotTheme.grid,
      },
      yaxis: {
        title: "Power",
        type: "log",
        gridcolor: plotTheme.grid,
      },
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )
}

function renderExportManifest() {
  if (!state.sessionId || !state.view) {
    exportManifestContent.innerHTML =
      '<div class="empty-state">Load a session before building export products.</div>'
    return
  }

  const manifest = state.exportManifest
  if (!manifest) {
    exportManifestContent.innerHTML =
      '<div class="empty-state">No export bundle yet. Build one from the current measurements, masking, event window, and DM state.</div>'
    return
  }

  const artifacts = Array.isArray(manifest.artifacts) ? manifest.artifacts : []
  const groupedArtifacts = {
    structured: artifacts.filter((artifact) => artifact.kind === "structured"),
    catalog: artifacts.filter((artifact) => artifact.kind === "catalog"),
    arrays: artifacts.filter((artifact) => artifact.kind === "arrays"),
    window: artifacts.filter((artifact) => artifact.kind === "window"),
    plots: artifacts.filter((artifact) => artifact.kind === "plot"),
  }

  exportManifestContent.innerHTML = `
    <div class="export-summary">
      <div class="results-secondary">
        ${resultTile("Bundle", manifest.bundle_name || "n/a", "secondary")}
        ${resultTile("Export ID", manifest.export_id || "n/a", "secondary")}
        ${resultTile("Schema", manifest.schema_version || "n/a", "secondary")}
        ${resultTile("Created", formatUtcTimestamp(manifest.created_at_utc), "secondary")}
      </div>
    </div>
    ${renderArtifactGroup("Structured", groupedArtifacts.structured)}
    ${renderArtifactGroup("Catalog", groupedArtifacts.catalog)}
    ${renderArtifactGroup("Arrays", groupedArtifacts.arrays)}
    ${renderArtifactGroup("Window", groupedArtifacts.window)}
    ${renderArtifactGroup("Plots", groupedArtifacts.plots)}
  `
}

function renderExportPlanner() {
  syncExportPlannerControls()

  if (!state.sessionId || !state.view) {
    exportSelectionSummary.textContent = "Load a session to configure an export preview."
    exportPreviewMeta.textContent = ""
    exportPreviewThumbs.innerHTML = ""
    exportPreviewContent.innerHTML =
      '<div class="empty-state">Load a session before planning export products.</div>'
    return
  }

  const labels = []
  if (state.exportSelection.include.includes("json")) labels.push("JSON")
  if (state.exportSelection.include.includes("csv")) labels.push("CSV")
  if (state.exportSelection.include.includes("npz")) labels.push("NPZ")
  if (state.exportSelection.include.includes("plots")) {
    const formats = state.exportSelection.plot_formats.map((value) => value.toUpperCase()).join(" + ")
    labels.push(formats ? `Plots (${formats})` : "Plots")
  }
  if (state.exportSelection.include.includes("window")) {
    const formats = state.exportSelection.window_formats.map((value) => value.toUpperCase()).join(" + ")
    const modes = state.exportSelection.window_resolutions.map((value) => value === "native" ? "Native" : "View").join(" + ")
    labels.push(`Window${formats || modes ? ` (${[formats, modes].filter(Boolean).join(" · ")})` : ""}`)
  }
  exportSelectionSummary.textContent = labels.length
    ? `Selected: ${labels.join(" · ")}`
    : "Select one or more outputs to generate a preview."

  if (exportSelectionCount() === 0) {
    exportPreviewMeta.textContent = ""
    exportPreviewThumbs.innerHTML = ""
    exportPreviewContent.innerHTML =
      '<div class="empty-state">Select outputs to preview the bundle before building it.</div>'
    return
  }

  if (state.exportPreviewPending) {
    exportPreviewMeta.textContent = "Refreshing exact preview..."
    exportPreviewMeta.dataset.tone = "neutral"
  } else if (state.exportPreviewError) {
    exportPreviewMeta.textContent = state.exportPreviewError
    exportPreviewMeta.dataset.tone = "warning"
  } else if (state.exportPreviewStale) {
    exportPreviewMeta.textContent = "Preview is out of date after session changes. It refreshes automatically in this tab."
    exportPreviewMeta.dataset.tone = "warning"
  } else if (state.exportPreview?.generated_at_utc) {
    exportPreviewMeta.textContent = `Preview ready · ${formatUtcTimestamp(state.exportPreview.generated_at_utc)}`
    exportPreviewMeta.dataset.tone = "success"
  } else {
    exportPreviewMeta.textContent = ""
    exportPreviewMeta.dataset.tone = "neutral"
  }

  const preview = state.exportPreview
  if (!preview) {
    exportPreviewThumbs.innerHTML = ""
    exportPreviewContent.innerHTML =
      '<div class="empty-state">Preview pending. Adjust the selection and wait for the exact artifact list.</div>'
    return
  }

  exportPreviewThumbs.innerHTML = renderExportPreviewThumbs(preview.plot_previews || [])
  exportPreviewContent.innerHTML = renderExportPreviewArtifacts(preview.artifacts || [])
}

function resultTile(label, value, variant, tooltip = "") {
  const tooltipMarkup = tooltip
    ? ` <span class="tooltip-icon" data-tooltip="${escapeHtml(tooltip)}">?</span>`
    : ""
  return `<div class="result-tile ${variant}"><span class="results-label">${escapeHtml(label)}${tooltipMarkup}</span><strong>${escapeHtml(value)}</strong></div>`
}

function renderMeasurementCard(label, value, { uncertainty = null, method = null, flags = [], details = "" } = {}) {
  const chips = Array.isArray(flags) && flags.length
    ? `<div class="measurement-flags">${flags.map((flag) => infoChip("Flag", formatMeasurementFlag(flag), flagTone(flag))).join("")}</div>`
    : ""
  const meta = [
    method ? `<div class="measurement-meta"><span>Method</span><strong>${escapeHtml(String(method))}</strong></div>` : "",
    uncertainty ? `<div class="measurement-meta"><span>Uncertainty</span><strong>${escapeHtml(String(uncertainty))}</strong></div>` : "",
  ].join("")
  return `
    <article class="measurement-card">
      <div class="measurement-head">
        <span class="results-label">${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
      ${meta ? `<div class="measurement-meta-grid">${meta}</div>` : ""}
      ${chips}
      ${details}
    </article>
  `
}

function renderMeasurementDetails(provenance) {
  if (!provenance) {
    return ""
  }
  return `
    <details class="details-card measurement-details">
      <summary>Why this value?</summary>
      <div class="kv-list">
        ${renderProvenanceItems(provenance)}
      </div>
    </details>
  `
}

function renderAcceptedWidthDetails(widthAnalysis, acceptedWidth, provenance, hasAcfFallback = false) {
  const result = widthAnalysis?.results?.find((item) => item.method === acceptedWidth?.method)
  const items = []
  if (acceptedWidth?.method) {
    items.push(["Accepted method", formatWidthMethod(acceptedWidth.method)])
  }
  if (hasAcfFallback) {
    items.push(["Shown value", "ACF correlation-width fallback"])
    items.push(["Interpretation", "Tracks coherence scale, not full burst extent"])
  }
  if (result?.algorithm_name) {
    items.push(["Algorithm", result.algorithm_name])
  }
  if (Array.isArray(result?.event_window_ms) && result.event_window_ms.length === 2) {
    items.push(["Event window", `${fmt(result.event_window_ms[0], 3)} to ${fmt(result.event_window_ms[1], 3)} ms`])
  }
  if (Array.isArray(result?.spectral_extent_mhz) && result.spectral_extent_mhz.length === 2) {
    items.push(["Spectral window", `${fmt(result.spectral_extent_mhz[0], 3)} to ${fmt(result.spectral_extent_mhz[1], 3)} MHz`])
  }
  if (Array.isArray(result?.offpulse_windows_ms) && result.offpulse_windows_ms.length) {
    items.push(["Off-pulse", result.offpulse_windows_ms.map((window) => `${fmt(window[0], 3)} to ${fmt(window[1], 3)} ms`).join(", ")])
  } else if (provenance?.noise_basis) {
    items.push(["Off-pulse", provenance.noise_basis])
  }
  const body = items
    .map(([key, current]) => `<div class="kv-item"><span>${escapeHtml(key)}</span><strong>${escapeHtml(String(current))}</strong></div>`)
    .join("")
  if (!body) {
    return ""
  }
  return `
    <details class="details-card measurement-details">
      <summary>Why this value?</summary>
      <div class="kv-list">${body}</div>
    </details>
  `
}

function renderWidthAnalysisSection(widthAnalysis) {
  if (!widthAnalysis) {
    return `
      <section class="results-section width-section">
        <div class="analysis-panel-head compact">
          <h5>Width Comparison</h5>
          <p>Compute the boxcar, Gaussian, and fluence-percentile widths for the current burst.</p>
        </div>
        <div class="export-actions">
          <button class="primary-button" id="computeWidthsButton">Compute Widths</button>
        </div>
        <div class="empty-state">No width comparison yet. Compute widths to compare methods and accept one for export.</div>
      </section>
    `
  }

  const rows = widthAnalysis.results.map((result) => {
    const isAccepted = widthAnalysis.accepted_width?.method === result.method
    const flags = Array.isArray(result.quality_flags) && result.quality_flags.length
      ? result.quality_flags.map((flag) => infoChip("Flag", formatMeasurementFlag(flag), flagTone(flag))).join("")
      : infoChip("Flag", "None", "neutral")
    const action = isAccepted
      ? `<span class="panel-badge" data-tone="success">Accepted</span>`
      : `<button class="ghost-button width-accept-button" data-width-method="${escapeHtml(result.method)}">Accept</button>`
    return `
      <div class="width-row">
        <div class="width-copy">
          <strong>${escapeHtml(result.label)}</strong>
          <span>${escapeHtml(result.value === null || result.value === undefined ? "n/a" : `${fmt(result.value, 3)} ${result.units || "ms"}`)}</span>
          <span>${escapeHtml(result.uncertainty === null || result.uncertainty === undefined ? "uncertainty n/a" : `±${fmt(result.uncertainty, 3)} ${result.units || "ms"}`)}</span>
        </div>
        <div class="width-flags">${flags}</div>
        <div class="width-actions">${action}</div>
      </div>
    `
  }).join("")

  return `
    <section class="results-section width-section">
      <div class="analysis-panel-head compact">
        <h5>Width Comparison</h5>
        <p>Compare multiple time-scale definitions. The accepted width is stored in the session snapshot and results export.</p>
      </div>
      <div class="export-actions">
        <button class="primary-button" id="computeWidthsButton">Recompute Widths</button>
      </div>
      <div class="width-list">
        ${rows}
      </div>
    </section>
  `
}

function bindWidthActionButtons() {
  const computeWidthsButton = document.getElementById("computeWidthsButton")
  if (computeWidthsButton) {
    computeWidthsButton.disabled = !state.sessionId || Boolean(state.busyAction)
    computeWidthsButton.addEventListener("click", () => postAction("compute_widths"))
  }
  document.querySelectorAll(".width-accept-button").forEach((button) => {
    button.disabled = !state.sessionId || Boolean(state.busyAction)
    button.addEventListener("click", () => {
      postAction("accept_width_result", { method: button.dataset.widthMethod })
    })
  })
}

function acceptedWidthFlags(widthAnalysis, acceptedWidth) {
  const flags = widthAnalysis?.results?.find((result) => result.method === acceptedWidth?.method)?.quality_flags
  return Array.isArray(flags) ? flags : []
}

function formatWidthMethod(method) {
  const labels = {
    boxcar_equivalent: "Boxcar equivalent",
    gaussian_sigma: "Gaussian sigma",
    gaussian_fwhm: "Gaussian FWHM",
    fluence_percentile: "Fluence percentile",
    acf_half_max: "ACF half max",
  }
  return labels[method] || method || "n/a"
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
        line: { color: plotTheme.charcoalSoft, width: 2.2 },
        hovertemplate: "%{x:.3f} ms<br>%{y:.3f}<extra></extra>",
      },
      {
        x: view.plot.heatmap.x_ms,
        y: view.plot.heatmap.y_mhz,
        z: view.plot.heatmap.z,
        xaxis: "x",
        yaxis: "y",
        type: "heatmap",
        colorscale: plotTheme.heatmapScale,
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
        line: { color: plotTheme.accentAlt, width: 2.2 },
        hovertemplate: "%{x:.3f}<br>%{y:.3f} MHz<extra></extra>",
      },
    ],
    {
      margin: { l: 76, r: 118, t: 18, b: 54 },
      paper_bgcolor: plotTheme.paperBg,
      plot_bgcolor: plotTheme.plotBg,
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
        gridcolor: plotTheme.grid,
      },
      yaxis2: {
        domain: viewerDomains.time.y,
        anchor: "x2",
        title: "Summed Intensity",
        automargin: true,
        gridcolor: plotTheme.grid,
      },
      xaxis3: {
        domain: viewerDomains.spectrum.x,
        anchor: "y3",
        title: "Summed Intensity",
        automargin: true,
        gridcolor: plotTheme.grid,
      },
      yaxis3: {
        domain: viewerDomains.spectrum.y,
        anchor: "x3",
        matches: "y",
        showticklabels: false,
        gridcolor: plotTheme.grid,
      },
      shapes: buildViewerShapes(view),
      annotations: buildViewerAnnotations(),
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )

  bindPlotEvents()
}

async function renderDmOptimizationPlot(optimization, appliedDm) {
  const componentCurves = Array.isArray(optimization.component_results)
    ? optimization.component_results.map((component) => component.metric_values || [])
    : []
  const yRange = dmOptimizationYRange([optimization.snr, ...componentCurves].flat())
  const traces = []
  const snrLabel = snrMetricLabel(optimization.snr_metric)

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
      line: { width: 0, color: "rgba(114, 53, 162, 0)" },
      fillcolor: plotTheme.accentSoft,
      hoverinfo: "skip",
      showlegend: false,
    })
  }

  traces.push({
    x: optimization.trial_dms,
    y: optimization.snr,
    mode: "lines+markers",
    type: "scattergl",
    line: { color: plotTheme.accent, width: 2.5 },
    marker: { color: plotTheme.accentStrong, size: 8 },
    hovertemplate: `DM %{x:.6f}<br>${escapeHtml(snrLabel)} %{y:.3f}<extra></extra>`,
    name: `${snrLabel} sweep`,
  })

  ;(optimization.component_results || []).forEach((component, index) => {
    traces.push({
      x: component.trial_dms,
      y: component.metric_values,
      mode: "lines",
      type: "scattergl",
      name: component.label || `Component ${index + 1}`,
      line: {
        color: dmComponentColor(index),
        width: 1.6,
        dash: "dot",
      },
      opacity: 0.75,
      hovertemplate: `DM %{x:.6f}<br>${escapeHtml(component.label || `Component ${index + 1}`)} ${escapeHtml(snrLabel)} %{y:.3f}<extra></extra>`,
    })
  })

  await Plotly.react(
    "dmOptimizationPlot",
    traces,
    {
      margin: { l: 70, r: 24, t: 18, b: 54 },
      paper_bgcolor: plotTheme.paperBg,
      plot_bgcolor: plotTheme.plotBg,
      showlegend: traces.length > 2,
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "right",
        x: 1.0,
      },
      hovermode: "closest",
      xaxis: {
        title: "Dispersion Measure",
        automargin: true,
        gridcolor: plotTheme.grid,
      },
      yaxis: {
        title: snrLabel,
        automargin: true,
        range: yRange,
        gridcolor: plotTheme.grid,
      },
      shapes: buildDmOptimizationShapes(optimization, appliedDm, yRange),
      annotations: buildDmOptimizationAnnotations(optimization, yRange),
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )
}

function syncDmPlots() {
  const optimization = state.view?.dm_optimization
  if (!optimization || state.activeAnalysisTab !== "dm") {
    return
  }
  dmOptimizationPlot.classList.toggle("is-empty", !optimization)
  renderDmOptimizationPlot(optimization, optimization.applied_dm)
  renderDmResidualPlot(optimization)
}

function syncFittingPlot() {
  const scatteringFit = state.view?.results?.diagnostics?.scattering_fit
  if (!scatteringFit || state.activeAnalysisTab !== "fitting") {
    return
  }
  renderFittingSpectrumPlot(scatteringFit)
  renderFittingProfilePlot(scatteringFit)
}

function syncSpectralPlot() {
  const temporalStructure = state.view?.temporal_structure
  if (!temporalStructure || state.activeAnalysisTab !== "temporal" || temporalStructure.status !== "ok") {
    temporalScalePlot.classList.add("is-empty")
    Plotly.purge(temporalScalePlot)
    temporalScalePlot.replaceChildren()
    spectralPlot.classList.add("is-empty")
    Plotly.purge(spectralPlot)
    spectralPlot.replaceChildren()
    return
  }
  renderTemporalScalePlot(temporalStructure)
  renderSpectralPlot(temporalStructure)
}

async function renderDmResidualPlot(optimization) {
  if (optimization.residual_status !== "ok") {
    dmResidualPlot.classList.add("is-empty")
    Plotly.purge(dmResidualPlot)
    dmResidualPlot.replaceChildren()
    return
  }

  dmResidualPlot.classList.remove("is-empty")
  await Plotly.react(
    "dmResidualPlot",
    [
      {
        x: optimization.subband_freqs_mhz,
        y: optimization.residuals_applied_ms,
        mode: "lines+markers",
        type: "scattergl",
        name: "Sweep applied DM",
        line: { color: plotTheme.accentAlt, width: 2.2 },
        marker: { color: plotTheme.accentAltStrong, size: 8 },
        hovertemplate: "%{x:.3f} MHz<br>Residual %{y:.4f} ms<extra>Sweep applied DM</extra>",
      },
      {
        x: optimization.subband_freqs_mhz,
        y: optimization.residuals_best_ms,
        mode: "lines+markers",
        type: "scattergl",
        name: "Best-fit DM",
        line: { color: plotTheme.accent, width: 2.4 },
        marker: { color: plotTheme.accentStrong, size: 8 },
        hovertemplate: "%{x:.3f} MHz<br>Residual %{y:.4f} ms<extra>Best-fit DM</extra>",
      },
    ],
    {
      margin: { l: 70, r: 24, t: 18, b: 54 },
      paper_bgcolor: plotTheme.paperBg,
      plot_bgcolor: plotTheme.plotBg,
      showlegend: true,
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "right",
        x: 1.0,
      },
      hovermode: "closest",
      xaxis: {
        title: "Sub-band Center Frequency (MHz)",
        automargin: true,
        gridcolor: plotTheme.grid,
      },
      yaxis: {
        title: "Arrival-time Residual (ms)",
        automargin: true,
        zeroline: true,
        zerolinecolor: plotTheme.gridStrong,
        gridcolor: plotTheme.grid,
      },
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )
}

async function renderFittingProfilePlot(scatteringFit) {
  // Profile plot is now integrated into the spectrum plot as marginals
  fittingProfilePlot.classList.add("is-empty")
  Plotly.purge(fittingProfilePlot)
  fittingProfilePlot.replaceChildren()
}

async function renderFittingSpectrumPlot(scatteringFit) {
  const dataSpectrum = scatteringFit?.data_dynamic_spectrum_sn
  const modelSpectrum = scatteringFit?.model_dynamic_spectrum_sn
  const residualSpectrum = scatteringFit?.residual_dynamic_spectrum_sn
  const freqAxis = scatteringFit?.freq_axis_mhz
  const timeAxis = scatteringFit?.time_axis_ms
  const dataProfile = scatteringFit?.data_profile_sn
  const modelProfile = scatteringFit?.model_profile_sn
  const residualProfile = scatteringFit?.residual_profile_sn
  const dataFreqProfile = scatteringFit?.data_freq_profile_sn
  const modelFreqProfile = scatteringFit?.model_freq_profile_sn
  const residualFreqProfile = scatteringFit?.residual_freq_profile_sn

  if (
    !scatteringFit
    || scatteringFit.status !== "ok"
    || !Array.isArray(dataSpectrum)
    || !Array.isArray(modelSpectrum)
    || !Array.isArray(residualSpectrum)
    || !Array.isArray(freqAxis)
    || !Array.isArray(timeAxis)
    || !dataSpectrum.length
    || !modelSpectrum.length
    || !residualSpectrum.length
  ) {
    fittingSpectrumPlot.classList.add("is-empty")
    Plotly.purge(fittingSpectrumPlot)
    fittingSpectrumPlot.replaceChildren()
    return
  }

  fittingSpectrumPlot.classList.remove("is-empty")
  const dataModelRange = combinedQuantileRange([dataSpectrum, modelSpectrum], 0.02, 0.995)
  const residualRange = symmetricQuantileRange(residualSpectrum, 0.995)

  // Domain constants for tight 3-column layout with marginals
  const colGap = 0.04
  const freqMarginW = 0.05
  const colW = (1.0 - 2 * colGap - 3 * freqMarginW) / 3
  const col1Start = 0.0
  const col1End = col1Start + colW
  const freq1Start = col1End + 0.005
  const freq1End = freq1Start + freqMarginW
  const col2Start = freq1End + colGap
  const col2End = col2Start + colW
  const freq2Start = col2End + 0.005
  const freq2End = freq2Start + freqMarginW
  const col3Start = freq2End + colGap
  const col3End = col3Start + colW
  const freq3Start = col3End + 0.005
  const freq3End = freq3Start + freqMarginW

  const heatmapBottom = 0.0
  const heatmapTop = 0.74
  const profileBottom = 0.78
  const profileTop = 1.0

  const traces = [
    // --- Heatmaps ---
    {
      x: timeAxis, y: freqAxis, z: dataSpectrum,
      type: "heatmap", xaxis: "x", yaxis: "y", coloraxis: "coloraxis",
      hovertemplate: "%{x:.3f} ms<br>%{y:.3f} MHz<br>%{z:.3f}<extra>Data</extra>",
    },
    {
      x: timeAxis, y: freqAxis, z: modelSpectrum,
      type: "heatmap", xaxis: "x2", yaxis: "y2", coloraxis: "coloraxis",
      hovertemplate: "%{x:.3f} ms<br>%{y:.3f} MHz<br>%{z:.3f}<extra>Model</extra>",
    },
    {
      x: timeAxis, y: freqAxis, z: residualSpectrum,
      type: "heatmap", xaxis: "x3", yaxis: "y3", coloraxis: "coloraxis2",
      hovertemplate: "%{x:.3f} ms<br>%{y:.3f} MHz<br>%{z:.3f}<extra>Residual</extra>",
    },
    // --- Time profiles (top marginals) ---
    {
      x: timeAxis, y: dataProfile,
      type: "scatter", mode: "lines",
      line: { color: plotTheme.charcoalSoft, width: 1.5 },
      xaxis: "x", yaxis: "y4",
      hovertemplate: "%{x:.3f} ms<br>%{y:.3f}<extra>Data</extra>",
      showlegend: false,
    },
    {
      x: timeAxis, y: modelProfile,
      type: "scatter", mode: "lines",
      line: { color: plotTheme.accent, width: 1.5 },
      xaxis: "x", yaxis: "y4",
      hovertemplate: "%{x:.3f} ms<br>%{y:.3f}<extra>Model</extra>",
      showlegend: false,
    },
    {
      x: timeAxis, y: modelProfile,
      type: "scatter", mode: "lines",
      line: { color: plotTheme.accent, width: 1.5 },
      xaxis: "x2", yaxis: "y5",
      hovertemplate: "%{x:.3f} ms<br>%{y:.3f}<extra>Model</extra>",
      showlegend: false,
    },
    {
      x: timeAxis, y: residualProfile,
      type: "scatter", mode: "lines",
      line: { color: plotTheme.accentAlt, width: 1.5, dash: "dot" },
      xaxis: "x3", yaxis: "y6",
      hovertemplate: "%{x:.3f} ms<br>%{y:.3f}<extra>Residual</extra>",
      showlegend: false,
    },
    // --- Freq profiles (right marginals) ---
    {
      x: dataFreqProfile, y: freqAxis,
      type: "scatter", mode: "lines",
      line: { color: plotTheme.charcoalSoft, width: 1.2 },
      xaxis: "x4", yaxis: "y",
      hovertemplate: "%{y:.3f} MHz<br>%{x:.3f}<extra>Data</extra>",
      showlegend: false,
    },
    {
      x: modelFreqProfile, y: freqAxis,
      type: "scatter", mode: "lines",
      line: { color: plotTheme.accent, width: 1.2 },
      xaxis: "x5", yaxis: "y2",
      hovertemplate: "%{y:.3f} MHz<br>%{x:.3f}<extra>Model</extra>",
      showlegend: false,
    },
    {
      x: residualFreqProfile, y: freqAxis,
      type: "scatter", mode: "lines",
      line: { color: plotTheme.accentAlt, width: 1.2, dash: "dot" },
      xaxis: "x6", yaxis: "y3",
      hovertemplate: "%{y:.3f} MHz<br>%{x:.3f}<extra>Residual</extra>",
      showlegend: false,
    },
  ]

  const gridColor = plotTheme.grid
  await Plotly.react(
    "fittingSpectrumPlot",
    traces,
    {
      margin: { l: 78, r: 24, t: 32, b: 54 },
      paper_bgcolor: plotTheme.paperBg,
      plot_bgcolor: plotTheme.plotBg,
      showlegend: false,
      hovermode: "closest",
      // Heatmap x-axes
      xaxis:  { domain: [col1Start, col1End], anchor: "y",  title: "Time (ms)", automargin: true, gridcolor: gridColor },
      xaxis2: { domain: [col2Start, col2End], anchor: "y2", title: "Time (ms)", automargin: true, gridcolor: gridColor },
      xaxis3: { domain: [col3Start, col3End], anchor: "y3", title: "Time (ms)", automargin: true, gridcolor: gridColor },
      // Freq marginal x-axes (right of each heatmap)
      xaxis4: { domain: [freq1Start, freq1End], anchor: "y",  showticklabels: false, zeroline: false, gridcolor: gridColor },
      xaxis5: { domain: [freq2Start, freq2End], anchor: "y2", showticklabels: false, zeroline: false, gridcolor: gridColor },
      xaxis6: { domain: [freq3Start, freq3End], anchor: "y3", showticklabels: false, zeroline: false, gridcolor: gridColor },
      // Heatmap y-axes
      yaxis:  { domain: [heatmapBottom, heatmapTop], anchor: "x",  title: "Frequency (MHz)", automargin: true, gridcolor: gridColor },
      yaxis2: { domain: [heatmapBottom, heatmapTop], anchor: "x2", matches: "y", showticklabels: false, gridcolor: gridColor },
      yaxis3: { domain: [heatmapBottom, heatmapTop], anchor: "x3", matches: "y", showticklabels: false, gridcolor: gridColor },
      // Time marginal y-axes (top of each heatmap)
      yaxis4: { domain: [profileBottom, profileTop], anchor: "x",  showticklabels: false, zeroline: true, zerolinecolor: plotTheme.gridStrong, gridcolor: gridColor },
      yaxis5: { domain: [profileBottom, profileTop], anchor: "x2", showticklabels: false, zeroline: true, zerolinecolor: plotTheme.gridStrong, gridcolor: gridColor },
      yaxis6: { domain: [profileBottom, profileTop], anchor: "x3", showticklabels: false, zeroline: true, zerolinecolor: plotTheme.gridStrong, gridcolor: gridColor },
      coloraxis: {
        colorscale: plotTheme.heatmapScale,
        cmin: dataModelRange[0],
        cmax: dataModelRange[1],
        colorbar: { title: { text: "Data / Model" }, x: 1.02, y: 0.37, len: 0.35, thickness: 12 },
      },
      coloraxis2: {
        colorscale: plotTheme.residualScale,
        cmin: residualRange[0],
        cmax: residualRange[1],
        colorbar: { title: { text: "Residual" }, x: 1.02, y: 0.04, len: 0.35, thickness: 12 },
      },
      annotations: [
        panelLabel("Data", col1Start, 1.0),
        panelLabel("Model", col2Start, 1.0),
        panelLabel("Residual", col3Start, 1.0),
      ],
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] },
  )
}

function renderResidualTable(optimization) {
  if (optimization.residual_status !== "ok") {
    return ""
  }

  const rows = optimization.subband_freqs_mhz.map((freq, index) => (
    `<tr>` +
    `<td>${escapeHtml(`${fmt(freq, 3)} MHz`)}</td>` +
    `<td>${escapeHtml(`${fmt(optimization.arrival_times_applied_ms[index], 4)} ms`)}</td>` +
    `<td>${escapeHtml(`${fmt(optimization.residuals_applied_ms[index], 4)} ms`)}</td>` +
    `<td>${escapeHtml(`${fmt(optimization.arrival_times_best_ms[index], 4)} ms`)}</td>` +
    `<td>${escapeHtml(`${fmt(optimization.residuals_best_ms[index], 4)} ms`)}</td>` +
    `</tr>`
  )).join("")

  return `
    <div class="residual-table-wrap">
      <table class="residual-table">
        <thead>
          <tr>
            <th>Sub-band</th>
            <th>Applied Arrival</th>
            <th>Applied Residual</th>
            <th>Best-fit Arrival</th>
            <th>Best-fit Residual</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
      </table>
    </div>
  `
}

function renderScatteringParameterTable(parameters, uncertainties) {
  const rows = [
    parameterRow("Arrival Time", parameters.arrival_time?.[0], uncertainties.arrival_time?.[0], 1e3, "ms"),
    parameterRow("Intrinsic Width", parameters.burst_width?.[0], uncertainties.burst_width?.[0], 1e3, "ms"),
    parameterRow("Scattering Tau", parameters.scattering_timescale?.[0], uncertainties.scattering_timescale?.[0], 1e3, "ms"),
    parameterRow("Log Amplitude", parameters.amplitude?.[0], uncertainties.amplitude?.[0], 1.0, ""),
    parameterRow("DM", parameters.dm?.[0], uncertainties.dm?.[0], 1.0, "pc/cm³"),
    parameterRow("DM Index", parameters.dm_index?.[0], uncertainties.dm_index?.[0], 1.0, ""),
    parameterRow("Spectral Index", parameters.spectral_index?.[0], uncertainties.spectral_index?.[0], 1.0, ""),
    parameterRow("Spectral Running", parameters.spectral_running?.[0], uncertainties.spectral_running?.[0], 1.0, ""),
    parameterRow("Scattering Index", parameters.scattering_index?.[0], uncertainties.scattering_index?.[0], 1.0, ""),
  ].filter(Boolean)

  if (!rows.length) {
    return ""
  }

  return `
    <details class="details-card results-details" open>
      <summary>All Best-Fit Parameters</summary>
      <div class="residual-table-wrap">
        <table class="residual-table">
          <thead>
            <tr>
              <th>Parameter</th>
              <th>Best Fit</th>
              <th>Uncertainty</th>
            </tr>
          </thead>
          <tbody>
            ${rows.join("")}
          </tbody>
        </table>
      </div>
    </details>
  `
}

function parameterRow(label, value, uncertainty, scale, unit) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return ""
  }
  const scaledValue = Number(value) * scale
  const scaledUncertainty = uncertainty === null || uncertainty === undefined || Number.isNaN(Number(uncertainty))
    ? "n/a"
    : `±${fmt(Number(uncertainty) * scale, 3)}${unit ? ` ${unit}` : ""}`
  return (
    `<tr>` +
    `<td>${escapeHtml(label)}</td>` +
    `<td>${escapeHtml(`${fmt(scaledValue, 3)}${unit ? ` ${unit}` : ""}`)}</td>` +
    `<td>${escapeHtml(scaledUncertainty)}</td>` +
    `</tr>`
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
    if (state.mode === "event") {
      handleTimePlotClick(Number(point.x))
    } else {
      handleFreqPlotClick(Number(point.y), true)
    }
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
  } else if (state.mode === "offpulse") {
    handlePair("add_offpulse", timeMs, "ms")
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

  if (action === "set_crop" || action === "set_event" || action === "add_region" || action === "add_offpulse") {
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

function setAnalysisTab(tab) {
  const normalizedTab = ["prepare", "dm", "fitting", "temporal", "export"].includes(tab) ? tab : "prepare"
  state.activeAnalysisTab = normalizedTab
  analysisTabButtons.forEach((button) => {
    const isActive = button.dataset.analysisTab === normalizedTab
    button.classList.toggle("active", isActive)
    button.setAttribute("aria-selected", String(isActive))
  })
  analysisPanels.forEach((panel) => {
    const isActive = panel.dataset.tabPanel === normalizedTab
    panel.classList.toggle("active", isActive)
    panel.hidden = !isActive
  })
  if (window.location.hash !== `#${normalizedTab}`) {
    window.history.replaceState(null, "", `#${normalizedTab}`)
  }
  if (normalizedTab === "dm") {
    syncDmPlots()
  } else if (normalizedTab === "fitting") {
    syncFittingPlot()
  } else if (normalizedTab === "temporal") {
    syncSpectralPlot()
  } else if (normalizedTab === "export" && state.sessionId && exportSelectionCount() > 0 && (state.exportPreviewStale || !state.exportPreview)) {
    scheduleExportPreview({ immediate: true })
  }
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
    verticalLine(optimization.center_dm, yRange[0], yRange[1], plotTheme.neutral, "dot"),
    verticalLine(optimization.sampled_best_dm, yRange[0], yRange[1], plotTheme.accentAlt, "dash"),
    verticalLine(optimization.best_dm, yRange[0], yRange[1], plotTheme.accent, "solid"),
    verticalLine(appliedDm, yRange[0], yRange[1], plotTheme.alert, "dot"),
  ]
}

function buildDmOptimizationAnnotations(optimization, yRange) {
  const y = yRange[1]
  return [
    dmOptimizationLabel("Center", optimization.center_dm, y, plotTheme.neutral),
    dmOptimizationLabel("Sampled", optimization.sampled_best_dm, y, plotTheme.accentAlt),
    dmOptimizationLabel("Best", optimization.best_dm, y, plotTheme.accent),
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
    bgcolor: plotTheme.annotationBg,
    bordercolor: plotTheme.annotationBorder,
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
      color: plotTheme.muted,
    },
  }
}

function buildTimeShapes(view) {
  const yMin = minFinite(view.plot.time_profile.y)
  const yMax = maxFinite(view.plot.time_profile.y)
  const shapes = [
    verticalLine(view.state.event_ms[0], yMin, yMax, plotTheme.accentAlt, "solid", "x2", "y2"),
    verticalLine(view.state.event_ms[1], yMin, yMax, plotTheme.accentAlt, "solid", "x2", "y2"),
  ]

  for (const region of view.state.offpulse_ms || []) {
    shapes.push({
      type: "rect",
      xref: "x2",
      yref: "y2",
      x0: region[0],
      x1: region[1],
      y0: yMin,
      y1: yMax,
      fillcolor: plotTheme.accentAltSoft,
      line: { width: 0 },
    })
  }

  for (const region of view.state.burst_regions_ms) {
    shapes.push({
      type: "rect",
      xref: "x2",
      yref: "y2",
      x0: region[0],
      x1: region[1],
      y0: yMin,
      y1: yMax,
      fillcolor: plotTheme.warningSoft,
      line: { width: 0 },
    })
  }

  for (const peak of view.state.peak_ms) {
    shapes.push(verticalLine(peak, yMin, yMax, plotTheme.alert, "dot", "x2", "y2"))
  }
  return shapes
}

function buildHeatmapShapes(view) {
  const x0 = view.state.crop_ms[0]
  const x1 = view.state.crop_ms[1]
  const y0 = view.meta.freq_range_mhz[0]
  const y1 = view.meta.freq_range_mhz[1]
  const shapes = [
    verticalLine(view.state.event_ms[0], y0, y1, plotTheme.accentAlt, "solid", "x", "y"),
    verticalLine(view.state.event_ms[1], y0, y1, plotTheme.accentAlt, "solid", "x", "y"),
    horizontalLine(view.state.spectral_extent_mhz[0], x0, x1, plotTheme.accent, "solid", "x", "y"),
    horizontalLine(view.state.spectral_extent_mhz[1], x0, x1, plotTheme.accent, "solid", "x", "y"),
  ]

  for (const region of view.state.offpulse_ms || []) {
    shapes.push({
      type: "rect",
      xref: "x",
      yref: "y",
      x0: region[0],
      x1: region[1],
      y0,
      y1,
      fillcolor: plotTheme.accentAltSoft,
      line: { width: 0 },
    })
  }

  for (const peak of view.state.peak_ms) {
    shapes.push(verticalLine(peak, y0, y1, plotTheme.alert, "dot", "x", "y"))
  }
  return shapes
}

function buildSpectrumShapes(view) {
  const xMin = minFinite(view.plot.spectrum.x)
  const xMax = maxFinite(view.plot.spectrum.x)
  return [
    horizontalLine(view.state.spectral_extent_mhz[0], xMin, xMax, plotTheme.accent, "solid", "x3", "y3"),
    horizontalLine(view.state.spectral_extent_mhz[1], xMin, xMax, plotTheme.accent, "solid", "x3", "y3"),
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

  notesInput.disabled = !hasSession || isBusy
  dmMetricInput.disabled = !hasSession || isBusy
  exportPlotPng.disabled = !hasSession || isBusy || !state.exportSelection.include.includes("plots")
  exportPlotSvg.disabled = !hasSession || isBusy || !state.exportSelection.include.includes("plots")
  exportWindowNpz.disabled = !hasSession || isBusy || !state.exportSelection.include.includes("window")
  exportWindowFil.disabled = !hasSession || isBusy || !state.exportSelection.include.includes("window")
  exportWindowNative.disabled = !hasSession || isBusy || !state.exportSelection.include.includes("window")
  exportWindowView.disabled = !hasSession || isBusy || !state.exportSelection.include.includes("window")
  buildExportButton.disabled = !hasSession || isBusy || exportSelectionCount() === 0

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
  if (action === "export_session") return exportSessionButton
  if (action === "import_session") return exportSessionButton
  if (action === "compute_properties") return computeButton
  if (action === "auto_mask_jess") return jessButton
  if (action === "optimize_dm") return optimizeDmButton
  if (action === "fit_scattering") return fitScatteringButton
  if (action === "run_temporal_structure_analysis") return runSpectralButton
  if (action === "run_spectral_analysis") return runSpectralButton
  if (action === "export_results") return buildExportButton
  if (action === "set_notes") return saveNotesButton
  if (action === "set_dm") return setDmButton
  if (action === "reset_view") return resetViewButton
  return null
}

function busyButtonText(action) {
  if (action === "load") return "Loading..."
  if (action === "export_session") return "Exporting..."
  if (action === "import_session") return "Importing..."
  if (action === "compute_properties") return "Computing..."
  if (action === "auto_mask_jess") return "Masking..."
  if (action === "optimize_dm") return "Sweeping DM..."
  if (action === "fit_scattering") return "Fitting..."
  if (action === "run_temporal_structure_analysis") return "Running..."
  if (action === "run_spectral_analysis") return "Running..."
  if (action === "export_results") return "Building..."
  if (action === "set_notes") return "Saving..."
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
    add_offpulse: "Adding off-pulse window",
    add_region: "Adding component region",
    clear_regions: "Clearing components",
    clear_offpulse: "Clearing off-pulse windows",
    add_peak: "Adding peak",
    remove_peak: "Removing peak",
    mask_channel: "Masking channel",
    mask_range: "Masking range",
    undo_mask: "Undoing mask",
    reset_mask: "Resetting masks",
    set_spectral_extent: "Setting spectral window",
    auto_mask_jess: "Auto masking",
    optimize_dm: "Sweeping DM",
    compute_widths: "Computing width comparison",
    accept_width_result: "Accepting width result",
    fit_scattering: "Running scattering fit",
    run_temporal_structure_analysis: "Running temporal structure",
    run_spectral_analysis: "Running power spectrum",
    export_results: "Building export bundle",
    set_notes: "Saving notes",
    set_dm: "Applying DM",
    compute_properties: "Computing",
  }
  return labels[action] || "Updating"
}

function actionSuccessText(action) {
  const labels = {
    reset_view: "View reset",
    clear_regions: "Component regions cleared",
    clear_offpulse: "Off-pulse windows cleared",
    undo_mask: "Last mask removed",
    reset_mask: "All masks cleared",
    optimize_dm: "DM sweep completed",
    compute_widths: "Width comparison updated",
    accept_width_result: "Accepted width updated",
    fit_scattering: "Scattering fit completed",
    run_temporal_structure_analysis: "Temporal structure updated",
    run_spectral_analysis: "Power spectrum updated",
    export_results: "Export bundle built",
    set_notes: "Notes saved",
    set_dm: "Dispersion measure updated",
    compute_properties: "Derived properties updated",
  }
  return labels[action] || null
}

function autoMaskToastText(summary) {
  if (!summary) {
    return "Auto mask applied"
  }
  const channelLabel = summary.added_channel_count === 1 ? "channel" : "channels"
  const sampleLabel = `${fmt(summary.sampled_time_bins, 0)}/${fmt(summary.candidate_time_bins, 0)} bins`
  const testLabel = summary.test_used ? `, ${summary.test_used}` : ""
  return `Auto mask added ${summary.added_channel_count} ${channelLabel} with ${summary.profile_label} (${sampleLabel}${testLabel})`
}

function formatAutoMaskSummary(summary) {
  if (!summary) {
    return "not run"
  }
  const sampleLabel = summary.candidate_time_bins
    ? `${fmt(summary.sampled_time_bins, 0)} / ${fmt(summary.candidate_time_bins, 0)} bins`
    : "no off-burst bins"
  const constantLabel = summary.constant_channel_count ? `, ${summary.constant_channel_count} constant` : ""
  const testLabel = summary.test_used ? `, ${summary.test_used}` : ""
  return `${summary.profile_label}, ${sampleLabel}, +${summary.added_channel_count}${constantLabel}${testLabel}`
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
  const changed = telescopeInput.value !== presetKey
  syncingPresetSelection = true
  telescopeInput.value = presetKey
  syncingPresetSelection = false
  if (changed) {
    syncPresetDefaults()
  }
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
  readStartInput.value = String(preset.read_start_sec)
  if (preset.read_end_sec !== null) {
    readEndInput.value = String(preset.read_end_sec)
    readEndInput.placeholder = ""
  } else {
    readEndInput.value = ""
    readEndInput.placeholder = "full file"
  }
}

function parseOptionalNumber(value) {
  if (value === null || value === undefined) return null
  const trimmed = String(value).trim().replace(",", ".")
  if (trimmed === "") return null
  const num = Number(trimmed)
  return isNaN(num) ? null : num
}

function parseOptionalInteger(value) {
  const num = parseOptionalNumber(value)
  if (num === null) return null
  return Math.max(1, Math.round(num))
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

function formatScientific(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "n/a"
  }
  return Number(value).toExponential(digits)
}

function formatUtcTimestamp(value) {
  if (!value) {
    return "n/a"
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) {
    return value
  }
  return parsed.toISOString().replace(".000Z", "Z")
}

function formatIsoEnergy(value, unit = "erg") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "n/a"
  }
  return `${formatScientific(value, 3)} ${unit || ""}`.trim()
}

function formatToaUncertainty(daysValue) {
  if (daysValue === null || daysValue === undefined || Number.isNaN(Number(daysValue))) {
    return "n/a"
  }
  const microseconds = Number(daysValue) * 86400 * 1e6
  return `±${fmt(microseconds, 3)} us`
}

function snrMetricLabel(metric) {
  const labels = {
    integrated_event_snr: "Integrated Event S/N",
    dm_phase: "DMphase",
    peak_snr: "Peak S/N",
    profile_sharpness: "Profile Sharpness",
    burst_compactness: "Burst Compactness",
    minimal_residual_drift: "Residual Drift Metric",
    maximal_structure: "Structure Metric",
  }
  return labels[metric] || "DM Metric"
}

function snrMetricSummary(metric) {
  const labels = {
    integrated_event_snr: "Sum the event-profile S/N across the selected event window. This is the legacy FLITS DM sweep metric.",
    dm_phase: "Use the automatic DMphase coherent-power curve on the selected reduced waterfall.",
    peak_snr: "Use the single highest-S/N time bin inside the selected event window.",
    profile_sharpness: "Use the smoothed in-window profile power to favor temporally sharp dedispersion solutions.",
    burst_compactness: "Use a fluence-to-width compactness score so broader smeared solutions are penalized.",
    minimal_residual_drift: "Use sub-band arrival-time residual scatter so flatter delay trends score higher.",
    maximal_structure: "Use profile curvature to favor DMs that align fine-scale temporal structure.",
  }
  return labels[metric] || "Optimize the currently selected DM metric."
}

function dmComponentColor(index) {
  const palette = ["#7235a2", "#327cbc", "#8e6ebd", "#a23b61", "#5f2b88", "#2e2e2e"]
  return palette[index % palette.length]
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

function combinedQuantileRange(collections, lowQuantile = 0.02, highQuantile = 0.995) {
  const finite = collections
    .flatMap((values) => values.flat ? values.flat() : values)
    .map(Number)
    .filter(Number.isFinite)
    .sort((a, b) => a - b)
  if (!finite.length) {
    return [-1, 1]
  }
  const lowIndex = Math.max(0, Math.floor((finite.length - 1) * lowQuantile))
  const highIndex = Math.max(lowIndex, Math.floor((finite.length - 1) * highQuantile))
  const low = finite[lowIndex]
  const high = finite[highIndex]
  if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) {
    return [finite[0], finite[finite.length - 1] || finite[0] + 1]
  }
  return [low, high]
}

function symmetricQuantileRange(values, quantile = 0.995) {
  const finite = values
    .flatMap((row) => row)
    .map(Number)
    .filter(Number.isFinite)
    .map((value) => Math.abs(value))
    .sort((a, b) => a - b)
  if (!finite.length) {
    return [-1, 1]
  }
  const index = Math.max(0, Math.floor((finite.length - 1) * quantile))
  const bound = finite[index]
  if (!Number.isFinite(bound) || bound <= 0) {
    return [-1, 1]
  }
  return [-bound, bound]
}

function fitStatusTone(status) {
  if (status === "quadratic_peak_fit") return "success"
  if (status === "quadratic_peak_fit_uncertainty_unavailable") return "warning"
  if (status === "dmphase_weighted_polyfit") return "success"
  if (status === "dmphase_weighted_polyfit_uncertainty_unavailable") return "warning"
  return "warning"
}

function scatteringStatusTone(status) {
  if (status === "ok") return "success"
  if (status === "fitburst_unavailable") return "error"
  return "warning"
}

function residualStatusTone(status) {
  if (status === "ok") return "success"
  return "warning"
}

function residualStatusLabel(status) {
  const labels = {
    ok: "Residual diagnostics ready",
    insufficient_active_channels: "Insufficient active channels",
    insufficient_subbands: "Insufficient usable sub-bands",
    heavily_masked_subbands: "Sub-bands too heavily masked",
    insufficient_signal: "Sub-band arrival times are unstable",
  }
  return labels[status] || "Residual diagnostics unavailable"
}

function residualStatusCopy(status) {
  const labels = {
    ok: "Residuals are referenced to the band-mean arrival time. A monotonic slope indicates under- or over-dedispersion across the selected band.",
    insufficient_active_channels: "Keep at least 12 unmasked channels in the selected spectral window so three sub-bands can each contribute four channels.",
    insufficient_subbands: "Widen the selected spectral window or reduce masking so the active band can be partitioned into at least three contiguous sub-bands.",
    heavily_masked_subbands: "The current masking pattern breaks the selected band into unreliable sub-bands. Relax the mask or widen the spectral window before interpreting residuals.",
    insufficient_signal: "At least one sub-band does not contain a stable arrival-time estimate inside the active event window.",
  }
  return labels[status] || "Review the selected band, masking, and event window before trusting the residual diagnostic."
}

function fitStatusLabel(status) {
  const labels = {
    quadratic_peak_fit: "Quadratic peak fit accepted",
    quadratic_peak_fit_uncertainty_unavailable: "Quadratic fit accepted without uncertainty",
    dmphase_weighted_polyfit: "DMphase weighted polynomial fit accepted",
    dmphase_weighted_polyfit_uncertainty_unavailable: "DMphase fit accepted without uncertainty",
    dmphase_weighted_polyfit_failed: "DMphase fit failed",
    dmphase_weighted_polyfit_fallback: "DMphase fit fell back to the sampled peak",
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
    dmphase_weighted_polyfit: "The fitted best DM and uncertainty come from an upstream-style weighted polynomial fit applied to the DMphase curve.",
    dmphase_weighted_polyfit_uncertainty_unavailable: "The DMphase fit found a best DM, but the weighted polynomial model did not yield a stable uncertainty.",
    dmphase_weighted_polyfit_failed: "The DMphase weighted polynomial fit failed, so the sampled peak was retained.",
    dmphase_weighted_polyfit_fallback: "The DMphase peak fit could not define a stable fitting window, so the discrete sampled peak was retained.",
    peak_on_sweep_edge: "Expand the DM half-range so the peak is bracketed before trusting the best-DM estimate.",
    insufficient_peak_window: "Use a smaller step or a broader sweep so the local peak has enough neighboring samples for a fit.",
    quadratic_fit_failed: "The sampled S/N curve could not support a stable local quadratic fit.",
    quadratic_not_concave: "The sampled S/N curve near the discrete peak does not resemble a local maximum.",
    fit_vertex_outside_peak_window: "The local quadratic fit shifted the maximum outside the sampled peak window, so the discrete best DM was retained.",
  }
  return labels[status] || "Review the sampled S/N curve before applying a new DM."
}

function scatteringStatusLabel(status) {
  const labels = {
    ok: "Scattering fit accepted",
    fitburst_unavailable: "fitburst is unavailable",
    insufficient_data: "Selected-band data are unavailable",
    insufficient_time_bins: "Selection is too short for fitting",
    insufficient_channels: "Too few usable channels",
    insufficient_offpulse: "No contiguous off-pulse region",
    insufficient_signal: "Event signal is too weak for fitting",
    fit_failed: "Scattering fit failed",
  }
  return labels[status] || "Scattering fit unavailable"
}

function scatteringStatusCopy(status) {
  const labels = {
    ok: "The model fit uses fitburst on the current selected band and reports intrinsic width and scattering time as secondary, model-based diagnostics.",
    fitburst_unavailable: "Install the optional fitburst dependency to enable model-based burst fitting in FLITS.",
    insufficient_data: "Widen the selected spectral window or reset the current crop before running a scattering fit.",
    insufficient_time_bins: "Use a wider crop or event window so the burst and off-pulse baseline are both represented in the fit.",
    insufficient_channels: "Keep at least four unmasked channels inside the selected spectral window for a stable fit.",
    insufficient_offpulse: "The fitter needs a contiguous off-pulse block to estimate per-channel weights from the current crop.",
    insufficient_signal: "The selected event window does not support a stable model fit. Recenter or widen the event window before retrying.",
    fit_failed: "The current selection or initial guesses did not converge to a stable fit. Check masking and event placement before retrying.",
  }
  return labels[status] || "Review the current selection before trusting the model fit."
}

function flagTone(flag) {
  if (flag === "calibrated" || flag === "acf") return "success"
  if (flag === "manual" || flag === "fit") return "neutral"
  return "warning"
}

function formatMeasurementFlag(flag) {
  const labels = {
    acf: "ACF",
    calibrated: "Calibrated",
    manual: "Manual",
    fit: "Fit",
    low_sn: "Low S/N",
    heavily_masked: "Heavily masked",
    edge_clipped: "Edge clipped",
    missing_distance: "Missing distance",
    missing_sefd: "Missing SEFD",
    implicit_offpulse: "Implicit off-pulse",
    insufficient_offpulse_bins: "Sparse off-pulse",
    uncertainty_unavailable: "No uncertainty",
    insufficient_successful_trials: "Sparse MC trials",
    measurement_unavailable: "Unavailable",
  }
  return labels[flag] || flag
}

function spectralStatusLabel(status) {
  const labels = {
    ok: "Ready",
    insufficient_data: "No data",
    insufficient_time_bins: "Event too short",
    invalid_segment_length: "Invalid segment",
    stingray_unavailable: "Stingray unavailable",
    stingray_failed: "Stingray failed",
  }
  return labels[status] || status || "Unknown"
}

function formatPowerLawFitStatus(status) {
  const labels = {
    ok: "Fit ready",
    underconstrained: "Underconstrained",
    fit_failed: "Fit failed",
    unstable_covariance: "Unstable covariance",
    unavailable: "Unavailable",
  }
  return labels[status] || status || "Unknown"
}

function renderProvenanceItems(provenance) {
  const items = [
    ["Peak selection", provenance.peak_selection || "n/a"],
    ["Width method", provenance.width_method || "n/a"],
    ["Spectral width method", provenance.spectral_width_method || "n/a"],
    ["Calibration", provenance.calibration_method || "n/a"],
    ["Calibration assumptions", Array.isArray(provenance.calibration_assumptions) && provenance.calibration_assumptions.length ? provenance.calibration_assumptions.join("; ") : "n/a"],
    ["Energy unit", provenance.energy_unit || "n/a"],
    ["Uncertainty basis", provenance.uncertainty_basis || "n/a"],
    ["Event window", Array.isArray(provenance.event_window_ms) ? `${fmt(provenance.event_window_ms[0], 3)} to ${fmt(provenance.event_window_ms[1], 3)} ms` : "n/a"],
    ["Spectral window", Array.isArray(provenance.spectral_extent_mhz) ? `${fmt(provenance.spectral_extent_mhz[0], 3)} to ${fmt(provenance.spectral_extent_mhz[1], 3)} MHz` : "n/a"],
    ["Off-pulse windows", Array.isArray(provenance.offpulse_windows_ms) && provenance.offpulse_windows_ms.length ? provenance.offpulse_windows_ms.map((window) => `${fmt(window[0], 3)} to ${fmt(window[1], 3)} ms`).join(", ") : provenance.noise_basis || "n/a"],
    ["Effective bandwidth", provenance.effective_bandwidth_mhz === undefined ? "n/a" : `${fmt(provenance.effective_bandwidth_mhz, 3)} MHz`],
    ["Masked fraction", provenance.masked_fraction === undefined ? "n/a" : fmt(provenance.masked_fraction, 3)],
    ["Masked channels", Array.isArray(provenance.masked_channels) && provenance.masked_channels.length ? compactList(provenance.masked_channels) : "none"],
    ["Noise basis", provenance.noise_basis || "n/a"],
    ["Noise estimator", provenance.noise_estimator || "n/a"],
    ["Algorithm", provenance.algorithm_name || "n/a"],
    ["Warnings", Array.isArray(provenance.warning_flags) && provenance.warning_flags.length ? provenance.warning_flags.map((flag) => formatMeasurementFlag(flag)).join(", ") : "none"],
    ["Off-pulse bins", provenance.offpulse_bin_count === undefined ? "n/a" : String(provenance.offpulse_bin_count)],
    ["Deprecated alias", Array.isArray(provenance.deprecated_fields) && provenance.deprecated_fields.length ? provenance.deprecated_fields.join(", ") : "none"],
  ]

  return items
    .map(
      ([label, value]) =>
        `<div class="kv-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`,
    )
    .join("")
}

function compactList(values) {
  if (!values.length) return "0"
  if (values.length <= 10) return values.join(", ")
  return `${values.slice(0, 10).join(", ")} ... (+${values.length - 10})`
}

function renderExportPreviewArtifacts(artifacts) {
  if (!artifacts.length) {
    return '<div class="empty-state">No artifacts in the current selection.</div>'
  }
  return `<div class="preview-artifact-list">${artifacts.map((artifact) => renderExportPreviewArtifactRow(artifact)).join("")}</div>`
}

function renderExportPreviewArtifactRow(artifact) {
  const detail = artifact.status === "ready"
    ? [artifact.format ? String(artifact.format).toUpperCase() : null, artifact.content_type].filter(Boolean).join(" · ")
    : formatArtifactReason(artifact.reason)
  return `
    <div class="preview-artifact-row" data-status="${escapeHtml(artifact.status || "unknown")}">
      <div class="preview-artifact-copy">
        <strong>${escapeHtml(artifact.label || "artifact")}</strong>
        <span>${escapeHtml(detail)}</span>
      </div>
      <span class="preview-artifact-badge">${escapeHtml(previewArtifactBadge(artifact))}</span>
    </div>
  `
}

function previewArtifactBadge(artifact) {
  if (artifact.status !== "ready") {
    return "Unavailable"
  }
  if (artifact.kind === "plot") {
    return "Plot"
  }
  if (artifact.kind === "structured") {
    return "Structured"
  }
  if (artifact.kind === "catalog") {
    return "Catalog"
  }
  if (artifact.kind === "arrays") {
    return "Arrays"
  }
  if (artifact.kind === "window") {
    return "Window"
  }
  return "Ready"
}

function renderExportPreviewThumbs(previews) {
  if (!previews.length) {
    return ""
  }
  return previews.map((preview) => renderExportPreviewThumb(preview)).join("")
}

function renderExportPreviewThumb(preview) {
  const figure = preview.svg
    ? `<div class="preview-thumb-figure">${preview.svg}</div>`
    : `<div class="preview-thumb-figure"><div class="preview-thumb-placeholder">${escapeHtml(formatArtifactReason(preview.reason))}</div></div>`
  const detail = preview.status === "ready" ? "Selected for export preview." : formatArtifactReason(preview.reason)
  return `
    <article class="preview-thumb" data-status="${escapeHtml(preview.status || "unknown")}">
      ${figure}
      <div class="preview-thumb-copy">
        <strong>${escapeHtml(preview.title || "Plot Preview")}</strong>
        <span>${escapeHtml(detail)}</span>
      </div>
    </article>
  `
}

function renderArtifactGroup(title, artifacts) {
  if (!artifacts.length) {
    return `
      <section class="artifact-group">
        <div class="analysis-panel-head compact">
          <h5>${escapeHtml(title)}</h5>
        </div>
        <div class="empty-state">No ${escapeHtml(title.toLowerCase())} artifacts in this bundle.</div>
      </section>
    `
  }

  return `
    <section class="artifact-group">
      <div class="analysis-panel-head compact">
        <h5>${escapeHtml(title)}</h5>
      </div>
      <div class="artifact-list">
        ${artifacts.map((artifact) => renderArtifactRow(artifact)).join("")}
      </div>
    </section>
  `
}

function renderArtifactRow(artifact) {
  const isReady = artifact.status === "ready" && artifact.url
  const detail = isReady
    ? `${formatBytes(artifact.size_bytes)} · ${artifact.content_type}`
    : formatArtifactReason(artifact.reason)
  const action = isReady
    ? `<a class="artifact-link" href="${escapeHtml(artifact.url)}" download="${escapeHtml(artifact.name)}">Download</a>`
    : `<span class="artifact-link is-disabled">Unavailable</span>`
  return `
    <div class="artifact-row" data-status="${escapeHtml(artifact.status || "unknown")}">
      <div class="artifact-copy">
        <strong>${escapeHtml(artifact.name || "artifact")}</strong>
        <span>${escapeHtml(detail)}</span>
      </div>
      ${action}
    </div>
  `
}

function formatArtifactReason(reason) {
  const labels = {
    dm_optimization_unavailable: "Run Optimize DM to add this plot.",
    residual_diagnostics_unavailable: "Residual diagnostics are unavailable for the current sweep.",
    acf_diagnostics_unavailable: "ACF diagnostics are unavailable for the current selection.",
    plot_unavailable: "This plot could not be generated for the current session state.",
  }
  return labels[reason] || "Unavailable for the current session state."
}

function formatBytes(value) {
  const size = Number(value)
  if (!Number.isFinite(size) || size <= 0) {
    return "size unknown"
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;")
}
