let users = [];
let editTargetId = null;

(async function init() {
  await loadUsers();
})();

async function loadUsers() {
  document.getElementById("users-wrap").innerHTML = '<div id="loading">불러오는 중...</div>';
  try {
    const res = await fetch("/admin/api/users");
    if (res.status === 401 || res.status === 403) { window.location.href = "/"; return; }
    users = await res.json();
    renderUsers();
  } catch(e) {
    document.getElementById("users-wrap").innerHTML = `<div id="loading" style="color:#dc2626">오류: ${escHtml(e.message)}</div>`;
  }
}

function renderUsers() {
  if (!users.length) {
    document.getElementById("users-wrap").innerHTML = '<div id="loading">사용자가 없습니다.</div>';
    return;
  }

  const rows = users.map(u => {
    const garmin = u.garmin_connected ? '<span class="status-on">● Garmin</span>' : '<span class="status-off">● Garmin</span>';
    const strava = u.strava_connected ? '<span class="status-on">● Strava</span>'  : '<span class="status-off">● Strava</span>';
    const athleteCell = u.strava_athlete_id
      ? `<a href="https://www.strava.com/athletes/${u.strava_athlete_id}" target="_blank" style="color:#fc4c02;font-size:0.82rem;">${u.strava_athlete_id}</a>`
      : '<span style="color:#d1d5db;">—</span>';
    const created = u.created_at ? u.created_at.slice(0, 10) : '';
    return `
      <tr class="user-row">
        <td>
          <div style="font-weight:600;">${escHtml(u.username)}</div>
          ${u.is_admin ? '<span class="badge-admin">관리자</span>' : ''}
        </td>
        <td>${garmin}&nbsp;&nbsp;${strava}</td>
        <td>${athleteCell}</td>
        <td style="text-align:center;font-weight:600;">${u.synced_count}</td>
        <td style="color:#9ca3af;font-size:0.82rem;">${escHtml(created)}</td>
        <td style="white-space:nowrap;">
          <button class="btn btn-secondary btn-sm" onclick="toggleActivities(${u.id})" id="acts-btn-${u.id}">활동 기록</button>
          <button class="btn btn-secondary btn-sm" onclick="openEditModal(${u.id})" style="margin-left:4px;">편집</button>
          <button class="btn btn-danger btn-sm"    onclick="deleteUser(${u.id})"    style="margin-left:4px;">삭제</button>
        </td>
      </tr>
      <tr class="acts-row" id="acts-row-${u.id}" style="display:none;">
        <td colspan="6">
          <div class="acts-panel" id="acts-panel-${u.id}"></div>
        </td>
      </tr>`;
  }).join("");

  document.getElementById("users-wrap").innerHTML = `
    <table>
      <thead>
        <tr>
          <th>사용자</th><th>연결 상태</th><th>Strava 계정</th>
          <th style="text-align:center;">동기화</th><th>가입일</th><th>관리</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function toggleActivities(userId) {
  const row   = document.getElementById("acts-row-" + userId);
  const panel = document.getElementById("acts-panel-" + userId);

  if (row.style.display !== "none") { row.style.display = "none"; return; }

  row.style.display = "";
  panel.innerHTML = '<div style="color:#9ca3af;font-size:0.85rem;padding:8px 0;">불러오는 중...</div>';

  try {
    const res  = await fetch("/admin/api/users/" + userId + "/activities");
    const acts = await res.json();

    if (!acts.length) {
      panel.innerHTML = '<div style="color:#9ca3af;font-size:0.85rem;padding:4px 0;">동기화 기록 없음</div>';
      return;
    }

    const actRows = acts.map(a => {
      const stravaLink   = a.strava_id
        ? `<a href="https://www.strava.com/activities/${a.strava_id}" target="_blank" style="color:#fc4c02;">${a.strava_id}</a>`
        : '<span style="color:#d1d5db;">—</span>';
      const statusColor  = {uploaded:"#16a34a", error:"#dc2626", pending:"#d97706"}[a.status] || "#6b7280";
      const syncedAt     = a.synced_at ? a.synced_at.slice(0, 16).replace("T", " ") : "";
      return `<tr>
        <td style="font-family:monospace;">${escHtml(a.garmin_id)}</td>
        <td>${stravaLink}</td>
        <td><span style="color:${statusColor};font-weight:600;">${escHtml(a.status)}</span></td>
        <td style="color:#9ca3af;">${escHtml(syncedAt)}</td>
        <td><button class="btn btn-danger btn-sm" onclick="deleteActivity(${userId},'${escAttr(a.garmin_id)}')">삭제</button></td>
      </tr>`;
    }).join("");

    panel.innerHTML = `
      <table class="acts-inner-table">
        <thead><tr><th>Garmin ID</th><th>Strava ID</th><th>상태</th><th>동기화 시간</th><th></th></tr></thead>
        <tbody>${actRows}</tbody>
      </table>`;
  } catch(e) {
    panel.innerHTML = `<div style="color:#dc2626;font-size:0.85rem;">오류: ${escHtml(e.message)}</div>`;
  }
}

function openEditModal(userId) {
  const user = users.find(u => u.id === userId);
  if (!user) return;
  editTargetId = userId;
  document.getElementById("edit-username-label").textContent = "@" + user.username;
  document.getElementById("edit-pw").value    = "";
  document.getElementById("edit-admin").checked = user.is_admin;
  document.getElementById("edit-error").textContent = "";
  document.getElementById("edit-modal").showModal();
}

async function saveEdit() {
  const pw      = document.getElementById("edit-pw").value;
  const isAdmin = document.getElementById("edit-admin").checked;
  const errEl   = document.getElementById("edit-error");
  errEl.textContent = "";
  if (pw && pw.length < 6) { errEl.textContent = "비밀번호는 6자 이상이어야 합니다."; return; }

  const body = {is_admin: isAdmin};
  if (pw) body.password = pw;

  const btn = document.getElementById("edit-save-btn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>저장중...';
  try {
    const res  = await fetch("/admin/api/users/" + editTargetId, { method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
    const data = await res.json();
    if (data.ok) { document.getElementById("edit-modal").close(); showToast("저장되었습니다.", "success"); await loadUsers(); }
    else         { errEl.textContent = data.error || "저장 실패"; }
  } catch(e) { errEl.textContent = "오류: " + e.message; }
  finally    { btn.disabled = false; btn.textContent = "저장"; }
}

async function deleteUser(userId) {
  const user = users.find(u => u.id === userId);
  const username = user ? user.username : userId;
  if (!confirm(`'${username}' 계정을 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.`)) return;
  try {
    const res  = await fetch("/admin/api/users/" + userId, {method: "DELETE"});
    const data = await res.json();
    if (data.ok) { showToast("삭제되었습니다.", "success"); await loadUsers(); }
    else         { showToast(data.error || "삭제 실패", "error"); }
  } catch(e) { showToast("오류: " + e.message, "error"); }
}

async function deleteActivity(userId, garminId) {
  if (!confirm("이 동기화 기록을 삭제하시겠습니까?")) return;
  try {
    const res  = await fetch(`/admin/api/users/${userId}/activities/${encodeURIComponent(garminId)}`, {method: "DELETE"});
    const data = await res.json();
    if (data.ok) {
      showToast("기록이 삭제되었습니다.", "success");
      document.getElementById("acts-row-" + userId).style.display = "none";
      await toggleActivities(userId);
    } else { showToast(data.error || "삭제 실패", "error"); }
  } catch(e) { showToast("오류: " + e.message, "error"); }
}

function escAttr(str) {
  return String(str)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#x27;");
}
