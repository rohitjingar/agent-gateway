"""The self-serve admin UI: one self-contained HTML page (no external assets).

Served at GET /admin/ui. It's just a screen over the admin API — it holds no logic
of its own; it reads and writes the DB-backed policy through the /admin/* endpoints.
"""

ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Gateway - Admin</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 0; padding: 24px; background:#f4f5f7; color:#1a1a1a; }
h1 { font-size: 22px; margin: 0 0 16px; }
h2 { font-size: 15px; margin: 0 0 10px; }
.bar { background:#fff; padding:12px; border-radius:10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; box-shadow:0 1px 3px rgba(0,0,0,.08); margin-bottom:16px; }
input { padding:6px 9px; border:1px solid #cbd0d8; border-radius:6px; font-size:14px; }
#token { width: 300px; }
.card { background:#fff; padding:16px 18px; border-radius:10px; box-shadow:0 1px 3px rgba(0,0,0,.08); margin-bottom:16px; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th, td { text-align:left; padding:7px 8px; border-bottom:1px solid #eef0f3; vertical-align: top; }
th { color:#6b7280; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
button { padding:5px 11px; border:1px solid #cbd0d8; background:#fafbfc; border-radius:6px; cursor:pointer; font-size:13px; }
button:hover { background:#eef1f5; }
.row { margin-top:12px; display:flex; gap:8px; flex-wrap:wrap; }
.role { margin:7px 0; display:flex; gap:8px; align-items:center; }
.role b { min-width:90px; }
.role input { flex:1; }
.riskItem { display:block; margin:5px 0; font-size:14px; }
.hint { color:#8b93a1; font-size:12px; }
code { background:#eef0f3; padding:1px 5px; border-radius:4px; }
#status { margin-left:auto; font-size:13px; font-weight:500; }
.approve { color:#0a7d33; border-color:#9dd6ac; }
.deny { color:#b3261e; border-color:#e6a6a1; }
</style>
</head>
<body>
<h1>&#128737; Agent Gateway - Admin</h1>

<div class="bar">
  <label>Gateway <input id="base"></label>
  <label>Admin token <input id="token" placeholder="paste an admin JWT"></label>
  <button onclick="genToken()">Generate dev admin token</button>
  <button onclick="loadAll()">Load / Refresh</button>
  <span id="status"></span>
</div>

<section class="card">
  <h2>Servers &mdash; the machines behind the gateway</h2>
  <table id="serversTable"></table>
  <div class="row">
    <input id="srvName" placeholder="name (e.g. slack)">
    <input id="srvUrl" placeholder="url (e.g. http://slack:9000/mcp)" style="width:340px">
    <button onclick="addServer()">Add server</button>
  </div>
</section>

<section class="card">
  <h2>Roles &amp; permissions &mdash; who may press which buttons</h2>
  <div id="rolesBox"></div>
  <div class="row">
    <input id="newRole" placeholder="new role (e.g. support)">
    <input id="newRolePatterns" placeholder="patterns (e.g. slack.*, db.run_query)" style="width:340px">
    <button onclick="addRole()">Add role</button>
  </div>
  <p class="hint">Patterns: <code>*</code> = everything &middot; <code>server.*</code> = a whole server &middot; <code>server.tool</code> = one button.</p>
</section>

<section class="card">
  <h2>High-risk tools &mdash; these require human approval</h2>
  <div id="riskBox"></div>
</section>

<section class="card">
  <h2>Approvals inbox</h2>
  <table id="approvalsTable"></table>
</section>

<section class="card">
  <h2>Recent activity (audit log)</h2>
  <table id="auditTable"></table>
</section>

<script>
const $ = (id) => document.getElementById(id);
$('base').value = window.location.origin;
function setStatus(msg, ok){ const s=$('status'); s.textContent=msg; s.style.color = (ok===false) ? '#b3261e' : '#0a7d33'; }
function esc(s){ return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function api(method, path, body){
  const headers = {'content-type':'application/json'};
  const t = $('token').value.trim();
  if(t) headers['Authorization'] = 'Bearer ' + t;
  const res = await fetch($('base').value + path, {method, headers, body: body ? JSON.stringify(body) : undefined});
  const text = await res.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch(e) { data = text; }
  if(!res.ok){ throw new Error((data && data.detail) ? data.detail : ('HTTP ' + res.status)); }
  return data;
}

async function genToken(){
  try { const d = await api('POST','/auth/token',{subject:'ui-admin', role:'admin'}); $('token').value = d.access_token; setStatus('got admin token'); loadAll(); }
  catch(e){ setStatus('token failed: ' + e.message, false); }
}

async function loadAll(){
  try {
    const cfg = await api('GET','/admin/config');
    renderServers(cfg); renderRoles(cfg); renderRisk(cfg);
    await loadApprovals(); await loadAudit();
    setStatus(cfg.editable ? 'loaded - editable (DB connected)' : 'loaded - READ ONLY (no database)');
  } catch(e){ setStatus('load failed: ' + e.message, false); }
}

function renderServers(cfg){
  const rows = cfg.servers.map(s => `<tr><td><b>${esc(s.name)}</b></td><td>${esc(s.url)}</td><td><button onclick="removeServer('${esc(s.name)}')">remove</button></td></tr>`).join('');
  $('serversTable').innerHTML = '<tr><th>name</th><th>url</th><th></th></tr>' + (rows || '<tr><td colspan=3 class=hint>no servers</td></tr>');
}
async function addServer(){
  try { await api('POST','/admin/servers',{name:$('srvName').value.trim(), url:$('srvUrl').value.trim()}); $('srvName').value=''; $('srvUrl').value=''; loadAll(); }
  catch(e){ setStatus('add server failed: ' + e.message, false); }
}
async function removeServer(name){
  try { await api('DELETE','/admin/servers/' + encodeURIComponent(name)); loadAll(); }
  catch(e){ setStatus('remove failed: ' + e.message, false); }
}

function renderRoles(cfg){
  const box = $('rolesBox'); box.innerHTML = '';
  Object.keys(cfg.roles).sort().forEach(role => {
    const patterns = cfg.roles[role];
    const div = document.createElement('div'); div.className = 'role';
    const admin = (role === 'admin');
    div.innerHTML = `<b>${esc(role)}</b>
      <input id="rp_${esc(role)}" value="${esc(patterns.join(', '))}" ${admin?'disabled':''}>
      ${admin ? '<span class=hint>protected</span>' : `<button onclick="saveRole('${esc(role)}')">save</button> <button onclick="deleteRole('${esc(role)}')">delete</button>`}`;
    box.appendChild(div);
  });
}
function parsePatterns(s){ return s.split(',').map(x => x.trim()).filter(Boolean); }
async function saveRole(role){
  try { await api('PUT','/admin/roles/' + encodeURIComponent(role), {patterns: parsePatterns($('rp_' + role).value)}); setStatus('saved ' + role); loadAll(); }
  catch(e){ setStatus('save role failed: ' + e.message, false); }
}
async function deleteRole(role){
  try { await api('DELETE','/admin/roles/' + encodeURIComponent(role)); loadAll(); }
  catch(e){ setStatus('delete role failed: ' + e.message, false); }
}
async function addRole(){
  const role = $('newRole').value.trim(); if(!role) return;
  try { await api('PUT','/admin/roles/' + encodeURIComponent(role), {patterns: parsePatterns($('newRolePatterns').value)}); $('newRole').value=''; $('newRolePatterns').value=''; loadAll(); }
  catch(e){ setStatus('add role failed: ' + e.message, false); }
}

function renderRisk(cfg){
  const box = $('riskBox'); box.innerHTML = '';
  const hr = new Set(cfg.high_risk);
  const tools = (cfg.all_tools && cfg.all_tools.length) ? cfg.all_tools : cfg.high_risk;
  if(!tools.length){ box.innerHTML = '<span class=hint>no tools discovered yet</span>'; return; }
  tools.forEach(t => {
    const label = document.createElement('label'); label.className = 'riskItem';
    label.innerHTML = `<input type=checkbox ${hr.has(t)?'checked':''} onchange="toggleRisk('${esc(t)}', this.checked)"> ${esc(t)}`;
    box.appendChild(label);
  });
}
async function toggleRisk(tool, high){
  try { await api('PUT','/admin/tools/' + encodeURIComponent(tool) + '/high-risk', {high}); setStatus((high?'marked ':'unmarked ') + tool); }
  catch(e){ setStatus('toggle failed: ' + e.message, false); loadAll(); }
}

async function loadApprovals(){
  const list = await api('GET','/approvals?status=pending');
  const rows = list.map(a => `<tr><td>${esc(a.id.slice(0,8))}</td><td>${esc(a.subject)}</td><td>${esc(a.tool)}</td><td>${esc(JSON.stringify(a.arguments))}</td>
    <td><button class=approve onclick="decide('${esc(a.id)}','approve')">approve</button> <button class=deny onclick="decide('${esc(a.id)}','deny')">deny</button></td></tr>`).join('');
  $('approvalsTable').innerHTML = '<tr><th>id</th><th>who</th><th>tool</th><th>args</th><th></th></tr>' + (rows || '<tr><td colspan=5 class=hint>no pending approvals</td></tr>');
}
async function decide(id, action){
  try { await api('POST','/approvals/' + id + '/' + action); setStatus(action + 'd ' + id.slice(0,8)); loadApprovals(); loadAudit(); }
  catch(e){ setStatus(action + ' failed: ' + e.message, false); }
}

async function loadAudit(){
  const list = await api('GET','/audit/recent?limit=10');
  const rows = list.map(r => `<tr><td>${esc(r.role)}</td><td>${esc(r.subject)}</td><td>${esc(r.tool)}</td><td>${esc(r.outcome)}</td><td>${esc(r.latency_ms)}ms</td></tr>`).join('');
  $('auditTable').innerHTML = '<tr><th>role</th><th>who</th><th>tool</th><th>outcome</th><th>latency</th></tr>' + (rows || '<tr><td colspan=5 class=hint>empty</td></tr>');
}
</script>
</body>
</html>
"""
