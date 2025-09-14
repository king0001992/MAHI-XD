# app.py
from flask import Flask, request, render_template_string, redirect, session, url_for, send_file
import requests, uuid, io, threading, time, re
from datetime import datetime

app = Flask(__name__)
app.secret_key = "SUPER_SECRET_KEY_CHANGE_ME"

# ---------- CONFIG ----------
USERNAME = "DARKQUEEN"
PASSWORD = "DARKQUEEN"

LOGIN_BG = "https://i.imgur.com/YOUR_GIRL_IMAGE.jpg"
PAGE_BG = "https://i.imgur.com/YOUR_PAGE_IMAGE.jpg"

# ---------- STORAGE ----------
drafts = {}
jobs = {}  # job_id -> {"stop": False, "logs": [], "thread": thread}

# ---------- HELPERS ----------
def extract_group_id_from_url(url):
    if not url: return None
    m = re.search(r'facebook\.com\/groups\/([0-9]+)', url)
    if m: return m.group(1)
    m2 = re.search(r'facebook\.com\/groups\/([^\/\?]+)', url)
    if m2: return m2.group(1)
    m3 = re.search(r'([0-9]{5,})', url)
    if m3: return m3.group(1)
    return None

def check_facebook_token(token):
    try:
        resp = requests.get("https://graph.facebook.com/me", params={"access_token": token}, timeout=10)
        if resp.status_code == 200:
            return {"valid": True, "info": resp.json()}
        else:
            return {"valid": False, "info": resp.json() if resp.text else resp.text}
    except Exception as e:
        return {"valid": False, "info": str(e)}

def message_worker(job_id, tokens, group_id, prefix, interval, messages):
    job = jobs[job_id]
    while not job["stop"]:
        for token in tokens:
            for msg in messages:
                if job["stop"]:
                    job["logs"].append("🛑 Job stopped by user")
                    return
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                text = f"{prefix} {msg} | ⏰ {now}" if prefix else f"{msg} | ⏰ {now}"
                try:
                    r = requests.post(
                        f"https://graph.facebook.com/v15.0/t_{group_id}/",
                        data={"access_token": token.strip(), "message": text}
                    )
                    if r.status_code == 200:
                        job["logs"].append(f"[✅ SENT] {text}")
                    else:
                        job["logs"].append(f"[❌ FAIL] {text} | {r.text}")
                except Exception as e:
                    job["logs"].append(f"⚠️ ERROR: {e}")
                time.sleep(interval)

# ---------- ROUTES ----------
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == USERNAME and request.form['password'] == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        return render_template_string(LOGIN_PAGE, error="❌ Invalid Login", login_bg=LOGIN_BG)
    return render_template_string(LOGIN_PAGE, error=None, login_bg=LOGIN_BG)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template_string(DASHBOARD_PAGE, page_bg=PAGE_BG)

# ---------- Convo Editor ----------
@app.route('/convo', methods=['GET','POST'])
def convo():
    if not session.get('logged_in'): return redirect(url_for('login'))
    preview = None
    name = None
    if request.method == 'POST':
        name = request.form.get('title') or f"draft-{uuid.uuid4().hex[:6]}"
        text = request.files['txtFile'].read().decode(errors='ignore') if 'txtFile' in request.files and request.files['txtFile'].filename else request.form.get('text') or ""
        drafts[name] = {"text": text, "created": datetime.utcnow().isoformat()}
        preview = text.replace("\n", "<br>")
    return render_template_string(CONVO_PAGE, preview=preview, name=name)

@app.route('/download/draft/<name>')
def download_draft(name):
    if not session.get('logged_in'): return redirect(url_for('login'))
    d = drafts.get(name)
    if not d: return "Not found", 404
    buf = io.BytesIO()
    buf.write(d['text'].encode())
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{name}.txt", mimetype='text/plain')

# ---------- Post Payload Generator ----------
@app.route('/postgen', methods=['GET','POST'])
def postgen():
    if not session.get('logged_in'): return redirect(url_for('login'))
    payload = None
    if request.method == 'POST':
        message = request.form.get('message') or ""
        prefix = request.form.get('prefix') or ""
        final = f"{prefix} {message}".strip()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {"message": final, "preview": f"{final} | ⏰ {ts}"}
    return render_template_string(POSTGEN_PAGE, payload=payload)

# ---------- UID Extractor ----------
@app.route('/uid', methods=['GET','POST'])
def uid():
    if not session.get('logged_in'): return redirect(url_for('login'))
    result = None
    if request.method == 'POST':
        url = request.form.get('group_url') or ""
        gid = extract_group_id_from_url(url)
        result = {"input": url, "group_id": gid}
    return render_template_string(UID_PAGE, result=result)

# ---------- Token Checker ----------
@app.route('/token', methods=['GET','POST'])
def token():
    if not session.get('logged_in'): return redirect(url_for('login'))
    check = None
    if request.method == 'POST':
        token = request.form.get('token') or ""
        file = request.files.get('tokenFile')
        if file and file.filename:
            token = file.read().decode().splitlines()[0].strip()
        if token:
            check = check_facebook_token(token)
    return render_template_string(TOKEN_PAGE, check=check)

# ---------- Job Control ----------
@app.route('/jobs')
def job_list():
    if not session.get('logged_in'): return redirect(url_for('login'))
    html = "<h3>📊 Active Jobs</h3><ul>"
    for job_id, job in jobs.items():
        status = "🟢 Running" if not job["stop"] else "🔴 Stopped"
        html += f"<li><b>{job_id}</b> — {status} | <a href='/stop/{job_id}'>🛑 Stop</a><br><pre>{''.join(job['logs'][-5:])}</pre></li>"
    html += "</ul><a href='/'>⬅ Back</a>"
    return html

@app.route('/start', methods=['GET','POST'])
def start_job():
    if not session.get('logged_in'): return redirect(url_for('login'))
    if request.method == 'POST':
        tokens = request.form.get('tokens').splitlines()
        group_id = request.form.get('group_id')
        prefix = request.form.get('prefix')
        interval = int(request.form.get('interval'))
        messages = request.form.get('messages').splitlines()
        job_id = uuid.uuid4().hex[:6]
        jobs[job_id] = {"stop": False, "logs": []}
        t = threading.Thread(target=message_worker, args=(job_id, tokens, group_id, prefix, interval, messages))
        jobs[job_id]["thread"] = t
        t.start()
        return redirect(url_for('job_list'))
    return render_template_string(START_JOB_PAGE)

@app.route('/stop/<job_id>')
def stop_job(job_id):
    if job_id in jobs:
        jobs[job_id]["stop"] = True
        return redirect(url_for('job_list'))
    return "❌ Job not found"

# ---------- HTML Templates ----------
# (same templates as your first code + new START_JOB_PAGE with bootstrap form)

START_JOB_PAGE = """
<!doctype html><html><head>
<title>Start Job</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body class="p-4">
<a href="/" class="btn btn-sm btn-link">⬅ Back</a>
<div class="card p-3 shadow">
<h4>🚀 Start Messaging Job</h4>
<form method="post">
<textarea class="form-control mb-2" name="tokens" rows="3" placeholder="Paste access tokens (each on new line)" required></textarea>
<input class="form-control mb-2" name="group_id" placeholder="Group ID" required>
<input class="form-control mb-2" name="prefix" placeholder="Prefix (optional)">
<input class="form-control mb-2" name="interval" placeholder="Interval (seconds)" type="number" min="1" value="5">
<textarea class="form-control mb-2" name="messages" rows="4" placeholder="Messages (each on new line)" required></textarea>
<button class="btn btn-success w-100">Start</button>
</form>
</div></body></html>
"""

# ---------- Run ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
