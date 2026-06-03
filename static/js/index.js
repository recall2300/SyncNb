let activities = [];
let currentUser = null;
let garminConnected = false;
let stravaConnected = false;
let garminOffset = 0;   // raw Garmin API offset (all activity types)
let hasMore = false;
const PAGE_LIMIT = 30;

// ── API helper ───────────────────────────────────────────────────────────────

async function apiFetch(url, options = {}) {
  const headers = { "Accept": "application/json", ...(options.headers || {}) };
  return fetch(url, { ...options, headers });
}

// ── Init ──────────────────────────────────────────────────────────────────────

(async function init() {
  const params = new URLSearchParams(location.search);
  if (params.get("strava_ok"))    showToast("Strava 연결 완료!", "success");
  if (params.get("strava_error")) showToast("Strava 오류: " + params.get("strava_error"), "error");
  if (params.get("strava_ok") || params.get("strava_error")) history.replaceState({}, "", "/");
  await refreshStatus();
})();

async function refreshStatus() {
  try {
    const res = await apiFetch("/me");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    currentUser = data;
    document.getElementById("admin-link").style.display = data.is_admin ? "" : "none";
    setGarminStatus(data.garmin_connected);
    setStravaStatus(data.strava_connected);
    if (data.garmin_connected) loadActivities(false);
  } catch(e) {}
}

// ── Status badges ─────────────────────────────────────────────────────────────

// 모바일(≤640px)에서는 "연결됨" 없이 서비스명만 표시 — dot 색상으로 연결 여부 구분
const _isMobileHeader = () => window.matchMedia("(max-width: 640px)").matches;

function setGarminStatus(connected) {
  garminConnected = connected;
  document.getElementById("garmin-badge").className = "badge " + (connected ? "connected" : "disconnected");
  document.getElementById("garmin-dot").className   = "dot " + (connected ? "on" : "off");
  document.getElementById("garmin-label").textContent =
    (!_isMobileHeader() && connected) ? "Garmin 연결됨" : "Garmin";
}

function setStravaStatus(connected) {
  stravaConnected = connected;
  document.getElementById("strava-badge").className = "badge " + (connected ? "connected" : "disconnected");
  document.getElementById("strava-dot").className   = "dot " + (connected ? "on" : "off");
  document.getElementById("strava-label").textContent =
    (!_isMobileHeader() && connected) ? "Strava 연결됨" : "Strava";
}

// ── Garmin ────────────────────────────────────────────────────────────────────

function handleGarminClick() {
  if (garminConnected) {
    if (confirm("Garmin 연결을 해제하시겠습니까?")) logoutGarmin();
  } else {
    openGarminModal();
  }
}

function openGarminModal() {
  document.getElementById("modal-login-form").style.display = "";
  document.getElementById("modal-mfa-form").style.display   = "none";
  document.getElementById("modal-title").textContent = "Garmin 로그인";
  document.getElementById("modal-error").textContent = "";
  document.getElementById("g-email").value    = "";
  document.getElementById("g-password").value = "";
  document.getElementById("garmin-modal").showModal();
}

function closeGarminModal() { document.getElementById("garmin-modal").close(); }

async function submitGarminLogin() {
  const email    = document.getElementById("g-email").value.trim();
  const password = document.getElementById("g-password").value;
  if (!email || !password) { document.getElementById("modal-error").textContent = "이메일과 비밀번호를 입력해주세요."; return; }
  setModalLoading("login-btn", true);
  document.getElementById("modal-error").textContent = "";
  try {
    const res  = await apiFetch("/garmin/login", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email, password}) });
    const data = await res.json();
    if (data.ok) {
      closeGarminModal(); setGarminStatus(true); loadActivities(false); showToast("Garmin 로그인 성공!", "success");
    } else if (data.mfa_required) {
      document.getElementById("modal-login-form").style.display = "none";
      document.getElementById("modal-mfa-form").style.display   = "";
      document.getElementById("modal-title").textContent = "2단계 인증";
      document.getElementById("g-mfa").focus();
    } else {
      document.getElementById("modal-error").textContent = data.error || "로그인 실패";
    }
  } catch(e) { document.getElementById("modal-error").textContent = "오류: " + e.message; }
  finally    { setModalLoading("login-btn", false); }
}

async function submitMfa() {
  const code = document.getElementById("g-mfa").value.trim();
  if (!code) { document.getElementById("modal-error").textContent = "인증 코드를 입력해주세요."; return; }
  setModalLoading("mfa-btn", true);
  document.getElementById("modal-error").textContent = "";
  try {
    const res  = await apiFetch("/garmin/mfa", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({code}) });
    const data = await res.json();
    if (data.ok) { closeGarminModal(); setGarminStatus(true); loadActivities(false); showToast("Garmin 로그인 성공!", "success"); }
    else         { document.getElementById("modal-error").textContent = data.error || "인증 실패"; }
  } catch(e) { document.getElementById("modal-error").textContent = "오류: " + e.message; }
  finally    { setModalLoading("mfa-btn", false); }
}

async function logoutGarmin() {
  await apiFetch("/garmin/logout", {method: "POST"});
  setGarminStatus(false);
  activities = [];
  garminOffset = 0;
  hasMore = false;
  document.getElementById("table-wrap").innerHTML = '<div id="empty-msg">Garmin에 로그인하면 러닝 활동이 표시됩니다.</div>';
  document.getElementById("sync-btn").disabled = true;
  showToast("Garmin 연결 해제됨", "info");
}

// ── Strava ────────────────────────────────────────────────────────────────────

function handleStravaClick() {
  if (stravaConnected) {
    if (confirm("Strava 연결을 해제하시겠습니까?")) disconnectStrava();
  } else {
    window.location.href = "/strava/connect";
  }
}

async function disconnectStrava() {
  await apiFetch("/strava/disconnect", {method: "POST"});
  setStravaStatus(false);
  showToast("Strava 연결 해제됨", "info");
  renderTable();
}

// ── Profile / Auth ────────────────────────────────────────────────────────────

function openProfileModal() {
  if (currentUser) {
    document.getElementById("profile-username").value = currentUser.username;
    const toggle = document.getElementById("autosync-toggle");
    if (toggle) toggle.checked = !!currentUser.auto_sync_enabled;
    updateAutoSyncNote(!!currentUser.auto_sync_enabled);
  }
  document.getElementById("pw-current").value = "";
  document.getElementById("pw-new").value     = "";
  document.getElementById("pw-new2").value    = "";
  document.getElementById("pw-error").textContent = "";
  document.getElementById("profile-modal").showModal();
}

async function handleAutoSync(cb) {
  cb.disabled = true;
  try {
    const res  = await apiFetch("/auto-sync", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled: cb.checked}),
    });
    const data = await res.json();
    if (data.ok) {
      if (currentUser) currentUser.auto_sync_enabled = data.auto_sync_enabled;
      updateAutoSyncNote(data.auto_sync_enabled);
      showToast(data.auto_sync_enabled ? "자동 동기화 활성화됨" : "자동 동기화 비활성화됨",
                data.auto_sync_enabled ? "success" : "info");
    } else {
      cb.checked = !cb.checked;
      showToast(data.error || "설정 변경 실패", "error");
    }
  } catch(e) {
    cb.checked = !cb.checked;
    showToast("오류: " + e.message, "error");
  } finally {
    cb.disabled = false;
  }
}

function updateAutoSyncNote(enabled) {
  const note = document.getElementById("autosync-note");
  if (!note) return;
  if (enabled) {
    note.textContent = "✓ 활성화됨 — 최신 활동 5개를 2분마다 확인합니다.";
    note.style.color = "var(--success)";
  } else {
    note.textContent = "비활성화됨";
    note.style.color = "var(--text-3)";
  }
}

async function changePassword() {
  const current = document.getElementById("pw-current").value;
  const newPw   = document.getElementById("pw-new").value;
  const newPw2  = document.getElementById("pw-new2").value;
  const errEl   = document.getElementById("pw-error");
  errEl.textContent = "";
  if (!current)          { errEl.textContent = "현재 비밀번호를 입력해주세요."; return; }
  if (newPw !== newPw2)  { errEl.textContent = "새 비밀번호가 일치하지 않습니다."; return; }
  if (newPw.length < 8)  { errEl.textContent = "새 비밀번호는 8자 이상이어야 합니다."; return; }
  const btn = document.getElementById("pw-btn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>변경중...';
  try {
    const res  = await apiFetch("/me/password", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({current, new: newPw}) });
    const data = await res.json();
    if (data.ok) {
      // 서버에서 세션을 초기화하므로 재로그인 필요
      showToast("비밀번호가 변경되었습니다. 다시 로그인해주세요.", "success");
      setTimeout(() => { window.location.href = "/login"; }, 1500);
    } else {
      errEl.textContent = data.error || "변경 실패";
    }
  } catch(e) { errEl.textContent = "오류: " + e.message; }
  finally    { btn.disabled = false; btn.textContent = "변경"; }
}

async function doLogout() {
  await apiFetch("/logout", {method: "POST"});
  window.location.href = "/login";
}

// ── Activities (with pagination) ──────────────────────────────────────────────

async function loadActivities(append, _autoSkips = 0) {
  if (!append) {
    garminOffset = 0;
    activities   = [];
    document.getElementById("table-wrap").innerHTML = '<div id="empty-msg">활동을 불러오는 중...</div>';
  } else {
    const btn = document.getElementById("load-more-btn");
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>불러오는 중...'; }
  }

  try {
    const res  = await apiFetch(`/activities?offset=${garminOffset}&limit=${PAGE_LIMIT}`);
    const data = await res.json();
    if (data.error) {
      // Garmin 세션 만료: 뱃지를 disconnected로 갱신해 사용자가 재연결하도록 안내
      if (data.garmin_error) setGarminStatus(false);
      if (!append) document.getElementById("table-wrap").innerHTML = `<div id="empty-msg" class="empty-error">${escHtml(data.error)}</div>`;
      else showToast(data.error, "error");
      return;
    }
    activities   = append ? [...activities, ...data.activities] : data.activities;
    hasMore      = data.has_more;
    garminOffset += PAGE_LIMIT;

    // 이번 페이지에 러닝 활동이 없지만 Garmin에 더 있으면 자동으로 다음 페이지 조회
    // (사이클·수영 등 비러닝 활동만 있는 페이지를 사용자가 직접 스킵해야 하는 불편 해소)
    if (data.activities.length === 0 && data.has_more && _autoSkips < 10) {
      await loadActivities(true, _autoSkips + 1);
      return;
    }

    renderTable();
  } catch(e) {
    if (!append) document.getElementById("table-wrap").innerHTML = `<div id="empty-msg" class="empty-error">오류: ${escHtml(e.message)}</div>`;
    else showToast("불러오기 오류: " + e.message, "error");
  }
}

function renderTable() {
  if (!activities.length) {
    document.getElementById("table-wrap").innerHTML = '<div id="empty-msg">러닝 활동이 없습니다.</div>';
    document.getElementById("sync-btn").disabled = true;
    return;
  }

  const rows = activities.map(a => {
    const dist = (a.distance_m / 1000).toFixed(2) + " km";
    const dur  = fmtDuration(a.duration_s);
    const date = a.start_time ? a.start_time.slice(0, 16).replace("T", " ") : "";
    const statusCell = a.synced
      ? '<span class="synced-badge">동기화됨</span>'
      : (a._syncing
          ? '<span class="pending-badge"><span class="spinner"></span>처리중</span>'
          : (a._error ? '<span class="error-badge">오류</span>' : ""));
    return `<tr id="row-${a.garmin_id}" class="clickable" onclick="openDetailModal('${a.garmin_id}')">
      <td onclick="event.stopPropagation()"><input type="checkbox" class="act-cb" data-id="${a.garmin_id}"></td>
      <td>${escHtml(date)}</td>
      <td>${escHtml(a.name || "러닝")}</td>
      <td>${dist}</td>
      <td>${dur}</td>
      <td>${statusCell}</td>
      <td class="note-cell">${buildNoteCell(a)}</td>
    </tr>`;
  }).join("");

  const loadMoreHtml = hasMore
    ? `<div class="load-more-wrap"><button id="load-more-btn" class="btn btn-secondary btn-sm" onclick="loadActivities(true)">이전 내역 불러오기</button></div>`
    : `<div class="load-more-wrap load-more-end">— 전체 ${activities.length}개 활동 —</div>`;

  document.getElementById("table-wrap").innerHTML = `
    <table>
      <thead>
        <tr>
          <th style="width:40px"><input type="checkbox" id="check-all" onchange="toggleAll(this)"></th>
          <th>날짜</th><th>이름</th><th>거리</th><th>시간</th><th>상태</th><th>비고</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    ${loadMoreHtml}`;
  document.getElementById("sync-btn").disabled = false;
}

function toggleAll(cb) {
  document.querySelectorAll(".act-cb:not(:disabled)").forEach(el => el.checked = cb.checked);
}

// ── Detail Modal ──────────────────────────────────────────────────────────────

let _activeMap = null;  // keep Leaflet instance to destroy on reopen

async function openDetailModal(garminId) {
  const a = activities.find(x => x.garmin_id === garminId);
  if (!a) return;

  // 이전 지도 인스턴스를 완전히 제거 (연달아 열 때 충돌 방지)
  if (_activeMap) { try { _activeMap.remove(); } catch(e) {} _activeMap = null; }

  const inner = document.getElementById("detail-inner");
  inner.innerHTML = buildDetailContent(a);
  document.getElementById("detail-modal").showModal();

  const [splitsRes, detailsRes] = await Promise.all([
    apiFetch("/activity/" + garminId + "/splits").catch(() => null),
    apiFetch("/activity/" + garminId + "/details").catch(() => null),
  ]);

  // 모달이 이미 닫혔으면 렌더링 중단 (빠른 닫기/재열기 케이스)
  if (!document.getElementById("detail-modal").open) return;

  let splitsData = {}, detailsData = {};
  try { if (splitsRes)  splitsData  = await splitsRes.json();  } catch(e) {}
  try { if (detailsRes) detailsData = await detailsRes.json(); } catch(e) {}

  const splitsEl = document.getElementById("detail-splits");
  if (splitsEl) {
    splitsEl.innerHTML = splitsData.error
      ? `<div class="detail-note detail-note-err">${escHtml(String(splitsData.error))}</div>`
      : renderSplits(splitsData.splits || []);
  }

  renderMap(detailsData.coords || [], splitsData.splits || []);
}

function buildDetailContent(a) {
  const name    = a.name || "러닝";
  const date    = a.start_time ? a.start_time.slice(0, 16).replace("T", " ") : "";
  const dist    = a.distance_m    ? (a.distance_m / 1000).toFixed(2) + " km" : "—";
  const dur     = a.duration_s    ? fmtDuration(a.duration_s) : "—";
  const pace    = (a.duration_s && a.distance_m) ? fmtPace(a.duration_s, a.distance_m) + " /km" : "—";
  const cal     = a.calories      != null ? a.calories + " kcal" : "—";
  const avgHR   = a.avg_hr        != null ? a.avg_hr + " bpm" : "—";
  const maxHR   = a.max_hr        != null ? a.max_hr + " bpm" : "—";
  const elev    = a.elevation_gain != null ? "+" + a.elevation_gain + " m" : "—";
  const elevL   = a.elevation_loss != null ? "-" + a.elevation_loss + " m" : "—";
  const cadence = a.avg_cadence   != null ? a.avg_cadence + " spm" : "—";
  const steps   = a.steps         != null ? a.steps.toLocaleString() : "—";
  const stride  = a.stride_length_cm != null ? a.stride_length_cm + " cm" : "—";

  let teLabel = "—";
  if (a.aerobic_te != null) {
    const bands = ["", "회복", "기초", "향상", "최고", "과부하"];
    const band  = bands[Math.min(Math.floor(a.aerobic_te), 5)] || "";
    teLabel = a.aerobic_te.toFixed(1) + (band ? " · " + band : "");
  }

  const movingNote = (a.moving_duration_s && a.moving_duration_s !== a.duration_s)
    ? ` <span class="moving-note">(이동 ${fmtDuration(a.moving_duration_s)})</span>`
    : "";

  const locationLine = a.location
    ? `<div class="detail-header-location">📍 ${escHtml(a.location)}</div>`
    : "";

  return `
    <div class="detail-header">
      <div>
        <div class="detail-header-title">${escHtml(name)}</div>
        <div class="detail-header-date">${escHtml(date)}</div>
        ${locationLine}
      </div>
      <button class="detail-close" onclick="document.getElementById('detail-modal').close()">×</button>
    </div>
    <div id="detail-map" class="map-container">
      <div class="map-loading"><span class="spinner"></span>지도 불러오는 중...</div>
    </div>
    <div class="detail-body">
      <div class="metric-grid">
        <div class="metric-box"><div class="metric-icon">🏃</div><div class="metric-value">${escHtml(dist)}</div><div class="metric-label">거리</div></div>
        <div class="metric-box"><div class="metric-icon">⏱</div><div class="metric-value">${escHtml(dur)}${movingNote}</div><div class="metric-label">시간</div></div>
        <div class="metric-box"><div class="metric-icon">⚡</div><div class="metric-value">${escHtml(pace)}</div><div class="metric-label">평균 페이스</div></div>
        <div class="metric-box"><div class="metric-icon">🔥</div><div class="metric-value">${escHtml(cal)}</div><div class="metric-label">칼로리</div></div>
        <div class="metric-box"><div class="metric-icon">❤️</div><div class="metric-value">${escHtml(avgHR)}</div><div class="metric-label">평균 심박</div></div>
        <div class="metric-box"><div class="metric-icon">💓</div><div class="metric-value">${escHtml(maxHR)}</div><div class="metric-label">최대 심박</div></div>
        <div class="metric-box"><div class="metric-icon">⛰️</div><div class="metric-value">${escHtml(elev)}</div><div class="metric-label">고도 획득</div></div>
        <div class="metric-box"><div class="metric-icon">⬇️</div><div class="metric-value">${escHtml(elevL)}</div><div class="metric-label">고도 하강</div></div>
        <div class="metric-box"><div class="metric-icon">👟</div><div class="metric-value">${escHtml(cadence)}</div><div class="metric-label">평균 케이던스</div></div>
        <div class="metric-box"><div class="metric-icon">🦶</div><div class="metric-value">${escHtml(stride)}</div><div class="metric-label">평균 보폭</div></div>
        <div class="metric-box"><div class="metric-icon">🧮</div><div class="metric-value">${escHtml(steps)}</div><div class="metric-label">총 발걸음</div></div>
        <div class="metric-box"><div class="metric-icon">📈</div><div class="metric-value">${escHtml(teLabel)}</div><div class="metric-label">훈련 효과</div></div>
      </div>
      <div id="detail-splits"><div class="detail-note">구간 기록 불러오는 중...</div></div>
    </div>`;
}

function renderMap(coords, splits) {
  const mapEl = document.getElementById("detail-map");
  if (!mapEl) return;

  if (!coords || !coords.length) {
    mapEl.innerHTML = '<div class="map-no-data">GPS 데이터 없음</div>';
    return;
  }

  mapEl.innerHTML = '<div id="leaflet-map"></div>';

  // dialog가 완전히 열리고 레이아웃이 확정된 뒤 Leaflet을 초기화한다.
  // - 80ms: showModal() 애니메이션 + 브라우저 레이아웃 계산 완료 대기
  // - invalidateSize()를 fitBounds() 이전에 호출해야 컨테이너 크기를 정확히 읽고 bounds를 잡는다.
  setTimeout(() => {
    if (typeof L === "undefined") { mapEl.innerHTML = '<div class="map-no-data">지도 라이브러리 로드 실패</div>'; return; }
    const accentColor = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#c85a00";
    const map = L.map("leaflet-map", { zoomControl: true, attributionControl: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(map);
    const poly = L.polyline(coords, { color: accentColor, weight: 3, opacity: 0.85 });
    poly.addTo(map);

    // ① invalidateSize 먼저: 컨테이너 실제 크기를 Leaflet에 알림
    // ② fitBounds 이후: 정확한 크기 기반으로 경로가 화면에 맞게 표시됨
    map.invalidateSize();
    map.fitBounds(poly.getBounds(), { padding: [16, 16] });
    addKmMarkers(map, coords, splits || []);
    _activeMap = map;
  }, 80);
}

function haversineDist(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const φ1 = lat1 * Math.PI / 180, φ2 = lat2 * Math.PI / 180;
  const Δφ = (lat2 - lat1) * Math.PI / 180;
  const Δλ = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(Δφ/2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ/2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function addKmMarkers(map, coords, splits) {
  let cumDist = 0, nextKm = 1000;
  for (let i = 1; i < coords.length; i++) {
    const segDist = haversineDist(coords[i-1][0], coords[i-1][1], coords[i][0], coords[i][1]);
    cumDist += segDist;
    while (cumDist >= nextKm) {
      const t = segDist > 0 ? (cumDist - nextKm) / segDist : 0;
      const lat = coords[i][0] + (coords[i-1][0] - coords[i][0]) * t;
      const lon = coords[i][1] + (coords[i-1][1] - coords[i][1]) * t;
      const km = Math.round(nextKm / 1000);
      const split = splits[km - 1];
      const paceStr = (split && split.duration && split.distance) ? fmtPace(split.duration, split.distance) : null;
      const icon = L.divIcon({
        className: 'km-marker',
        html: `<div class="km-marker-inner">${km}</div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      L.marker([lat, lon], { icon })
        .bindTooltip(paceStr ? `${km} km · ${paceStr} /km` : `${km} km`,
          { className: 'km-tooltip', direction: 'top', offset: [0, -14] })
        .addTo(map);
      nextKm += 1000;
    }
  }
}

function renderSplits(splits) {
  if (!splits.length) {
    return '<div class="detail-note">구간 데이터 없음</div>';
  }
  const rows = splits.map((lap, i) => {
    const ld = lap.distance  ? (lap.distance / 1000).toFixed(2) + " km" : "—";
    const lt = lap.duration  ? fmtDuration(lap.duration) : "—";
    const lp = (lap.duration && lap.distance) ? fmtPace(lap.duration, lap.distance) + " /km" : "—";
    const lh = lap.averageHR ? Math.round(lap.averageHR) : "—";
    const lc = lap.averageRunCadence ? Math.round(lap.averageRunCadence) : "—";
    return `<tr><td>${i + 1}</td><td>${ld}</td><td>${lt}</td><td>${lp}</td><td>${lh}</td><td>${lc}</td></tr>`;
  }).join("");
  return `
    <div class="splits-title">구간 기록</div>
    <table class="splits-table">
      <thead><tr><th>구간</th><th>거리</th><th>시간</th><th>페이스</th><th>심박</th><th>케이던스</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Sync ──────────────────────────────────────────────────────────────────────

async function syncSelected() {
  const checked = [...document.querySelectorAll(".act-cb:checked")].map(el => el.dataset.id);
  if (!checked.length)   { showToast("업로드할 활동을 선택해주세요.", "info"); return; }
  if (!stravaConnected)  { showToast("먼저 Strava를 연결해주세요.", "error"); return; }

  const total = checked.length;
  let done = 0, okCount = 0, errCount = 0;
  document.getElementById("sync-btn").disabled = true;
  setProgress(0, total);
  checked.forEach(id => { const a = activities.find(x => x.garmin_id === id); if (a) { a._syncing = true; a._note = null; } });
  renderTable();

  for (const id of checked) {
    let r;
    try {
      const res = await apiFetch("/sync", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({activity_ids: [id]}) });
      r = (await res.json()).results[0];
    } catch(e) { r = {garmin_id: id, ok: false, error: e.message}; }

    const a = activities.find(x => x.garmin_id === id);
    if (a) {
      a._syncing = false;
      if (r.ok) {
        a.synced = true; a._error = null; okCount++;
        if (r.already_synced) a._note = {type:"info", text:"이미 Strava에 있음"};
        else if (r.duplicate) a._note = {type:"warn", text:"Strava 기존 활동과 중복", url:`https://www.strava.com/activities/${r.strava_activity_id}`};
        else                   a._note = {type:"ok",   text:"업로드 완료",             url:`https://www.strava.com/activities/${r.strava_activity_id}`};
      } else {
        a._error = r.error; a._note = {type:"err", text:r.error}; errCount++;
      }
    }
    done++;
    setProgress(done, total);
    renderTable();
  }

  hideProgress();
  document.getElementById("sync-btn").disabled = false;
  if (!errCount)    showToast(`${okCount}개 처리 완료!`, "success");
  else if (okCount) showToast(`${okCount}개 성공, ${errCount}개 실패`, "info");
  else              showToast(`${errCount}개 실패 — 비고 확인`, "error");
}

function setProgress(done, total) {
  const pct = total ? Math.round(done / total * 100) : 0;
  document.getElementById("progress-wrap").style.display = "";
  document.getElementById("progress-bar").style.width = pct + "%";
  document.getElementById("progress-text").textContent = `${done} / ${total} 처리중`;
}
function hideProgress() { document.getElementById("progress-wrap").style.display = "none"; }

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildNoteCell(a) {
  if (a._note) {
    const n      = a._note;
    const colors = {ok:"#16a34a", warn:"#d97706", err:"#dc2626", info:"#2563eb"};
    const color  = colors[n.type] || "#6b7280";
    const text   = escHtml(n.text);
    return n.url
      ? `<a href="${n.url}" target="_blank" rel="noopener noreferrer" style="color:${color};font-size:0.8rem;text-decoration:none;" title="${text}">↗ ${text}</a>`
      : `<span style="color:${color};font-size:0.8rem;">${text}</span>`;
  }
  if (a.synced && a.strava_activity_id) {
    return `<a href="https://www.strava.com/activities/${a.strava_activity_id}" target="_blank" rel="noopener noreferrer" style="color:#16a34a;font-size:0.8rem;text-decoration:none;">↗ Strava</a>`;
  }
  return "";
}

function fmtDuration(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h > 0 ? `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}` : `${m}:${String(sec).padStart(2,"0")}`;
}

function fmtPace(dur_s, dist_m) {
  if (!dist_m) return "—";
  const ps = dur_s / (dist_m / 1000);
  return `${Math.floor(ps / 60)}:${String(Math.floor(ps % 60)).padStart(2,"0")}`;
}

function setModalLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  btn.disabled = loading;
  btn.innerHTML = loading ? '<span class="spinner"></span>처리중...' : (btnId === "login-btn" ? "로그인" : "확인");
}

document.addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  if (document.getElementById("modal-mfa-form").style.display !== "none")       submitMfa();
  else if (document.getElementById("modal-login-form").style.display !== "none") submitGarminLogin();
});
