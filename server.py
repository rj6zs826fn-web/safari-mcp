#!/usr/bin/env python3
"""Safari-MCP: steuert das echte Safari (JXA + echte Klicks) und transkribiert Videos lokal.

Server (stdio, kein Netzwerk-Port):  python3 server.py
Schalter:                            python3 server.py on [Minuten] | off | status
Selbsttest:                          python3 server.py selftest
"""
import base64, glob, json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
FLAG = HERE / ".enabled_until"
MODEL = HERE / "models" / "ggml-large-v3-turbo-q5_0.bin"
BREW = "/opt/homebrew/bin"  # Claude startet MCP-Server ohne Login-PATH

# Sperrliste: hier aendern. Domains gelten inkl. Subdomains, Stichwoerter als Teil des Hostnamens.
BLOCK_DOMAINS = ["paypal.com", "paypal.de", "klarna.com", "dkb.de", "ing.de", "comdirect.de", "n26.com",
                 "traderepublic.com", "scalable.capital", "coinbase.com", "binance.com", "kraken.com",
                 "appleid.apple.com", "account.apple.com", "accounts.google.com", "myaccount.google.com",
                 "elster.de", "icloud.com"]
BLOCK_WORDS = ["bank", "sparkasse", "paypal", "broker", "depot"]


class ToolError(Exception):
    pass


# ---------- Schalter & Sperrliste ----------

def write_flag(text):
    FLAG.write_text(text)
    os.chmod(FLAG, 0o600)


def enabled_until():
    try:
        return float(FLAG.read_text())
    except (OSError, ValueError):
        return 0.0


def gate():
    left = enabled_until() - time.time()
    if left <= 0:
        raise ToolError("Safari-Zugriff ist AUS. Der Nutzer muss ihn selbst einschalten: "
                        "python3 ~/mcp/safari/server.py on 30")


def blocked(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in BLOCK_DOMAINS) or any(w in host for w in BLOCK_WORDS)


def check_url(url):
    if blocked(url):
        raise ToolError(f"Gesperrte Seite ({urlparse(url).hostname}). Das muss der Nutzer selbst machen.")


# ---------- Safari via JXA ----------

def run(cmd, timeout=60):
    env = dict(os.environ, PATH=f"{BREW}:{os.environ.get('PATH', '/usr/bin:/bin')}")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if p.returncode:
        raise ToolError(f"{Path(cmd[0]).name} fehlgeschlagen: {(p.stderr or p.stdout).strip()[-600:]}")
    return p.stdout


def jxa(body):
    out = run(["osascript", "-l", "JavaScript", "-e",
               "function run(){var s=Application('Safari');if(!s.running())throw new Error('Safari laeuft nicht');"
               "if(!s.windows.length)throw new Error('Kein Safari-Fenster offen');" + body + "}"], timeout=30)
    return out.strip()


def current_url():
    return jxa("return s.windows[0].currentTab().url()||'';")


def page_js(code, guard=True):
    """Fuehrt einen Funktionsrumpf (mit return) im aktuellen Tab aus und liefert das JSON-Ergebnis."""
    if guard:
        check_url(current_url())
    wrapped = ("(()=>{try{return JSON.stringify((()=>{" + code + "})()??null)}"
               "catch(e){return JSON.stringify({__err:String(e)})}})()")
    try:
        raw = jxa("return s.doJavaScript(" + json.dumps(wrapped) + ",{in:s.windows[0].currentTab()});")
    except ToolError as e:
        if "Apple Events" in str(e) or "Apple-Events" in str(e):
            raise ToolError("In Safari ist 'JavaScript von Apple Events erlauben' aus "
                            "(Einstellungen > Erweitert > Webentwickler-Funktionen, dann Menue Entwickler). "
                            "Der Nutzer muss das selbst einschalten.")
        raise
    res = json.loads(raw) if raw else None
    if isinstance(res, dict) and "__err" in res:
        raise ToolError("Seiten-JS: " + res["__err"])
    return res


SNAPSHOT_JS = r"""
const MAX=%d, ALL=%s;
const sel='a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=switch],[role=option],[onclick],[contenteditable=""],[contenteditable=true]';
document.querySelectorAll('[data-mcp-id]').forEach(e=>e.removeAttribute('data-mcp-id'));
const items=[]; let n=0, hidden=0;
// shortcut: Shadow-DOM und iframes werden nicht durchsucht, erweitern falls eine Seite das braucht
for (const el of document.querySelectorAll(sel)) {
  const r=el.getBoundingClientRect(); if(r.width<1||r.height<1) continue;
  const cs=getComputedStyle(el); if(cs.visibility==='hidden'||cs.display==='none'||cs.opacity==='0') continue;
  if(!ALL && (r.bottom<0||r.top>innerHeight)){hidden++; continue;}
  if(n>=MAX){hidden++; continue;}
  el.setAttribute('data-mcp-id',++n);
  const tag=el.tagName.toLowerCase(), role=el.getAttribute('role');
  let label=(el.getAttribute('aria-label')||el.innerText||el.value||el.placeholder||el.title||el.alt||el.name||'').trim().replace(/\s+/g,' ');
  if(label.length>70) label=label.slice(0,70)+'…';
  let x='';
  if(tag==='input') x+=':'+(el.type||'text');
  if(el.checked) x+=' [x]';
  if(el.disabled) x+=' (disabled)';
  if(tag==='select') x+=' ='+(el.selectedOptions[0]?.text||'').slice(0,30);
  if(tag==='a'){const h=el.getAttribute('href')||''; if(!h.startsWith('#')&&!h.startsWith('javascript')) x+=' → '+h.replace(location.origin,'').slice(0,60);}
  items.push(`[${n}] ${role||tag}${x} "${label}"`);
}
return {title:document.title, url:location.href, scroll:`${Math.round(scrollY)}/${document.documentElement.scrollHeight} (Fenster ${innerHeight})`, items, weitere_ausserhalb:hidden};
"""


def snapshot(max_items=120, all_page=False):
    s = page_js(SNAPSHOT_JS % (int(max_items), "true" if all_page else "false"))
    return f"{s['title']}\n{s['url']}\nScroll {s['scroll']}\n" + "\n".join(s["items"]) + \
        (f"\n(+{s['weitere_ausserhalb']} weitere ausserhalb/ueber Limit)" if s["weitere_ausserhalb"] else "")


def find_el(i):
    return f"const el=document.querySelector('[data-mcp-id=\"{int(i)}\"]');" \
           "if(!el) throw new Error('Element nicht gefunden, neuen Snapshot machen');"


def wait_loaded(timeout=10):
    end = time.time() + timeout
    time.sleep(0.3)
    while time.time() < end:
        try:
            if page_js("return document.readyState", guard=False) == "complete":
                return
        except ToolError:
            pass
        time.sleep(0.3)


def native_click(x, y):
    # Echte Maus-Ereignisse (isTrusted) – braucht Bedienungshilfen-Recht fuer die Claude-App.
    # Sicherheit: x/y kommen aus der Webseite – nur echte Zahlen in das Skript lassen, sonst Code-Einschleusung
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        raise ToolError("Ungültige Klickposition von der Seite")
    if not (-10000 < x < 20000 and -10000 < y < 20000):
        raise ToolError("Klickposition außerhalb des Bildschirms")
    run(["osascript", "-l", "JavaScript", "-e",
         "ObjC.import('CoreGraphics');Application('Safari').activate();delay(0.25);"
         f"var p={{x:{x},y:{y}}};"
         "[5,1,2].forEach(function(t){$.CGEventPost(0,$.CGEventCreateMouseEvent(null,t,p,0));delay(0.05);});"])


def after(args, msg):
    return snapshot() if args.get("then_snapshot") else msg


# ---------- Werkzeuge ----------

def t_tabs(a):
    gate()
    if a.get("select"):
        w, t = (int(x) for x in a["select"].split(":"))
        jxa(f"var w=s.windows[{w - 1}];w.currentTab=w.tabs[{t - 1}];w.index=1;return '';")
    out = jxa("var r=[];s.windows().forEach(function(w,i){w.tabs().forEach(function(t,j){"
              "r.push((i+1)+':'+(j+1)+(w.currentTab().index()===t.index()?' *':'  ')+' '+t.name().slice(0,60)+' | '+t.url());});});"
              "return r.join('\\n');")
    return out


def t_open(a):
    gate()
    url = a["url"]
    if not re.match(r"https?://", url):
        url = "https://" + url
    check_url(url)
    if a.get("new_tab"):
        jxa(f"var w=s.windows[0];var t=s.Tab({{url:{json.dumps(url)}}});w.tabs.push(t);w.currentTab=t;return '';")
    else:
        jxa(f"s.windows[0].currentTab().url={json.dumps(url)};return '';")
    wait_loaded()
    check_url(current_url())  # Weiterleitung auf gesperrte Seite
    return after(a, "geoeffnet: " + current_url())


def t_snapshot(a):
    gate()
    return snapshot(a.get("max_items", 120), a.get("all", False))


def t_text(a):
    gate()
    sel = json.dumps(a.get("selector") or "body")
    txt = page_js(f"const e=document.querySelector({sel});if(!e)throw new Error('Selektor nicht gefunden');"
                  "return e.innerText.replace(/\\n\\s*\\n+/g,'\\n');")
    mx = int(a.get("max_chars", 4000))
    return txt[:mx] + (f"\n…(gekuerzt, {len(txt)} Zeichen gesamt)" if len(txt) > mx else "")


def t_click(a):
    gate()
    if not a.get("native"):
        page_js(find_el(a["id"]) + "el.scrollIntoView({block:'center'});el.focus?.();el.click();return 1;")
    else:
        # shortcut: setzt Seitenzoom 100 % und Safari auf dem Hauptbildschirm voraus
        x, y = page_js(find_el(a["id"]) + "el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();"
                       "return [Math.round(screenX+r.left+r.width/2), Math.round(screenY+outerHeight-innerHeight+r.top+r.height/2)];")
        native_click(x, y)
    time.sleep(0.4)
    wait_loaded(5)
    return after(a, "geklickt. " + current_url())


def t_type(a):
    gate()
    js = find_el(a["id"]) + """
if(el.type==='password') throw new Error('Passwortfelder fuellt der Nutzer selbst aus');
el.focus();
if(el.isContentEditable){document.execCommand('selectAll');document.execCommand('insertText',false,TEXT);}
else{const P=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:el instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype;
 Object.getOwnPropertyDescriptor(P,'value').set.call(el,TEXT);
 el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}
if(SUBMIT){ if(el.form) el.form.requestSubmit(); else ['keydown','keypress','keyup'].forEach(t=>el.dispatchEvent(new KeyboardEvent(t,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true})));}
return 1;"""
    page_js(js.replace("TEXT", json.dumps(a["text"])).replace("SUBMIT", "true" if a.get("submit") else "false"))
    if a.get("submit"):
        wait_loaded(5)
    return after(a, "eingetragen.")


def t_eval(a):
    gate()
    return json.dumps(page_js(a["code"]), ensure_ascii=False)[:int(a.get("max_chars", 4000))]


def t_screenshot(a):
    gate()
    check_url(current_url())
    # screencapture -l scheitert, wenn das Fenster auf einem anderen Space liegt -> vorher nach vorne holen
    wid = str(int(jxa("s.activate();delay(0.6);return s.windows[0].id();")))
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.jpg")
        run(["screencapture", "-x", "-o", "-t", "jpg", "-l", wid, f])
        run(["sips", "-Z", str(int(a.get("max_px", 1024))), "-s", "formatOptions", "60", f])
        return [{"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(Path(f).read_bytes()).decode()}]


def compact_whisper(out):
    lines = []
    for m in re.finditer(r"\[(\d+):(\d+):(\d+)\.\d+ --> [^\]]+\]\s*(.*)", out):
        h, mi, s, txt = int(m[1]), int(m[2]), int(m[3]), m[4].strip()
        if txt:
            lines.append(f"[{h * 60 + mi}:{s:02d}] {txt}")
    return "\n".join(lines)


def pick_evenly(items, k):
    if len(items) <= k:
        return items
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)] if k > 1 else items[:1]


def t_media(a):
    src, mode = a["source"], a.get("mode", "audio")
    if mode not in ("audio", "frames", "both"):
        raise ToolError("mode muss audio, frames oder both sein")
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="safari-mcp-")
    try:
        if src == "current_tab":
            gate()
            src = current_url()
            check_url(src)
        if re.match(r"https?://", src):
            if not shutil.which("yt-dlp", path=BREW + ":" + os.environ.get("PATH", "")):
                raise ToolError("yt-dlp fehlt fuer Links (brew install yt-dlp). Lokale Dateien gehen ohne.")
            fmt = "ba/b" if mode == "audio" else "b[height<=720]/bv*[height<=720]+ba/b"
            run(["yt-dlp", "-q", "--no-playlist", "-f", fmt, "-o", f"{tmp}/in.%(ext)s", src], timeout=300)
            files = glob.glob(f"{tmp}/in.*")
            if not files:
                raise ToolError("Download lieferte keine Datei")
            f = files[0]
        else:
            f = os.path.expanduser(src)
            if not os.path.isfile(f):
                raise ToolError("Datei nicht gefunden: " + f)
        dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).strip() or 0)
        t_dl = time.time() - t0
        content, info = [], [f"Dauer {dur:.0f}s"]

        if mode in ("audio", "both"):
            t1 = time.time()
            wav = f"{tmp}/a.wav"
            run(["ffmpeg", "-v", "error", "-y", "-i", f, "-vn", "-ac", "1", "-ar", "16000", wav], timeout=300)
            out = run(["whisper-cli", "-m", str(MODEL), "-f", wav, "-l", a.get("lang", "auto"), "-np", "-t", "8"], timeout=900)
            text = compact_whisper(out) or "(kein Sprachinhalt erkannt)"
            cap = int(a.get("max_chars", 20000))
            if len(text) > cap:  # MCP-Antworten ueber ~25k Tokens lehnt der Client ab
                fd, full = tempfile.mkstemp(prefix="transkript-", suffix=".txt")
                os.write(fd, text.encode()); os.close(fd)
                text = text[:cap] + f"\n… gekuerzt ({len(text)} Zeichen). Volltext: {full}"
            content.append({"type": "text", "text": "TRANSKRIPT\n" + text})
            info.append(f"Whisper {time.time() - t1:.1f}s")

        if mode in ("frames", "both"):
            t1 = time.time()
            maxf = int(a.get("max_frames", 8))
            every = max(float(a.get("every_s", 5)), dur / maxf if dur else 0)
            run(["ffmpeg", "-v", "error", "-y", "-i", f, "-vf", f"fps=1/{every},scale=512:-2", "-q:v", "6",
                 f"{tmp}/f_%04d.jpg"], timeout=300)
            frames = sorted(glob.glob(f"{tmp}/f_*.jpg"))
            for p in pick_evenly(frames, maxf):
                sec = (int(Path(p).stem[2:]) - 1) * every
                content.append({"type": "text", "text": f"Bild bei {int(sec // 60)}:{int(sec % 60):02d}"})
                content.append({"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(Path(p).read_bytes()).decode()})
            info.append(f"{min(len(frames), maxf)} Bilder in {time.time() - t1:.1f}s")

        info.insert(1, f"Laden {t_dl:.1f}s")
        content.insert(0, {"type": "text", "text": " | ".join(info) + f" | gesamt {time.time() - t0:.1f}s"})
        return content
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # Videos werden nicht aufbewahrt


S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}
SNAP = {"then_snapshot": {"type": "boolean", "description": "danach direkt Snapshot liefern (spart einen Aufruf)"}}
TOOLS = {
    "safari_tabs": (t_tabs, "Listet Safari-Tabs (Fenster:Tab, * = aktiv). select='1:3' wechselt dorthin.",
                    S({"select": {"type": "string"}})),
    "safari_open": (t_open, "Oeffnet URL im aktiven Tab (oder new_tab) und wartet aufs Laden.",
                    S({"url": {"type": "string"}, "new_tab": {"type": "boolean"}, **SNAP}, ["url"])),
    "safari_snapshot": (t_snapshot, "Kompakte Liste der sichtbaren bedienbaren Elemente mit Nummern. Billiger als Screenshot – zuerst das nutzen. all=true: ganze Seite.",
                        S({"max_items": {"type": "integer"}, "all": {"type": "boolean"}})),
    "safari_text": (t_text, "Sichtbarer Text der Seite oder eines CSS-Selektors, gekuerzt.",
                    S({"selector": {"type": "string"}, "max_chars": {"type": "integer"}})),
    "safari_click": (t_click, "Klickt Element-Nummer aus dem Snapshot per JS. native=true: echter Mausklick (wenn JS-Klick nicht wirkt, z.B. Video-Play).",
                     S({"id": {"type": "integer"}, "native": {"type": "boolean"}, **SNAP}, ["id"])),
    "safari_type": (t_type, "Setzt Text in Feld (Nummer aus Snapshot). submit=true schickt das Formular ab. Keine Passwortfelder.",
                    S({"id": {"type": "integer"}, "text": {"type": "string"}, "submit": {"type": "boolean"}, **SNAP}, ["id", "text"])),
    "safari_eval": (t_eval, "Fuehrt JS-Funktionsrumpf im Tab aus, Ergebnis per return (z.B. scrollen: 'scrollBy(0,800);return scrollY').",
                    S({"code": {"type": "string"}, "max_chars": {"type": "integer"}}, ["code"])),
    "safari_screenshot": (t_screenshot, "Screenshot des Safari-Fensters (teuer, nur wenn Layout/Bild wichtig).",
                          S({"max_px": {"type": "integer", "description": "laengste Kante, Standard 1024"}})),
    "media_transcribe": (t_media, "Video/Audio lokal auswerten. source: URL, Dateipfad oder 'current_tab'. mode: audio (nur Transkript, Standard), frames (nur Bilder), both.",
                         S({"source": {"type": "string"}, "mode": {"type": "string", "enum": ["audio", "frames", "both"]},
                            "every_s": {"type": "number", "description": "Bildabstand in s, Standard 5"},
                            "max_frames": {"type": "integer", "description": "Standard 8"},
                            "lang": {"type": "string", "description": "de, en … oder auto"},
                            "max_chars": {"type": "integer", "description": "Transkript kuerzen, Standard 20000; Volltext als Datei"}}, ["source"])),
}


# ---------- MCP über stdio ----------

def handle(msg):
    m, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if m == "initialize":
        r = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
             "capabilities": {"tools": {}}, "serverInfo": {"name": "safari", "version": "1.0"}}
    elif m == "ping":
        r = {}
    elif m == "tools/list":
        r = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in TOOLS.items()]}
    elif m == "tools/call":
        p = msg.get("params", {})
        fn = TOOLS.get(p.get("name"), (None,))[0]
        try:
            if not fn:
                raise ToolError("unbekanntes Werkzeug")
            out = fn(p.get("arguments") or {})
            r = {"content": out if isinstance(out, list) else [{"type": "text", "text": out}]}
        except (ToolError, subprocess.TimeoutExpired, KeyError, ValueError) as e:
            r = {"content": [{"type": "text", "text": f"Fehler: {e}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unbekannt: {m}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": r}


def serve():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            resp = handle(json.loads(line))
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if resp:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def selftest():
    assert blocked("https://www.sparkasse-hannover.de/x") and blocked("https://paypal.com")
    assert blocked("https://login.ing.de") and not blocked("https://spring.de") and not blocked("https://tiktok.com")
    assert compact_whisper("[00:01:05.000 --> 00:01:07.000]  Hallo Welt\n") == "[1:05] Hallo Welt"
    assert pick_evenly(list(range(10)), 3) == [0, 4, 9] and pick_evenly([1, 2], 5) == [1, 2]
    write_flag("0")
    try:
        gate(); raise AssertionError("gate offen")
    except ToolError:
        pass
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "safari_tabs", "arguments": {}}})
    assert r["result"]["isError"] and "AUS" in r["result"]["content"][0]["text"]
    print("selftest ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "on":
        mins = float(sys.argv[2]) if len(sys.argv) > 2 else 30
        write_flag(str(time.time() + mins * 60))
        print(f"Safari-Zugriff AN fuer {mins:g} Min (bis {time.strftime('%H:%M', time.localtime(enabled_until()))})")
    elif cmd == "off":
        write_flag("0")
        print("Safari-Zugriff AUS")
    elif cmd == "status":
        left = enabled_until() - time.time()
        print(f"AN, noch {left / 60:.0f} Min" if left > 0 else "AUS")
    elif cmd == "selftest":
        selftest()
    else:
        serve()
