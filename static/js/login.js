function switchTab(tab) {
  document.getElementById("tab-login").classList.toggle("active", tab === "login");
  document.getElementById("tab-register").classList.toggle("active", tab === "register");
  document.getElementById("form-login").style.display = tab === "login" ? "" : "none";
  document.getElementById("form-register").style.display = tab === "register" ? "" : "none";
}

async function doLogin() {
  const username = document.getElementById("login-user").value.trim();
  const password = document.getElementById("login-pass").value;
  const errEl = document.getElementById("login-error");
  const btn   = document.getElementById("login-btn");
  errEl.textContent = "";
  if (!username || !password) { errEl.textContent = "아이디와 비밀번호를 입력해주세요."; return; }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>로그인 중...';
  try {
    const res  = await fetch("/login", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({username, password}) });
    const data = await res.json();
    if (data.ok) { window.location.href = "/"; }
    else         { errEl.textContent = data.error || "로그인 실패"; }
  } catch(e) { errEl.textContent = "오류: " + e.message; }
  finally    { btn.disabled = false; btn.textContent = "로그인"; }
}

async function doRegister() {
  const username  = document.getElementById("reg-user").value.trim();
  const password  = document.getElementById("reg-pass").value;
  const password2 = document.getElementById("reg-pass2").value;
  const errEl = document.getElementById("reg-error");
  const btn   = document.getElementById("reg-btn");
  errEl.textContent = "";
  if (password !== password2) { errEl.textContent = "비밀번호가 일치하지 않습니다."; return; }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>처리 중...';
  try {
    const res  = await fetch("/register", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({username, password}) });
    const data = await res.json();
    if (data.ok) {
      const loginRes  = await fetch("/login", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({username, password}) });
      const loginData = await loginRes.json();
      if (loginData.ok) window.location.href = "/";
    } else {
      errEl.textContent = data.error || "회원가입 실패";
    }
  } catch(e) { errEl.textContent = "오류: " + e.message; }
  finally    { btn.disabled = false; btn.textContent = "회원가입"; }
}

document.addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  const isLogin = document.getElementById("form-login").style.display !== "none";
  if (isLogin) doLogin(); else doRegister();
});
