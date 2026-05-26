const state = {
  streams: [],
  videos: [],
  videoFormatsList: [],
  schedules: [],
  importantLogs: [],
  storage: [],
  videoPolicy: null,
  logsUnsupported: false,
  focusedSection: null,
  filters: {
    streamModes: new Set(["single", "playlist", "separate"]),
    logLevels: new Set(["info", "warn", "err"]),
  },
  pagination: {
    streams: { page: 1 },
    videos: { page: 1 },
    logs: { page: 1 },
  },
};
let activeLoaderCount = 0;
let loaderFailSafeTimer = null;
let pendingDeleteVideoName = null;
let pendingDeleteScheduleId = null;
let pendingDeleteScheduleLabel = null;
let pendingBulkClearKind = null;
let pendingPlaylistStreamName = null;
let bootstrapVideoSearchQuery = "";
const LOADER_FAILSAFE_MS = 180000;

const el = {
  activeStreamList: document.getElementById("activeStreamList"),
  activeStreamPager: document.getElementById("activeStreamPager"),
  videoList: document.getElementById("videoList"),
  videoPager: document.getElementById("videoPager"),
  logPager: document.getElementById("logPager"),
  scheduleList: document.getElementById("scheduleList"),
  diskList: document.getElementById("diskList"),
  videoFormats: document.getElementById("videoFormats"),
  statStreams: document.getElementById("statStreams"),
  statRunning: document.getElementById("statRunning"),
  statVideos: document.getElementById("statVideos"),
  statDisabledVideos: document.getElementById("statDisabledVideos"),
  statInUseVideos: document.getElementById("statInUseVideos"),
  statSchedules: document.getElementById("statSchedules"),
  statSchedulesEnabled: document.getElementById("statSchedulesEnabled"),
  statNextRun: document.getElementById("statNextRun"),
  schedulerHealth: document.getElementById("schedulerHealth"),
  toast: document.getElementById("toast"),
  apiHealth: document.getElementById("apiHealth"),
  mediamtxHealth: document.getElementById("mediamtxHealth"),
  importantLogs: document.getElementById("importantLogs"),
  streamTypeFilterBtn: document.getElementById("streamTypeFilterBtn"),
  streamTypeFilterMenu: document.getElementById("streamTypeFilterMenu"),
  logTypeFilterBtn: document.getElementById("logTypeFilterBtn"),
  logTypeFilterMenu: document.getElementById("logTypeFilterMenu"),
  bootstrapStreamsBtn: document.getElementById("bootstrapStreamsBtn"),
  addScheduleBtn: document.getElementById("addScheduleBtn"),
  clearActiveStreamsBtn: document.getElementById("clearActiveStreamsBtn"),
  clearVideosBtn: document.getElementById("clearVideosBtn"),
  clearSchedulesBtn: document.getElementById("clearSchedulesBtn"),
  videoUpload: document.getElementById("videoUpload"),
  pageLoader: document.getElementById("pageLoader"),
  deleteVideoModal: document.getElementById("deleteVideoModal"),
  deleteVideoText: document.getElementById("deleteVideoText"),
  deleteVideoConfirmBtn: document.getElementById("deleteVideoConfirmBtn"),
  deleteVideoCancelBtn: document.getElementById("deleteVideoCancelBtn"),
  deleteScheduleModal: document.getElementById("deleteScheduleModal"),
  deleteScheduleText: document.getElementById("deleteScheduleText"),
  deleteScheduleConfirmBtn: document.getElementById("deleteScheduleConfirmBtn"),
  deleteScheduleCancelBtn: document.getElementById("deleteScheduleCancelBtn"),
  bulkClearModal: document.getElementById("bulkClearModal"),
  bulkClearText: document.getElementById("bulkClearText"),
  bulkClearHint: document.getElementById("bulkClearHint"),
  bulkClearConfirmBtn: document.getElementById("bulkClearConfirmBtn"),
  bulkClearCancelBtn: document.getElementById("bulkClearCancelBtn"),
  playlistVideoModal: document.getElementById("playlistVideoModal"),
  playlistVideoForm: document.getElementById("playlistVideoForm"),
  playlistVideoTarget: document.getElementById("playlistVideoTarget"),
  playlistVideoPicker: document.getElementById("playlistVideoPicker"),
  playlistVideoPickAllBtn: document.getElementById("playlistVideoPickAllBtn"),
  playlistVideoClearAllBtn: document.getElementById("playlistVideoClearAllBtn"),
  playlistVideoSubmitBtn: document.getElementById("playlistVideoSubmitBtn"),
  playlistVideoCancelBtn: document.getElementById("playlistVideoCancelBtn"),
  bootstrapModal: document.getElementById("bootstrapModal"),
  bootstrapForm: document.getElementById("bootstrapForm"),
  bootstrapMode: document.getElementById("bootstrapMode"),
  bootstrapNameWrap: document.getElementById("bootstrapNameWrap"),
  bootstrapName: document.getElementById("bootstrapName"),
  bootstrapPrefixWrap: document.getElementById("bootstrapPrefixWrap"),
  bootstrapPrefix: document.getElementById("bootstrapPrefix"),
  bootstrapCodec: document.getElementById("bootstrapCodec"),
  bootstrapStartNow: document.getElementById("bootstrapStartNow"),
  bootstrapModeHint: document.getElementById("bootstrapModeHint"),
  bootstrapVideoSearch: document.getElementById("bootstrapVideoSearch"),
  bootstrapPickAllBtn: document.getElementById("bootstrapPickAllBtn"),
  bootstrapClearAllBtn: document.getElementById("bootstrapClearAllBtn"),
  bootstrapVideoPicker: document.getElementById("bootstrapVideoPicker"),
  bootstrapSubmitBtn: document.getElementById("bootstrapSubmitBtn"),
  bootstrapCancelBtn: document.getElementById("bootstrapCancelBtn"),
  createStreamModal: document.getElementById("createStreamModal"),
  createStreamForm: document.getElementById("createStreamForm"),
  createStreamName: document.getElementById("createStreamName"),
  createStreamMode: document.getElementById("createStreamMode"),
  createStreamCodec: document.getElementById("createStreamCodec"),
  createStreamStartNow: document.getElementById("createStreamStartNow"),
  createStreamVideoPicker: document.getElementById("createStreamVideoPicker"),
  createStreamSubmitBtn: document.getElementById("createStreamSubmitBtn"),
  createStreamCancelBtn: document.getElementById("createStreamCancelBtn"),
  quickTimerModal: document.getElementById("quickTimerModal"),
  quickTimerForm: document.getElementById("quickTimerForm"),
  quickTimerStreamPicker: document.getElementById("quickTimerStreamPicker"),
  quickTimerPickAllBtn: document.getElementById("quickTimerPickAllBtn"),
  quickTimerClearAllBtn: document.getElementById("quickTimerClearAllBtn"),
  quickTimerAction: document.getElementById("quickTimerAction"),
  quickTimerDate: document.getElementById("quickTimerDate"),
  quickTimerCancelBtn: document.getElementById("quickTimerCancelBtn"),
  quickTimerSubmitBtn: document.getElementById("quickTimerSubmitBtn"),
  appShell: document.querySelector(".app-shell"),
};
const focusPanels = Array.from(document.querySelectorAll(".focus-panel[data-focus-section]"));
const focusSectionButtons = Array.from(document.querySelectorAll('button[data-action="focusSection"]'));
let activeFilterMenu = null;

function setPageLoading(flag, label = "Выполняю действие...") {
  if (!el.pageLoader) return;
  if (flag) {
    activeLoaderCount += 1;
    setPageLoadingText(label);
    el.pageLoader.classList.remove("hidden");
    resetLoaderFailsafe();
    return;
  }

  activeLoaderCount = Math.max(0, activeLoaderCount - 1);
  if (activeLoaderCount === 0) {
    if (loaderFailSafeTimer) clearTimeout(loaderFailSafeTimer);
    loaderFailSafeTimer = null;
    el.pageLoader.classList.add("hidden");
  }
}

function resetLoaderFailsafe() {
  if (loaderFailSafeTimer) clearTimeout(loaderFailSafeTimer);
  loaderFailSafeTimer = window.setTimeout(() => {
    activeLoaderCount = 0;
    el.pageLoader?.classList.add("hidden");
  }, LOADER_FAILSAFE_MS);
}

function setPageLoadingText(label) {
  if (!el.pageLoader) return;
  const textNode = el.pageLoader.querySelector("p");
  if (textNode) textNode.textContent = label;
  if (!el.pageLoader.classList.contains("hidden")) {
    resetLoaderFailsafe();
  }
}

function setButtonLoading(button, flag) {
  if (!button) return;
  if (flag) {
    button.dataset.prevLabel = button.textContent;
    button.disabled = true;
    button.classList.add("is-loading");
    button.textContent = "...";
  } else {
    button.disabled = false;
    button.classList.remove("is-loading");
    button.textContent = button.dataset.prevLabel || button.textContent;
  }
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 12000);
  const response = await fetch(path, { ...options, signal: controller.signal });
  clearTimeout(timeoutId);
  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const detail = typeof payload === "object" ? JSON.stringify(payload.detail || payload) : String(payload);
    throw new Error(detail);
  }

  return payload;
}

function toast(message, type = "ok") {
  el.toast.textContent = message;
  el.toast.className = `toast ${type}`;
  el.toast.classList.remove("hidden");
  window.setTimeout(() => el.toast.classList.add("hidden"), 2600);
}

function normalizeErrorMessage(error) {
  const raw = String(error?.message || error || "неизвестная ошибка");
  try {
    const parsed = JSON.parse(raw);
    const detail = parsed?.detail ?? parsed;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") return detail.message;
      return JSON.stringify(detail);
    }
  } catch {}
  return raw;
}

function syncFocusButtons() {
  const current = state.focusedSection;
  for (const button of focusSectionButtons) {
    const section = button.dataset.section || "";
    const active = Boolean(current) && section === current;
    button.classList.toggle("active", active);
    button.textContent = "⛶";
    button.title = active ? "В фокусе" : "Развернуть";
    button.setAttribute("aria-label", active ? "Раздел в фокусе" : "Развернуть раздел");
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
}

function setFocusedSection(sectionId) {
  const next = sectionId || null;
  state.focusedSection = next;
  const inFocusMode = Boolean(next);
  el.appShell?.classList.toggle("focus-mode", inFocusMode);
  for (const panel of focusPanels) {
    const section = panel.dataset.focusSection || "";
    panel.classList.toggle("is-focused", inFocusMode && section === next);
  }
  syncFocusButtons();
}

function toggleFocusedSection(sectionId) {
  if (!sectionId) return;
  if (state.focusedSection === sectionId) {
    setFocusedSection(null);
    return;
  }
  setFocusedSection(sectionId);
}

function notifyError(prefix, error) {
  toast(`${prefix}: ${normalizeErrorMessage(error)}`, "err");
}

async function copyToClipboard(text) {
  const value = String(text || "");
  if (!value) throw new Error("пустая строка");

  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const temp = document.createElement("textarea");
  temp.value = value;
  temp.setAttribute("readonly", "");
  temp.style.position = "absolute";
  temp.style.left = "-9999px";
  document.body.appendChild(temp);
  temp.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(temp);
  if (!ok) throw new Error("не удалось скопировать");
}

function clearFieldInvalid(...nodes) {
  for (const node of nodes) {
    if (!node) continue;
    node.classList.remove("field-invalid");
  }
}

function markFieldInvalid(node) {
  if (!node) return;
  node.classList.add("field-invalid");
}

function clearModalValidation(container) {
  if (!container) return;
  container.querySelectorAll(".field-invalid").forEach((n) => n.classList.remove("field-invalid"));
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function parseDateTs(value) {
  if (!value) return 0;
  const ts = Date.parse(String(value));
  return Number.isFinite(ts) ? ts : 0;
}

function updateFilterButtons() {
  if (el.streamTypeFilterBtn) {
    el.streamTypeFilterBtn.textContent = `Типы стримов (${state.filters.streamModes.size})`;
  }
  if (el.logTypeFilterBtn) {
    el.logTypeFilterBtn.textContent = `Типы логов (${state.filters.logLevels.size})`;
  }
}

function closeFilterMenu(menu) {
  if (!menu) return;
  menu.classList.add("hidden");
  if (activeFilterMenu === menu) activeFilterMenu = null;
}

function closeAllFilterMenus(exceptMenu = null) {
  [el.streamTypeFilterMenu, el.logTypeFilterMenu].forEach((menu) => {
    if (!menu || menu === exceptMenu) return;
    closeFilterMenu(menu);
  });
}

function toggleFilterMenu(menu) {
  if (!menu) return;
  const openNext = menu.classList.contains("hidden");
  if (openNext) {
    closeAllFilterMenus(menu);
    menu.classList.remove("hidden");
    activeFilterMenu = menu;
    return;
  }
  closeFilterMenu(menu);
}

function syncFilterStateFromDom() {
  const selectedModes = Array.from(document.querySelectorAll('input[name="stream_mode_filter"]:checked')).map((x) => x.value);
  const selectedLevels = Array.from(document.querySelectorAll('input[name="log_level_filter"]:checked')).map((x) => x.value);
  state.filters.streamModes = new Set(selectedModes.length ? selectedModes : ["single", "playlist", "separate"]);
  state.filters.logLevels = new Set(selectedLevels.length ? selectedLevels : ["info", "warn", "err"]);
  updateFilterButtons();
}

function PathLikeName(pathValue) {
  const raw = String(pathValue || "");
  const parts = raw.split("/");
  return parts[parts.length - 1] || raw;
}

function statusRu(status) {
  if (status === "running") return "запущен";
  if (status === "stopped") return "остановлен";
  return status;
}

function actionRu(action) {
  if (action === "start") return "запуск";
  if (action === "stop") return "остановка";
  if (action === "restart") return "перезапуск";
  if (action === "delete") return "удаление";
  return action;
}

function streamModeHint(mode) {
  const m = String(mode || "").toLowerCase();
  if (m === "single") return "Single: один стрим из одного видеофайла";
  if (m === "playlist") return "Playlist: несколько видео идут подряд как один стрим";
  if (m === "separate") return "Separate: группа файлов как отдельные стримы";
  return "Тип стрима";
}

function codecModeHint(codec) {
  const c = String(codec || "").toLowerCase();
  if (c === "copy") return "Copy: без перекодирования, минимальная нагрузка CPU";
  if (c === "transcode") return "Transcode: перекодирование в H.264 для совместимости";
  if (c === "auto") return "Auto: выбор между copy и transcode по параметрам файла";
  return "Режим кодека";
}

function streamCard(stream) {
  const statusClass = stream.status === "running" ? "on" : "off";
  const filesCount = Array.isArray(stream.files) ? stream.files.length : 0;
  const isPlaylist = stream.mode === "playlist";
  const mode = String(stream.mode || "");
  const codec = String(stream.codec_mode || "");
  const filesList = (stream.files || [])
    .map((file) => {
      const base = PathLikeName(file);
      const removeBtn = isPlaylist
        ? `<button class="mini danger" data-action="removePlaylistVideo" data-stream="${stream.name}" data-video="${base}">Удалить</button>`
        : "";
      return `<li><span>${file}</span>${removeBtn}</li>`;
    })
    .join("");
  const playlistManageBtn = isPlaylist
    ? `<button class="btn" data-action="openAddPlaylistVideo" data-stream="${stream.name}">+ Добавить видео</button>`
    : "";
  const mainButton =
    stream.status === "running"
      ? `<button class="btn solid" data-action="stop" data-name="${stream.name}">Остановить</button>`
      : `<button class="btn solid" data-action="start" data-name="${stream.name}">Запустить</button>`;

  return `
    <div class="stream-card">
      <div>
        <h3>${stream.name}</h3>
        <p class="stream-meta-line">
          <span class="stream-meta-chip has-tooltip" data-tooltip="${streamModeHint(mode)}" aria-label="${streamModeHint(mode)}">${mode}</span>
          <span class="stream-meta-dot">·</span>
          <span class="stream-meta-chip has-tooltip" data-tooltip="${codecModeHint(codec)}" aria-label="${codecModeHint(codec)}">${codec}</span>
        </p>
      </div>
      <div class="stream-status-row">
        <span class="status ${statusClass} has-tooltip" data-tooltip="Текущее состояние стрима" aria-label="Текущее состояние стрима">${statusRu(stream.status)}</span>
        <span class="status video-count-chip has-tooltip" data-tooltip="Количество видеофайлов в этом стриме" aria-label="Количество видеофайлов в этом стриме">видео: ${filesCount}</span>
      </div>
      <button
        class="url copy-url-btn has-tooltip"
        data-action="copyUrl"
        data-url="${stream.public_url}"
        data-tooltip="Нажмите чтобы скопировать"
        aria-label="Нажмите чтобы скопировать"
        title="Нажмите чтобы скопировать"
      >
        ${stream.public_url}
      </button>
      <div class="stream-files-wrap">
        <p class="muted small">Файлы:</p>
        <ol class="stream-files-list">${filesList}</ol>
      </div>
      <div class="actions">
        ${playlistManageBtn}
        ${mainButton}
        <button class="btn" data-action="restart" data-name="${stream.name}">Перезапустить</button>
      </div>
    </div>`;
}

function perPageFor(kind) {
  if (kind === "streams") return 6;
  if (kind === "videos") return 6;
  if (kind === "logs") return 30;
  return 6;
}

function paginate(items, page, perPage) {
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / perPage));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const startIndex = (safePage - 1) * perPage;
  const endIndex = Math.min(total, startIndex + perPage);
  return {
    total,
    totalPages,
    page: safePage,
    perPage,
    start: total ? startIndex + 1 : 0,
    end: endIndex,
    items: items.slice(startIndex, endIndex),
  };
}

function renderPager(container, kind, meta) {
  if (!container) return;
  if (meta.total <= meta.perPage) {
    container.innerHTML = "";
    return;
  }
  const prevDisabled = meta.page <= 1 ? "disabled" : "";
  const nextDisabled = meta.page >= meta.totalPages ? "disabled" : "";
  container.innerHTML = `
    <span class="pager-meta">Показано ${meta.start}-${meta.end} из ${meta.total}</span>
    <div class="row-actions">
      <button class="mini" data-action="pagePrev" data-kind="${kind}" ${prevDisabled}>Назад</button>
      <span class="pager-meta">Стр. ${meta.page}/${meta.totalPages}</span>
      <button class="mini" data-action="pageNext" data-kind="${kind}" ${nextDisabled}>Вперед</button>
    </div>`;
}

function renderStreams() {
  const visible = state.streams
    .filter((s) => state.filters.streamModes.has(String(s.mode || "").toLowerCase()))
    .sort((a, b) => {
      const aRun = a.status === "running" ? 0 : 1;
      const bRun = b.status === "running" ? 0 : 1;
      if (aRun !== bRun) return aRun - bRun;
      return parseDateTs(b.added_at) - parseDateTs(a.added_at);
    });
  const perPage = perPageFor("streams");
  const current = state.pagination.streams.page || 1;
  const meta = paginate(visible, current, perPage);
  state.pagination.streams.page = meta.page;

  el.activeStreamList.innerHTML = meta.total
    ? meta.items.map(streamCard).join("")
    : `<div class="stream-card"><p>Стримов пока нет.</p></div>`;

  renderPager(el.activeStreamPager, "streams", meta);
}

function renderVideos(formats = state.videoFormatsList) {
  const retentionDays = Number(state.videoPolicy?.retention_days || 30);
  const maxUploadBytes = Number(state.videoPolicy?.max_upload_bytes || 0);
  const maxUploadText = maxUploadBytes > 0 ? formatBytes(maxUploadBytes) : "-";
  el.videoFormats.textContent = `Поддерживаемые форматы: ${formats.join(", ")} · Хранение: ${retentionDays} дней (автоудаление) · Лимит файла: ${maxUploadText}`;

  const perPage = perPageFor("videos");
  const current = state.pagination.videos.page || 1;
  const sortedVideos = [...state.videos].sort((a, b) => parseDateTs(b.uploaded_at) - parseDateTs(a.uploaded_at));
  const meta = paginate(sortedVideos, current, perPage);
  state.pagination.videos.page = meta.page;

  if (!meta.total) {
    el.videoList.innerHTML = `<div class="video-row"><p>Папка videos пока пустая.</p></div>`;
    renderPager(el.videoPager, "videos", meta);
    return;
  }

  el.videoList.innerHTML = meta.items
    .map((video) => {
      const m = video.metadata || {};
      const meta = [
        m.codec ? `${m.codec.toUpperCase()}` : null,
        m.width && m.height ? `${m.width}x${m.height}` : null,
        m.fps ? `${m.fps} fps` : null,
        m.duration ? `${Number(m.duration).toFixed(1)}s` : null,
      ]
        .filter(Boolean)
        .join(" · ");

      const refs = video.in_use_by?.length ? `используется в: ${video.in_use_by.join(", ")}` : "не привязано к стримам";
      const toggleLabel = video.enabled ? "Отключить" : "Включить";
      const toggleAction = video.enabled ? "disableVideo" : "enableVideo";
      const expiresAt = video.expires_at ? new Date(video.expires_at).toLocaleString("ru-RU") : "-";
      const addedAt = video.uploaded_at ? new Date(video.uploaded_at).toLocaleString("ru-RU") : "-";
      const expireHint =
        Number.isFinite(video.expires_in_days) && video.expires_in_days > 0
          ? `истекает через ${video.expires_in_days} д`
          : "истекает сегодня";

      return `
        <div class="video-row">
          <strong>${video.name}</strong>
          <p>${meta || "метаданные недоступны"}</p>
          <p class="muted small">Добавлено: ${addedAt}</p>
          <p class="muted small">${formatBytes(video.size_bytes)} · ${refs}</p>
          <p class="muted small">Срок хранения: ${expireHint} · до ${expiresAt}</p>
          <div class="row-actions">
            <span class="tiny ${video.enabled ? "on" : "warn"}">${video.enabled ? "Включено" : "Отключено"}</span>
            <button class="mini" data-action="${toggleAction}" data-name="${video.name}">${toggleLabel}</button>
            <button class="mini danger" data-action="deleteVideo" data-name="${video.name}">Удалить</button>
          </div>
        </div>`;
    })
    .join("");

  renderPager(el.videoPager, "videos", meta);
}

function changePage(kind, delta) {
  const bag = state.pagination[kind];
  if (!bag) return;
  const next = Math.max(1, (bag.page || 1) + delta);
  bag.page = next;
  if (kind === "streams") renderStreams();
  if (kind === "videos") renderVideos();
  if (kind === "logs") renderImportantLogs();
}

function renderStorage() {
  if (!el.diskList) return;
  if (!state.storage.length) {
    el.diskList.innerHTML = `<div class="disk-row"><p>Данные о дисках недоступны.</p></div>`;
    return;
  }

  el.diskList.innerHTML = state.storage
    .map((disk) => {
      const mount = disk.mount_point || "/";
      const free = formatBytes(Number(disk.free_bytes || 0));
      const used = formatBytes(Number(disk.used_bytes || 0));
      const total = formatBytes(Number(disk.total_bytes || 0));
      const usedPct = Number(disk.used_percent || 0).toFixed(1);
      const freePctRaw = Number(disk.free_percent || 0);
      const isLowSpace = Number.isFinite(freePctRaw) && freePctRaw < 10;
      return `
        <div class="disk-row ${isLowSpace ? "low-space" : ""}">
          <h4>${mount}</h4>
          <p>Свободно: ${free}</p>
          <p>Занято: ${used} (${usedPct}%)</p>
          <p>Всего: ${total}</p>
        </div>`;
    })
    .join("");
}

function renderSchedules() {
  if (!state.schedules.length) {
    el.scheduleList.innerHTML = `<div class="schedule-card"><p>Правила пока не созданы.</p></div>`;
    return;
  }

  el.scheduleList.innerHTML = state.schedules
    .map((item) => {
      const when = item.next_run_at || item.cron || item.start_at || "вручную";
      const firedBadge = item.is_fired ? `<p class="pill">сработал</p>` : "";
      const firedAt = item.fired_at ? ` · сработал: ${new Date(item.fired_at).toLocaleString("ru-RU")}` : "";
      const runResult = item.last_result ? `<p class="muted small">Результат: ${item.last_result}</p>` : "";
      return `
      <div class="schedule-card">
        <div class="schedule-tags">
          <p class="pill">${item.enabled ? "включено" : "выключено"}</p>
          ${firedBadge}
        </div>
        <h4>${item.stream} · ${actionRu(item.action)}</h4>
        <p>${when} (${item.timezone || "UTC"})${firedAt}</p>
        ${runResult}
        <div class="row-actions">
          <button class="mini" data-action="toggleSchedule" data-id="${item.id}" data-enabled="${item.enabled}">
            ${item.enabled ? "Отключить" : "Включить"}
          </button>
          <button class="mini danger" data-action="deleteSchedule" data-id="${item.id}" data-stream="${item.stream}" data-kind="${item.action}">
            Удалить
          </button>
        </div>
      </div>`;
    })
    .join("");
}

function renderImportantLogs() {
  if (!el.importantLogs) return;
  if (state.logsUnsupported) {
    el.importantLogs.innerHTML = `<div class="log-row"><p>Блок логов недоступен: backend старой версии (нет /backend/logs/important).</p></div>`;
    renderPager(el.logPager, "logs", { total: 0, perPage: 30, page: 1, totalPages: 1, start: 0, end: 0, items: [] });
    return;
  }
  const filteredLogs = state.importantLogs.filter((row) => {
    const level = String(row.level || "info").toLowerCase();
    return state.filters.logLevels.has(level);
  });

  const perPage = perPageFor("logs");
  const current = state.pagination.logs.page || 1;
  const meta = paginate(filteredLogs, current, perPage);
  state.pagination.logs.page = meta.page;

  if (!meta.total) {
    el.importantLogs.innerHTML = `<div class="log-row"><p>Пока нет важных событий.</p></div>`;
    renderPager(el.logPager, "logs", meta);
    return;
  }

  el.importantLogs.innerHTML = meta.items
    .map((row) => {
      const level = row.level || "info";
      const at = row.at ? new Date(row.at).toLocaleString("ru-RU") : "-";
      const source = row.source || "system";
      const message = row.message || "";
      return `
        <div class="log-row">
          <div class="log-head">
            <span class="tiny ${level === "err" ? "warn" : level === "warn" ? "warn" : "on"}">${level}</span>
            <strong>${source}</strong>
            <span class="muted small">${at}</span>
          </div>
          <p class="log-message">${message}</p>
        </div>`;
    })
    .join("");

  renderPager(el.logPager, "logs", meta);
}

function updateStats() {
  const running = state.streams.filter((x) => x.status === "running").length;
  const disabledVideos = state.videos.filter((x) => !x.enabled).length;
  const inUseVideos = state.videos.filter((x) => Array.isArray(x.in_use_by) && x.in_use_by.length > 0).length;
  const schedulesEnabled = state.schedules.filter((x) => x.enabled).length;
  const nextRun = state.schedules
    .filter((x) => x.next_run_at)
    .map((x) => x.next_run_at)
    .sort()[0];

  el.statStreams.textContent = String(state.streams.length);
  el.statRunning.textContent = String(running);
  el.statVideos.textContent = String(state.videos.length);
  if (el.statDisabledVideos) el.statDisabledVideos.textContent = String(disabledVideos);
  if (el.statInUseVideos) el.statInUseVideos.textContent = String(inUseVideos);
  if (el.statSchedules) el.statSchedules.textContent = String(state.schedules.length);
  if (el.statSchedulesEnabled) el.statSchedulesEnabled.textContent = String(schedulesEnabled);
  if (el.statNextRun) el.statNextRun.textContent = nextRun ? nextRun.replace("T", " ").slice(0, 16) : "-";

}

async function loadStreams() {
  const data = await api("/backend/streams");
  state.streams = data.items || [];
  renderStreams();
  updateStats();
}

async function loadVideos() {
  const data = await api("/backend/videos?with_probe=false");
  state.videos = data.items || [];
  state.videoFormatsList = data.supported_formats || [];
  state.videoPolicy = data.policy || null;
  renderVideos();
  updateStats();
}

async function loadSchedules() {
  const data = await api("/backend/schedules");
  state.schedules = data.items || [];
  renderSchedules();
  updateStats();
}

async function loadStorage() {
  const data = await api("/backend/storage");
  state.storage = data.items || [];
  renderStorage();
}

async function loadImportantLogs() {
  try {
    const data = await api("/backend/logs/important?limit=500&files=12");
    state.logsUnsupported = false;
    state.importantLogs = data.items || [];
    renderImportantLogs();
  } catch (error) {
    const msg = String(error?.message || error || "");
    if (msg.toLowerCase().includes("not found")) {
      state.logsUnsupported = true;
      state.importantLogs = [];
      renderImportantLogs();
      return;
    }
    throw error;
  }
}

async function loadHealth() {
  try {
    const h = await api("/backend/health");
    const apiState = h.ok ? "online" : "offline";
    const mtxState = h.mediamtx_reachable ? "reachable" : "unreachable";
    const schedState = h.scheduler_running ? "running" : "stopped";
    const apiStateRu = h.ok ? "онлайн" : "оффлайн";
    const mtxStateRu = h.mediamtx_reachable ? "доступен" : "недоступен";
    const schedStateRu = h.scheduler_running ? "работает" : "остановлен";

    el.apiHealth.textContent = apiStateRu;
    el.apiHealth.className = h.ok ? "ok" : "warn";
    el.mediamtxHealth.textContent = mtxStateRu;
    el.mediamtxHealth.className = h.mediamtx_reachable ? "ok" : "warn";
    if (el.schedulerHealth) {
      el.schedulerHealth.textContent = schedStateRu;
      el.schedulerHealth.className = h.scheduler_running ? "ok" : "warn";
    }
  } catch {
    el.apiHealth.textContent = "оффлайн";
    el.apiHealth.className = "warn";
    el.mediamtxHealth.textContent = "неизвестно";
    el.mediamtxHealth.className = "warn";
    if (el.schedulerHealth) {
      el.schedulerHealth.textContent = "неизвестно";
      el.schedulerHealth.className = "warn";
    }
  }
}

async function refreshAll(withLoader = false) {
  if (withLoader) setPageLoading(true, "Обновляю данные...");
  try {
    const results = await Promise.allSettled([
      loadStreams(),
      loadVideos(),
      loadSchedules(),
      loadHealth(),
      loadStorage(),
      loadImportantLogs(),
    ]);
    const rejected = results.find((r) => r.status === "rejected");
    if (rejected) {
      const reason = rejected.reason?.message || String(rejected.reason || "неизвестная ошибка");
      notifyError("Ошибка загрузки", reason);
    }
  } catch (error) {
    notifyError("Ошибка загрузки", error);
  } finally {
    if (withLoader) setPageLoading(false);
  }
}

function forceHideLoader() {
  activeLoaderCount = 0;
  if (loaderFailSafeTimer) clearTimeout(loaderFailSafeTimer);
  loaderFailSafeTimer = null;
  el.pageLoader?.classList.add("hidden");
}

async function handleStreamAction(button, name, action) {
  setButtonLoading(button, true);
  setPageLoading(true, `Стрим ${name}: ${actionRu(action)}...`);
  try {
    await api(`/backend/streams/${encodeURIComponent(name)}/${action}`, { method: "POST" });
    toast(`Стрим ${name}: ${actionRu(action)} выполнена`);
    await loadStreams();
    await loadHealth();
  } catch (error) {
    notifyError(`Стрим ${name}`, error);
  } finally {
    setButtonLoading(button, false);
    setPageLoading(false);
  }
}

async function setVideoEnabled(button, name, enabled) {
  setButtonLoading(button, true);
  setPageLoading(true, `${enabled ? "Включаю" : "Отключаю"} ${name}...`);
  try {
    await api(`/backend/videos/${encodeURIComponent(name)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    toast(`${name}: ${enabled ? "включено" : "отключено"}`);
    await loadVideos();
  } catch (error) {
    notifyError("Ошибка обновления видео", error);
  } finally {
    setButtonLoading(button, false);
    setPageLoading(false);
  }
}

function closeDeleteVideoModal() {
  pendingDeleteVideoName = null;
  clearModalValidation(el.deleteVideoModal);
  el.deleteVideoModal?.classList.add("hidden");
}

function openDeleteVideoModal(name) {
  pendingDeleteVideoName = name;
  if (el.deleteVideoText) {
    el.deleteVideoText.textContent = `Удалить видео "${name}"?`;
  }
  el.deleteVideoModal?.classList.remove("hidden");
}

async function confirmDeleteVideo() {
  if (!pendingDeleteVideoName) {
    closeDeleteVideoModal();
    return;
  }
  const name = pendingDeleteVideoName;
  setButtonLoading(el.deleteVideoConfirmBtn, true);
  setPageLoading(true, `Удаляю ${name}...`);
  try {
    await api(`/backend/videos/${encodeURIComponent(name)}`, { method: "DELETE" });
    closeDeleteVideoModal();
    toast(`${name}: удалено`);
    await loadVideos();
  } catch (error) {
    notifyError("Ошибка удаления", error);
  } finally {
    setButtonLoading(el.deleteVideoConfirmBtn, false);
    setPageLoading(false);
  }
}

async function uploadVideo(file) {
  if (!file) return;
  setPageLoading(true, `Загружаю ${file.name}: 0%`);

  try {
    await uploadVideoWithProgress(file, (percent, loaded, total) => {
      const sizeHint = total > 0 ? ` (${formatBytes(loaded)} / ${formatBytes(total)})` : "";
      setPageLoadingText(`Загружаю ${file.name}: ${percent}%${sizeHint}`);
    });

    setPageLoadingText(`Файл ${file.name} загружен (100%). Добавляю в библиотеку...`);
    await waitForVideoInLibrary(file.name);
    toast(`${file.name}: загружено`);
  } catch (error) {
    notifyError("Ошибка загрузки", error);
  } finally {
    setPageLoading(false);
  }
}

function uploadVideoWithProgress(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/backend/videos");
    xhr.responseType = "json";

    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) {
        onProgress?.(0, event.loaded || 0, 0);
        return;
      }
      const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
      onProgress?.(percent, event.loaded, event.total);
    });

    xhr.addEventListener("load", () => {
      const payload = xhr.response ?? safeJsonParse(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(100, file.size, file.size);
        resolve(payload);
        return;
      }
      reject(new Error(extractApiError(payload, xhr.statusText || "upload failed")));
    });

    xhr.addEventListener("error", () => {
      reject(new Error("network error during upload"));
    });

    xhr.addEventListener("abort", () => {
      reject(new Error("upload aborted"));
    });

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

function safeJsonParse(text) {
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

function extractApiError(payload, fallback) {
  if (!payload) return fallback;
  if (typeof payload === "string") return payload;
  if (typeof payload.detail === "string") return payload.detail;
  if (payload.detail && typeof payload.detail === "object") return JSON.stringify(payload.detail);
  return JSON.stringify(payload);
}

async function waitForVideoInLibrary(videoName, timeoutMs = 12000, stepMs = 500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await loadVideos();
    if (state.videos.some((x) => x.name === videoName)) return true;
    await new Promise((resolve) => window.setTimeout(resolve, stepMs));
  }
  return false;
}

async function createStreamFromVideosRequest(payload) {
  try {
    return await api("/backend/streams/create-from-videos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    const msg = String(error?.message || error || "");
    if (msg.toLowerCase().includes("not found")) {
      throw new Error(
        "API не поддерживает create-from-videos (404). Перезапустите backend: docker compose up -d --build (или python app/api_server.py).",
      );
    }
    throw error;
  }
}

async function removeVideoFromPlaylist(button, streamName, videoName) {
  setButtonLoading(button, true);
  setPageLoading(true, `Удаляю ${videoName} из ${streamName}...`);
  try {
    const result = await api(`/backend/streams/${encodeURIComponent(streamName)}/playlist/remove-video`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_name: videoName }),
    });
    if (result.restart_error) {
      notifyError(`Плейлист ${streamName}`, result.restart_error);
    } else {
      toast(`${videoName} удалено из ${streamName}`);
    }
    await loadStreams();
  } catch (error) {
    notifyError(`Ошибка удаления из плейлиста ${streamName}`, error);
  } finally {
    setButtonLoading(button, false);
    setPageLoading(false);
  }
}

function closePlaylistVideoModal() {
  pendingPlaylistStreamName = null;
  clearModalValidation(el.playlistVideoModal);
  el.playlistVideoModal?.classList.add("hidden");
}

function selectedPlaylistModalVideos() {
  if (!el.playlistVideoPicker) return [];
  return Array.from(el.playlistVideoPicker.querySelectorAll('input[name="playlist_add_video"]:checked')).map((x) => x.value);
}

function openPlaylistVideoModal(streamName) {
  const stream = state.streams.find((x) => x.name === streamName);
  if (!stream || stream.mode !== "playlist") {
    toast("Добавление видео доступно только для playlist-стрима", "err");
    return;
  }
  pendingPlaylistStreamName = streamName;
  if (el.playlistVideoTarget) el.playlistVideoTarget.textContent = `Стрим: ${streamName}`;

  const used = new Set((stream.files || []).map((x) => PathLikeName(x)));
  const candidates = state.videos.filter((v) => !used.has(v.name));
  if (!candidates.length) {
    el.playlistVideoPicker.innerHTML = `<div class="video-pick-row"><span>Нет доступных видео для добавления.</span></div>`;
  } else {
    el.playlistVideoPicker.innerHTML = candidates
      .map(
        (v) => `
        <label class="video-pick-row ${v.enabled ? "" : "disabled"}">
          <input type="checkbox" name="playlist_add_video" value="${v.name}" ${v.enabled ? "" : "disabled"} />
          <span>${v.name}</span>
          <span class="meta">${v.enabled ? "доступно" : "отключено"}</span>
        </label>`,
      )
      .join("");
  }

  clearModalValidation(el.playlistVideoModal);
  el.playlistVideoModal?.classList.remove("hidden");
}

async function submitPlaylistVideoModal() {
  if (!pendingPlaylistStreamName) {
    closePlaylistVideoModal();
    return;
  }
  const selected = selectedPlaylistModalVideos();
  if (!selected.length) {
    markFieldInvalid(el.playlistVideoPicker);
    toast("Выберите хотя бы один файл", "err");
    return;
  }

  setButtonLoading(el.playlistVideoSubmitBtn, true);
  setPageLoading(true, `Добавляю видео в ${pendingPlaylistStreamName}...`);
  try {
    const results = await Promise.allSettled(
      selected.map((videoName) =>
        api(`/backend/streams/${encodeURIComponent(pendingPlaylistStreamName)}/playlist/add-video`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ video_name: videoName }),
        }),
      ),
    );

    const failed = results
      .map((res, idx) => ({ res, name: selected[idx] }))
      .filter((x) => x.res.status === "rejected")
      .map((x) => `${x.name}: ${normalizeErrorMessage(x.res.reason)}`);

    if (failed.length) {
      notifyError("Часть видео не добавлена", failed.join(" | "));
    } else {
      toast(`Добавлено видео: ${selected.length}`);
    }

    closePlaylistVideoModal();
    await loadStreams();
  } catch (error) {
    notifyError("Ошибка добавления видео в плейлист", error);
  } finally {
    setButtonLoading(el.playlistVideoSubmitBtn, false);
    setPageLoading(false);
  }
}

function selectedCreateVideos() {
  if (!el.createStreamVideoPicker) return [];
  return Array.from(el.createStreamVideoPicker.querySelectorAll('input[name="create_video"]:checked')).map((x) => x.value);
}

function closeCreateStreamModal() {
  clearModalValidation(el.createStreamModal);
  el.createStreamModal?.classList.add("hidden");
}

function renderCreateStreamVideoPicker() {
  if (!el.createStreamVideoPicker) return;
  if (!state.videos.length) {
    el.createStreamVideoPicker.innerHTML = `<div class="video-pick-row"><span>Нет видео для выбора.</span></div>`;
    return;
  }

  el.createStreamVideoPicker.innerHTML = state.videos
    .map((video) => {
      const disabled = !video.enabled;
      const refsCount = Array.isArray(video.in_use_by) ? video.in_use_by.length : 0;
      return `
        <label class="video-pick-row ${disabled ? "disabled" : ""}">
          <input type="checkbox" name="create_video" value="${video.name}" ${disabled ? "disabled" : ""} />
          <span>${video.name}</span>
          <span class="meta">${disabled ? "отключено" : refsCount ? `в ${refsCount} стрим(ах)` : "доступно"}</span>
        </label>`;
    })
    .join("");
}

function openCreateStreamModal() {
  if (!state.videos.length) {
    toast("Сначала загрузите видео", "err");
    return;
  }
  renderCreateStreamVideoPicker();
  el.createStreamName.value = "";
  el.createStreamMode.value = "single";
  el.createStreamCodec.value = "auto";
  el.createStreamStartNow.checked = true;
  clearModalValidation(el.createStreamModal);
  el.createStreamModal.classList.remove("hidden");
}

async function submitCreateStream() {
  clearFieldInvalid(el.createStreamName, el.createStreamMode, el.createStreamCodec, el.createStreamVideoPicker);
  const name = (el.createStreamName.value || "").trim();
  const mode = el.createStreamMode.value;
  const codec_mode = el.createStreamCodec.value;
  const files = selectedCreateVideos();
  const start_after_create = Boolean(el.createStreamStartNow.checked);

  if (!name) {
    markFieldInvalid(el.createStreamName);
    toast("Укажите имя стрима", "err");
    return;
  }
  if (!files.length) {
    markFieldInvalid(el.createStreamVideoPicker);
    toast("Выберите хотя бы один видеофайл", "err");
    return;
  }
  if (mode === "single" && files.length !== 1) {
    markFieldInvalid(el.createStreamMode);
    markFieldInvalid(el.createStreamVideoPicker);
    toast("Для single выберите ровно один файл", "err");
    return;
  }
  if (mode === "playlist" && files.length < 2) {
    markFieldInvalid(el.createStreamMode);
    markFieldInvalid(el.createStreamVideoPicker);
    toast("Для playlist выберите минимум два файла", "err");
    return;
  }

  setButtonLoading(el.createStreamSubmitBtn, true);
  setPageLoading(true, "Создаю стрим из выбранных видео...");
  try {
    const payload = { name, mode, files, codec_mode, start_after_create };
    const result = await createStreamFromVideosRequest(payload);

    closeCreateStreamModal();
    if (result.started) {
      toast(`Стрим ${name} создан и запущен`);
    } else {
      toast(`Стрим ${name} создан, но не запущен: ${result.start_error || "проверьте ffmpeg/MediaMTX"}`, "err");
    }
    await refreshAll(false);
  } catch (error) {
    notifyError("Ошибка создания стрима", error);
  } finally {
    setButtonLoading(el.createStreamSubmitBtn, false);
    setPageLoading(false);
  }
}

function toLocalDateTimeValue(date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function closeQuickTimerModal() {
  clearModalValidation(el.quickTimerModal);
  el.quickTimerModal.classList.add("hidden");
}

function selectedQuickTimerStreams() {
  if (!el.quickTimerStreamPicker) return [];
  return Array.from(el.quickTimerStreamPicker.querySelectorAll('input[name="quick_timer_stream"]:checked')).map((x) => x.value);
}

function renderQuickTimerStreamPicker() {
  if (!el.quickTimerStreamPicker) return;
  if (!state.streams.length) {
    el.quickTimerStreamPicker.innerHTML = `<div class="video-pick-row"><span>Нет доступных стримов.</span></div>`;
    return;
  }

  const sorted = state.streams
    .slice()
    .sort((a, b) => {
      const aScore = a.status === "running" ? 0 : 1;
      const bScore = b.status === "running" ? 0 : 1;
      if (aScore !== bScore) return aScore - bScore;
      return a.name.localeCompare(b.name, "ru");
    });

  el.quickTimerStreamPicker.innerHTML = sorted
    .map((stream) => {
      const stateText = stream.status === "running" ? "запущен" : "остановлен";
      return `
        <label class="video-pick-row">
          <input type="checkbox" name="quick_timer_stream" value="${stream.name}" />
          <span>${stream.name}</span>
          <span class="meta">${stateText}</span>
        </label>`;
    })
    .join("");
}

function openQuickTimerModal() {
  if (!state.streams.length) {
    toast("Сначала создайте хотя бы один стрим", "err");
    return;
  }

  renderQuickTimerStreamPicker();
  el.quickTimerAction.value = "start";
  el.quickTimerDate.value = toLocalDateTimeValue(new Date(Date.now() + 5 * 60 * 1000));
  clearModalValidation(el.quickTimerModal);
  el.quickTimerModal.classList.remove("hidden");
}

async function submitQuickTimer() {
  clearFieldInvalid(el.quickTimerStreamPicker, el.quickTimerAction, el.quickTimerDate);
  const streams = selectedQuickTimerStreams();
  const action = (el.quickTimerAction.value || "").trim();
  const startAt = (el.quickTimerDate.value || "").trim();

  if (!streams.length) {
    markFieldInvalid(el.quickTimerStreamPicker);
    toast("Выберите хотя бы один стрим", "err");
    return;
  }
  if (!startAt) {
    markFieldInvalid(el.quickTimerDate);
    toast("Укажите дату и время", "err");
    return;
  }

  setButtonLoading(el.quickTimerSubmitBtn, true);
  setPageLoading(true, "Создаю расписания...");
  try {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Almaty";
    const jobs = streams.map((stream) =>
      api("/backend/schedules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stream,
          action,
          cron: null,
          start_at: startAt,
          timezone,
          enabled: true,
        }),
      }),
    );
    const results = await Promise.allSettled(jobs);
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length > 0) {
      throw new Error(`успешно: ${streams.length - failed.length}, с ошибкой: ${failed.length}`);
    }
    closeQuickTimerModal();
    toast(`Создано правил: ${streams.length}`);
    await loadSchedules();
  } catch (error) {
    notifyError("Ошибка создания расписания", error);
  } finally {
    setButtonLoading(el.quickTimerSubmitBtn, false);
    setPageLoading(false);
  }
}

async function toggleSchedule(button, id, enabledNow) {
  setButtonLoading(button, true);
  setPageLoading(true, "Обновляю расписание...");
  try {
    await api(`/backend/schedules/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !enabledNow }),
    });
    toast("Расписание обновлено");
    await loadSchedules();
  } catch (error) {
    notifyError("Ошибка обновления расписания", error);
  } finally {
    setButtonLoading(button, false);
    setPageLoading(false);
  }
}

function closeDeleteScheduleModal() {
  pendingDeleteScheduleId = null;
  pendingDeleteScheduleLabel = null;
  clearModalValidation(el.deleteScheduleModal);
  el.deleteScheduleModal?.classList.add("hidden");
}

function openDeleteScheduleModal(id, stream, action) {
  pendingDeleteScheduleId = id;
  pendingDeleteScheduleLabel = `${stream || "stream"} · ${actionRu(action || "action")}`;
  if (el.deleteScheduleText) {
    el.deleteScheduleText.textContent = `Удалить правило "${pendingDeleteScheduleLabel}"?`;
  }
  el.deleteScheduleModal?.classList.remove("hidden");
}

async function confirmDeleteSchedule() {
  if (!pendingDeleteScheduleId) {
    closeDeleteScheduleModal();
    return;
  }
  const scheduleId = pendingDeleteScheduleId;
  const label = pendingDeleteScheduleLabel || scheduleId;
  setButtonLoading(el.deleteScheduleConfirmBtn, true);
  setPageLoading(true, "Удаляю расписание...");
  try {
    await api(`/backend/schedules/${encodeURIComponent(scheduleId)}`, { method: "DELETE" });
    closeDeleteScheduleModal();
    toast(`Правило удалено: ${label}`);
    await loadSchedules();
  } catch (error) {
    notifyError("Ошибка удаления расписания", error);
  } finally {
    setButtonLoading(el.deleteScheduleConfirmBtn, false);
    setPageLoading(false);
  }
}

function closeBulkClearModal() {
  pendingBulkClearKind = null;
  clearModalValidation(el.bulkClearModal);
  el.bulkClearModal?.classList.add("hidden");
}

function openBulkClearModal(kind) {
  pendingBulkClearKind = kind;
  let title = "Подтвердите очистку";
  let text = "Вы уверены, что хотите выполнить очистку?";
  let hint = "Действие нельзя отменить.";

  if (kind === "streams") {
    title = "Очистить все стримы";
    text = `Будут удалены все стримы из конфигурации (${state.streams.length}).`;
    hint = "Будут остановлены процессы FFmpeg, очищены плейлисты и список стримов.";
  }
  if (kind === "videos") {
    title = "Очистить библиотеку видео";
    text = `Будет попытка удалить все видео (${state.videos.length}).`;
    hint = "Видео, которые используются стримами, останутся и будут показаны в ошибке.";
  }
  if (kind === "schedules") {
    title = "Очистить таймеры";
    text = `Будут удалены все правила расписаний (${state.schedules.length}).`;
    hint = "После удаления правила придется создавать заново.";
  }

  const titleNode = document.getElementById("bulkClearTitle");
  if (titleNode) titleNode.textContent = title;
  if (el.bulkClearText) el.bulkClearText.textContent = text;
  if (el.bulkClearHint) el.bulkClearHint.textContent = hint;
  el.bulkClearModal?.classList.remove("hidden");
}

async function clearActiveStreamsBatch() {
  if (!state.streams.length) return { total: 0, success: 0, failed: [] };
  const result = await api("/backend/streams/clear", { method: "POST" });
  const errors = result?.stop_errors && typeof result.stop_errors === "object" ? result.stop_errors : {};
  const failed = Object.entries(errors).map(([name, err]) => `${name}: ${err}`);
  const total = Number(result?.stopped_count || 0);
  const success = Math.max(0, total - failed.length);
  return { total, success, failed };
}

async function clearVideosBatch() {
  const allVideos = state.videos.map((x) => x.name);
  if (!allVideos.length) return { total: 0, success: 0, failed: [] };
  const results = await Promise.allSettled(
    allVideos.map((name) => api(`/backend/videos/${encodeURIComponent(name)}`, { method: "DELETE" })),
  );
  const failed = results
    .map((res, idx) => ({ res, name: allVideos[idx] }))
    .filter((x) => x.res.status === "rejected")
    .map((x) => `${x.name}: ${normalizeErrorMessage(x.res.reason)}`);
  return { total: allVideos.length, success: allVideos.length - failed.length, failed };
}

async function clearSchedulesBatch() {
  const ids = state.schedules.map((x) => x.id);
  if (!ids.length) return { total: 0, success: 0, failed: [] };
  const results = await Promise.allSettled(
    ids.map((id) => api(`/backend/schedules/${encodeURIComponent(id)}`, { method: "DELETE" })),
  );
  const failed = results
    .map((res, idx) => ({ res, id: ids[idx] }))
    .filter((x) => x.res.status === "rejected")
    .map((x) => `${x.id}: ${normalizeErrorMessage(x.res.reason)}`);
  return { total: ids.length, success: ids.length - failed.length, failed };
}

async function confirmBulkClear() {
  if (!pendingBulkClearKind) {
    closeBulkClearModal();
    return;
  }

  setButtonLoading(el.bulkClearConfirmBtn, true);
  setPageLoading(true, "Выполняю массовую очистку...");
  try {
    let result = { total: 0, success: 0, failed: [] };
    if (pendingBulkClearKind === "streams") result = await clearActiveStreamsBatch();
    if (pendingBulkClearKind === "videos") result = await clearVideosBatch();
    if (pendingBulkClearKind === "schedules") result = await clearSchedulesBatch();

    closeBulkClearModal();

    if (result.total === 0) {
      toast("Список уже пуст", "ok");
    } else if (!result.failed.length) {
      toast(`Очистка выполнена: ${result.success}/${result.total}`, "ok");
    } else {
      toast(`Частично: ${result.success}/${result.total}. Ошибок: ${result.failed.length}`, "err");
      if (result.failed.length) {
        notifyError("Детали очистки", result.failed.join(" | "));
      }
    }

    await refreshAll(false);
  } catch (error) {
    notifyError("Ошибка очистки", error);
  } finally {
    setButtonLoading(el.bulkClearConfirmBtn, false);
    setPageLoading(false);
  }
}

function selectedBootstrapVideos() {
  if (!el.bootstrapVideoPicker) return [];
  return Array.from(el.bootstrapVideoPicker.querySelectorAll('input[name="bootstrap_video"]:checked')).map((x) => x.value);
}

function applyBootstrapVideoSearchFilter() {
  if (!el.bootstrapVideoPicker) return;
  const search = String(bootstrapVideoSearchQuery || "").trim().toLowerCase();
  const rows = Array.from(el.bootstrapVideoPicker.querySelectorAll(".video-pick-row[data-video-name]"));

  let visibleIndex = 0;
  for (const row of rows) {
    const name = String(row.dataset.videoName || "").toLowerCase();
    const matched = !search || name.includes(search);
    row.classList.toggle("hidden-by-search", !matched);
    if (matched) {
      visibleIndex += 1;
      const numberEl = row.querySelector(".video-pick-num");
      if (numberEl) numberEl.textContent = `${visibleIndex}.`;
    }
  }
}

function closeBootstrapModal() {
  clearModalValidation(el.bootstrapModal);
  el.bootstrapModal?.classList.add("hidden");
}

function syncBootstrapModeUI() {
  const mode = el.bootstrapMode.value;
  const isMulti = mode === "multi";
  el.bootstrapNameWrap.classList.toggle("hidden", isMulti);
  el.bootstrapPrefixWrap.classList.toggle("hidden", !isMulti);

  if (mode === "single") {
    el.bootstrapModeHint.textContent = "Single: выберите ровно один файл. Будет создан один RTSP-стрим.";
  }
  if (mode === "playlist") {
    el.bootstrapModeHint.textContent = "Playlist: выберите минимум два файла. Они будут проигрываться по кругу как один стрим.";
  }
  if (mode === "multi") {
    el.bootstrapModeHint.textContent =
      "Multi: выберите один или несколько файлов. Для каждого файла будет создан отдельный стрим с именем <префикс>001, 002, ...";
  }
}

function renderBootstrapVideoPicker() {
  if (!el.bootstrapVideoPicker) return;
  const enabledVideos = state.videos.filter((v) => v.enabled);
  if (!enabledVideos.length) {
    el.bootstrapVideoPicker.innerHTML = `<div class="video-pick-row"><span>Нет включенных видео для запуска.</span></div>`;
    return;
  }

  el.bootstrapVideoPicker.innerHTML = enabledVideos
    .map((video, index) => {
      const refsCount = Array.isArray(video.in_use_by) ? video.in_use_by.length : 0;
      return `
        <label class="video-pick-row" data-video-name="${video.name}">
          <span class="video-pick-num">${index + 1}.</span>
          <input type="checkbox" name="bootstrap_video" value="${video.name}" />
          <span>${video.name}</span>
          <span class="meta">${refsCount ? `уже в ${refsCount} стрим(ах)` : "готово к запуску"}</span>
        </label>`;
    })
    .join("");

  applyBootstrapVideoSearchFilter();
}

function openBootstrapModal() {
  if (!state.videos.length) {
    toast("Сначала загрузите видео", "err");
    return;
  }

  el.bootstrapMode.value = "single";
  el.bootstrapName.value = "";
  el.bootstrapPrefix.value = "cam_auto_";
  el.bootstrapCodec.value = "auto";
  el.bootstrapStartNow.checked = true;
  bootstrapVideoSearchQuery = "";
  if (el.bootstrapVideoSearch) el.bootstrapVideoSearch.value = "";
  renderBootstrapVideoPicker();
  syncBootstrapModeUI();
  clearModalValidation(el.bootstrapModal);
  el.bootstrapModal.classList.remove("hidden");
  el.bootstrapVideoSearch?.focus();
}

function nextStreamNameByPrefix(prefix, usedNames) {
  let i = 1;
  while (true) {
    const candidate = `${prefix}${String(i).padStart(3, "0")}`;
    if (!usedNames.has(candidate)) return candidate;
    i += 1;
  }
}

async function submitBootstrapModal() {
  clearFieldInvalid(el.bootstrapMode, el.bootstrapName, el.bootstrapPrefix, el.bootstrapCodec, el.bootstrapVideoPicker);
  const mode = el.bootstrapMode.value;
  const files = selectedBootstrapVideos();
  const codecMode = el.bootstrapCodec.value;
  const startNow = Boolean(el.bootstrapStartNow.checked);
  const name = (el.bootstrapName.value || "").trim();
  const prefix = (el.bootstrapPrefix.value || "").trim();

  if (!files.length) {
    markFieldInvalid(el.bootstrapVideoPicker);
    toast("Выберите хотя бы один видеофайл", "err");
    return;
  }

  if (mode === "single") {
    if (!name) {
      markFieldInvalid(el.bootstrapName);
      toast("Укажите имя стрима", "err");
      return;
    }
    if (files.length !== 1) {
      markFieldInvalid(el.bootstrapMode);
      markFieldInvalid(el.bootstrapVideoPicker);
      toast("Для single выберите ровно один файл", "err");
      return;
    }
  }

  if (mode === "playlist") {
    if (!name) {
      markFieldInvalid(el.bootstrapName);
      toast("Укажите имя стрима", "err");
      return;
    }
    if (files.length < 2) {
      markFieldInvalid(el.bootstrapMode);
      markFieldInvalid(el.bootstrapVideoPicker);
      toast("Для playlist выберите минимум два файла", "err");
      return;
    }
  }

  if (mode === "multi" && !prefix) {
    markFieldInvalid(el.bootstrapPrefix);
    toast("Укажите префикс имен для multi", "err");
    return;
  }

  setButtonLoading(el.bootstrapSubmitBtn, true);
  setPageLoading(true, "Создаю и запускаю стримы...");
  try {
    if (mode === "single" || mode === "playlist") {
      const payload = {
        name,
        mode,
        files,
        codec_mode: codecMode,
        start_after_create: startNow,
      };
      const result = await createStreamFromVideosRequest(payload);

      closeBootstrapModal();
      if (result.started) {
        toast(`Стрим ${name} создан и запущен`);
      } else {
        toast(`Стрим ${name} создан, но не запущен: ${result.start_error || "проверьте ffmpeg/MediaMTX"}`, "err");
      }
      await refreshAll(false);
      return;
    }

    const usedNames = new Set(state.streams.map((s) => s.name));
    const created = [];
    const started = [];
    const notStarted = [];
    const failed = [];

    for (const videoName of files) {
      const streamName = nextStreamNameByPrefix(prefix, usedNames);
      usedNames.add(streamName);

      try {
        const result = await createStreamFromVideosRequest({
          name: streamName,
          mode: "single",
          files: [videoName],
          codec_mode: codecMode,
          start_after_create: startNow,
        });
        created.push(streamName);
        if (result.started) {
          started.push(streamName);
        } else if (startNow) {
          notStarted.push(`${streamName}: ${result.start_error || "не запущен"}`);
        }
      } catch (error) {
        failed.push(`${videoName}: ${normalizeErrorMessage(error)}`);
      }
    }

    closeBootstrapModal();
    if (created.length) {
      if (startNow) {
        toast(
          `Создано: ${created.length}, запущено: ${started.length}, не запущено: ${notStarted.length}, ошибок: ${failed.length}`,
          notStarted.length || failed.length ? "err" : "ok",
        );
      } else {
        toast(`Создано стримов: ${created.length}${failed.length ? `, ошибок: ${failed.length}` : ""}`, failed.length ? "err" : "ok");
      }
    } else {
      toast(`Не удалось создать стримы: ${failed.join(" | ")}`, "err");
    }
    await refreshAll(false);
  } catch (error) {
    notifyError("Ошибка создания стримов", error);
  } finally {
    setButtonLoading(el.bootstrapSubmitBtn, false);
    setPageLoading(false);
  }
}

function attachStreamHandlers(container) {
  container.addEventListener("click", async (event) => {
    const target = event.target.closest("button[data-action]");
    if (!target) return;

    const action = target.dataset.action;
    if (!action) return;

    if (["start", "stop", "restart"].includes(action)) {
      const name = target.dataset.name;
      if (!name) return;
      await handleStreamAction(target, name, action);
      return;
    }

    if (action === "removePlaylistVideo") {
      const streamName = target.dataset.stream;
      const videoName = target.dataset.video;
      if (!streamName || !videoName) return;
      await removeVideoFromPlaylist(target, streamName, videoName);
      return;
    }

    if (action === "openAddPlaylistVideo") {
      const streamName = target.dataset.stream;
      if (!streamName) return;
      openPlaylistVideoModal(streamName);
      return;
    }

    if (action === "copyUrl") {
      const url = target.dataset.url || "";
      if (!url) return;
      try {
        await copyToClipboard(url);
        toast("RTSP ссылка скопирована");
      } catch (error) {
        notifyError("Ошибка копирования", error);
      }
    }
  });
}

attachStreamHandlers(el.activeStreamList);

el.activeStreamPager?.addEventListener("click", (event) => {
  const target = event.target.closest("button[data-action][data-kind]");
  if (!target) return;
  const kind = target.dataset.kind;
  const action = target.dataset.action;
  if (kind !== "streams") return;
  if (action === "pagePrev") changePage("streams", -1);
  if (action === "pageNext") changePage("streams", 1);
});

el.videoPager?.addEventListener("click", (event) => {
  const target = event.target.closest("button[data-action][data-kind]");
  if (!target) return;
  const kind = target.dataset.kind;
  const action = target.dataset.action;
  if (kind !== "videos") return;
  if (action === "pagePrev") changePage("videos", -1);
  if (action === "pageNext") changePage("videos", 1);
});

el.logPager?.addEventListener("click", (event) => {
  const target = event.target.closest("button[data-action][data-kind]");
  if (!target) return;
  const kind = target.dataset.kind;
  const action = target.dataset.action;
  if (kind !== "logs") return;
  if (action === "pagePrev") changePage("logs", -1);
  if (action === "pageNext") changePage("logs", 1);
});

el.videoList.addEventListener("click", async (event) => {
  const target = event.target.closest("button[data-action]");
  if (!target) return;

  const action = target.dataset.action;
  const name = target.dataset.name;
  if (!action || !name) return;

  if (action === "enableVideo") await setVideoEnabled(target, name, true);
  if (action === "disableVideo") await setVideoEnabled(target, name, false);
  if (action === "deleteVideo") openDeleteVideoModal(name);
});

el.scheduleList.addEventListener("click", async (event) => {
  const target = event.target.closest("button[data-action]");
  if (!target) return;

  const action = target.dataset.action;
  const id = target.dataset.id;
  if (!action || !id) return;

  if (action === "toggleSchedule") {
    await toggleSchedule(target, id, target.dataset.enabled === "true");
  }
  if (action === "deleteSchedule") {
    openDeleteScheduleModal(id, target.dataset.stream, target.dataset.kind);
  }
});

el.videoUpload.addEventListener("change", async () => {
  await uploadVideo(el.videoUpload.files?.[0]);
  el.videoUpload.value = "";
});

el.bootstrapStreamsBtn?.addEventListener("click", () => openBootstrapModal());
el.addScheduleBtn.addEventListener("click", () => openQuickTimerModal());
el.clearActiveStreamsBtn?.addEventListener("click", () => openBulkClearModal("streams"));
el.clearVideosBtn?.addEventListener("click", () => openBulkClearModal("videos"));
el.clearSchedulesBtn?.addEventListener("click", () => openBulkClearModal("schedules"));
el.streamTypeFilterBtn?.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleFilterMenu(el.streamTypeFilterMenu);
});
el.logTypeFilterBtn?.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleFilterMenu(el.logTypeFilterMenu);
});
el.streamTypeFilterMenu?.addEventListener("click", (event) => event.stopPropagation());
el.logTypeFilterMenu?.addEventListener("click", (event) => event.stopPropagation());
el.streamTypeFilterMenu?.addEventListener("change", () => {
  syncFilterStateFromDom();
  state.pagination.streams.page = 1;
  renderStreams();
});
el.logTypeFilterMenu?.addEventListener("change", () => {
  syncFilterStateFromDom();
  state.pagination.logs.page = 1;
  renderImportantLogs();
});
focusSectionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    toggleFocusedSection(button.dataset.section || "");
  });
});
el.bootstrapMode?.addEventListener("change", syncBootstrapModeUI);
el.quickTimerPickAllBtn?.addEventListener("click", () => {
  el.quickTimerStreamPicker
    ?.querySelectorAll('input[name="quick_timer_stream"]:not(:disabled)')
    .forEach((x) => {
      x.checked = true;
    });
  el.quickTimerStreamPicker?.classList.remove("field-invalid");
});
el.quickTimerClearAllBtn?.addEventListener("click", () => {
  el.quickTimerStreamPicker?.querySelectorAll('input[name="quick_timer_stream"]').forEach((x) => {
    x.checked = false;
  });
});
el.deleteVideoCancelBtn?.addEventListener("click", () => closeDeleteVideoModal());
el.deleteVideoModal?.addEventListener("click", (event) => {
  if (event.target === el.deleteVideoModal) closeDeleteVideoModal();
});
el.deleteVideoConfirmBtn?.addEventListener("click", async () => {
  await confirmDeleteVideo();
});
el.deleteScheduleCancelBtn?.addEventListener("click", () => closeDeleteScheduleModal());
el.deleteScheduleModal?.addEventListener("click", (event) => {
  if (event.target === el.deleteScheduleModal) closeDeleteScheduleModal();
});
el.deleteScheduleConfirmBtn?.addEventListener("click", async () => {
  await confirmDeleteSchedule();
});
el.bulkClearCancelBtn?.addEventListener("click", () => closeBulkClearModal());
el.bulkClearModal?.addEventListener("click", (event) => {
  if (event.target === el.bulkClearModal) closeBulkClearModal();
});
el.bulkClearConfirmBtn?.addEventListener("click", async () => {
  await confirmBulkClear();
});
el.bootstrapPickAllBtn?.addEventListener("click", () => {
  el.bootstrapVideoPicker
    ?.querySelectorAll('.video-pick-row[data-video-name]:not(.hidden-by-search) input[name="bootstrap_video"]:not(:disabled)')
    .forEach((x) => {
      x.checked = true;
    });
});
el.bootstrapClearAllBtn?.addEventListener("click", () => {
  el.bootstrapVideoPicker?.querySelectorAll('input[name="bootstrap_video"]').forEach((x) => {
    x.checked = false;
  });
});
el.bootstrapCancelBtn?.addEventListener("click", () => closeBootstrapModal());
el.bootstrapModal?.addEventListener("click", (event) => {
  if (event.target === el.bootstrapModal) closeBootstrapModal();
});
el.bootstrapForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitBootstrapModal();
});
el.bootstrapVideoSearch?.addEventListener("input", () => {
  bootstrapVideoSearchQuery = el.bootstrapVideoSearch?.value || "";
  applyBootstrapVideoSearchFilter();
});
el.quickTimerCancelBtn?.addEventListener("click", () => closeQuickTimerModal());
el.quickTimerModal?.addEventListener("click", (event) => {
  if (event.target === el.quickTimerModal) closeQuickTimerModal();
});
el.playlistVideoCancelBtn?.addEventListener("click", () => closePlaylistVideoModal());
el.playlistVideoModal?.addEventListener("click", (event) => {
  if (event.target === el.playlistVideoModal) closePlaylistVideoModal();
});
el.playlistVideoPickAllBtn?.addEventListener("click", () => {
  el.playlistVideoPicker
    ?.querySelectorAll('input[name="playlist_add_video"]:not(:disabled)')
    .forEach((x) => {
      x.checked = true;
    });
  el.playlistVideoPicker?.classList.remove("field-invalid");
});
el.playlistVideoClearAllBtn?.addEventListener("click", () => {
  el.playlistVideoPicker?.querySelectorAll('input[name="playlist_add_video"]').forEach((x) => {
    x.checked = false;
  });
});
el.playlistVideoForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitPlaylistVideoModal();
});
el.createStreamCancelBtn?.addEventListener("click", () => closeCreateStreamModal());
el.createStreamModal?.addEventListener("click", (event) => {
  if (event.target === el.createStreamModal) closeCreateStreamModal();
});
el.createStreamForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitCreateStream();
});
el.quickTimerForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitQuickTimer();
});

[el.bootstrapForm, el.createStreamForm, el.quickTimerForm].forEach((form) => {
  form?.addEventListener("input", (event) => {
    event.target?.classList?.remove("field-invalid");
  });
  form?.addEventListener("change", (event) => {
    event.target?.classList?.remove("field-invalid");
    el.bootstrapVideoPicker?.classList.remove("field-invalid");
    el.createStreamVideoPicker?.classList.remove("field-invalid");
    el.quickTimerStreamPicker?.classList.remove("field-invalid");
    el.playlistVideoPicker?.classList.remove("field-invalid");
  });
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeAllFilterMenus();
  }
  if (event.key !== "Escape") return;
  if (state.focusedSection) {
    setFocusedSection(null);
  }
  if (el.quickTimerModal && !el.quickTimerModal.classList.contains("hidden")) {
    closeQuickTimerModal();
  }
  if (el.deleteVideoModal && !el.deleteVideoModal.classList.contains("hidden")) {
    closeDeleteVideoModal();
  }
  if (el.deleteScheduleModal && !el.deleteScheduleModal.classList.contains("hidden")) {
    closeDeleteScheduleModal();
  }
  if (el.bulkClearModal && !el.bulkClearModal.classList.contains("hidden")) {
    closeBulkClearModal();
  }
  if (el.bootstrapModal && !el.bootstrapModal.classList.contains("hidden")) {
    closeBootstrapModal();
  }
  if (el.createStreamModal && !el.createStreamModal.classList.contains("hidden")) {
    closeCreateStreamModal();
  }
  if (el.playlistVideoModal && !el.playlistVideoModal.classList.contains("hidden")) {
    closePlaylistVideoModal();
  }
});

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const inFilter = target.closest(".filter-wrap");
  if (!inFilter) closeAllFilterMenus();
});

window.addEventListener("error", forceHideLoader);
window.addEventListener("unhandledrejection", forceHideLoader);
let resizeTimer = null;
window.addEventListener("resize", () => {
  if (resizeTimer) window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    renderStreams();
    renderVideos();
  }, 120);
});

syncFocusButtons();
syncFilterStateFromDom();
refreshAll(false);
window.setInterval(() => {
  loadStreams().catch(() => {});
}, 6000);
window.setInterval(() => {
  loadHealth().catch(() => {});
}, 8000);
window.setInterval(() => {
  loadSchedules().catch(() => {});
}, 10000);
window.setInterval(() => {
  loadImportantLogs().catch(() => {});
}, 12000);
window.setInterval(() => {
  loadStorage().catch(() => {});
}, 30000);
