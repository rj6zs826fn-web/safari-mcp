#!/usr/bin/env python3
"""Safari-MCP: steuert das echte Safari (JXA + echte Klicks) und transkribiert Videos lokal.

Server (stdio, kein Netzwerk-Port):  python3 server.py
Schalter:                            python3 server.py on [Minuten] | off | status
Selbsttest:                          python3 server.py selftest
"""
import base64, glob, json, os, re, select, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
FLAG = HERE / ".enabled_until"
WIN_FILE = HERE / ".claude_window"  # ID von Claudes eigenem Safari-Fenster
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


_helper = None


def osa(src, timeout=30):
    """Fuehrt JXA im dauerhaft laufenden Swift-Helfer aus (~ms statt ~150 ms pro osascript-Start).
    Fehlt Swift oder stirbt der Helfer, faellt es auf osascript zurueck."""
    global _helper
    try:
        if not (_helper and _helper.poll() is None):
            exe, src_file = HERE / "helper", HERE / "helper.swift"
            if not exe.exists() or exe.stat().st_mtime < src_file.stat().st_mtime:
                run(["xcrun", "swiftc", "-O", str(src_file), "-o", str(exe)], timeout=300)
            _helper = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        _helper.stdin.write(json.dumps({"s": src}) + "\n")
        _helper.stdin.flush()
        if not select.select([_helper.stdout], [], [], timeout)[0]:
            _helper.kill()
            raise ToolError("Safari antwortet nicht (Zeitlimit)")
        res = json.loads(_helper.stdout.readline())
    except (OSError, ValueError):  # Helfer nicht baubar/abgestuerzt -> alter Weg
        _helper = None
        return run(["osascript", "-l", "JavaScript", "-e", src], timeout=timeout).strip()
    if not res["ok"]:
        raise ToolError("Safari: " + res["e"])
    return res["r"]


def jxa(body):
    return osa("function run(){var s=Application('Safari');if(!s.running())throw new Error('Safari laeuft nicht');"
               + body + "}")


def win_id():
    try:
        return int(WIN_FILE.read_text())
    except (OSError, ValueError):
        return 0


USER_TAB = False  # pro Aufruf: True = im vordersten Fenster des Nutzers statt in Claudes Fenster arbeiten


def wj(body):
    """JXA mit w = Claudes eigenes Safari-Fenster (oder mit USER_TAB das vorderste Fenster des Nutzers). Fehlt es, wird es im Hintergrund neu angelegt.
    So bleiben deine Fenster unberuehrt und Safari muss nie nach vorne."""
    if USER_TAB:
        return jxa(f"var w=s.windows().filter(function(x){{return x.id()!={win_id()}}})[0];"
                   "if(!w)throw new Error('Kein eigenes Safari-Fenster des Nutzers offen');" + body)
    for retry in (False, True):
        try:
            return jxa(f"var w;try{{w=s.windows.byId({win_id()});w.id();}}catch(e){{throw new Error('NOWIN')}}" + body)
        except ToolError as e:
            if "NOWIN" not in str(e) or retry:
                raise
            # Neues Fenster = die ID, die vorher nicht da war (windows[0] ist nicht zuverlaessig das neue)
            wid = int(jxa("var old=s.windows.id();s.documents.push(s.Document());"
                          "for(var i=0;i<20;i++){delay(0.1);var n=s.windows.id().filter(function(x){return old.indexOf(x)<0});"
                          "if(n.length)return n[0];}throw new Error('Neues Fenster nicht gefunden');"))
            WIN_FILE.write_text(str(wid))


def current_url():
    return wj("return w.currentTab().url()||'';")


def page_js(code, guard=True):
    """Fuehrt einen Funktionsrumpf (mit return) im aktuellen Tab aus und liefert das JSON-Ergebnis."""
    if guard:
        check_url(current_url())
    wrapped = ("(()=>{try{return JSON.stringify((()=>{" + code + "})()??null)}"
               "catch(e){return JSON.stringify({__err:String(e)})}})()")
    try:
        raw = wj("return s.doJavaScript(" + json.dumps(wrapped) + ",{in:w.currentTab()});")
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
const MAX=%d, ALL=%s, Q=%s;
const sel='a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=switch],[role=option],[onclick],[contenteditable=""],[contenteditable=true]';
document.querySelectorAll('[data-mcp-id]').forEach(e=>e.removeAttribute('data-mcp-id'));
const items=[]; let n=0, hidden=0;
// shortcut: Shadow-DOM und iframes werden nicht durchsucht, erweitern falls eine Seite das braucht
for (const el of document.querySelectorAll(sel)) {
  const r=el.getBoundingClientRect(); if(r.width<1||r.height<1) continue;
  const cs=getComputedStyle(el); if(cs.visibility==='hidden'||cs.display==='none'||cs.opacity==='0') continue;
  if(!ALL && (r.bottom<0||r.top>innerHeight)){hidden++; continue;}
  const tag=el.tagName.toLowerCase(), role=el.getAttribute('role');
  let label=(el.getAttribute('aria-label')||el.innerText||el.value||el.placeholder||el.title||el.alt||el.name||'').trim().replace(/\s+/g,' ');
  if(label.length>70) label=label.slice(0,70)+'…';
  let x='';
  if(tag==='input') x+=':'+(el.type||'text');
  if(el.checked) x+=' [x]';
  if(el.disabled) x+=' (disabled)';
  if(tag==='select') x+=' ='+(el.selectedOptions[0]?.text||'').slice(0,30);
  if(tag==='a'){const h=el.getAttribute('href')||''; if(!h.startsWith('#')&&!h.startsWith('javascript')) x+=' → '+h.replace(location.origin,'').slice(0,60);}
  const line=`${role||tag}${x} "${label}"`;
  if(Q.length && !Q.some(q=>line.toLowerCase().includes(q))) continue;
  if(n>=MAX){hidden++; continue;}
  el.setAttribute('data-mcp-id',++n);
  items.push(`[${n}] ${line}`);
}
return {title:document.title, url:location.href, scroll:`${Math.round(scrollY)}/${document.documentElement.scrollHeight} (Fenster ${innerHeight})`, items, weitere_ausserhalb:hidden};
"""


def snapshot(max_items=120, all_page=False, query=None):
    """query: nur Elemente, deren Zeile einen der Begriffe enthaelt (mit | trennen); sucht dann die ganze Seite."""
    q = json.dumps([t.strip().lower() for t in query.split("|") if t.strip()] if query else [])
    s = page_js(SNAPSHOT_JS % (int(max_items), "true" if (all_page or query) else "false", q))
    return f"{s['title']}\n{s['url']}\nScroll {s['scroll']}\n" + "\n".join(s["items"]) + \
        (f"\n(+{s['weitere_ausserhalb']} weitere ausserhalb/ueber Limit)" if s["weitere_ausserhalb"] else "")


def resolve(a):
    """Element-Nummer aus a['id'] oder erstes passendes Element zu a['query'] (spart den Snapshot vorher)."""
    if a.get("id") is not None:
        return int(a["id"])
    if not a.get("query"):
        raise ToolError("id oder query angeben")
    first = snapshot(1, True, a["query"]).split("\n")[3:4]
    if not first or not first[0].startswith("["):
        raise ToolError(f"Kein Element passt zu '{a['query']}'")
    return int(first[0][1:first[0].index("]")])


def find_el(i):
    return f"const el=document.querySelector('[data-mcp-id=\"{int(i)}\"]');" \
           "if(!el) throw new Error('Element nicht gefunden, neuen Snapshot machen');"


MARK = "window.__mcpMark=1;"  # vor einer Aktion in die alte Seite setzen; fehlt er, ist die neue Seite da


def wait_loaded(timeout=10, settle=None):
    """Wartet, bis die neue Seite geladen ist. settle=None: Seitenwechsel wird erwartet.
    settle=s: kommt binnen s Sekunden kein Seitenwechsel, war es keiner (z. B. Klick auf Aufklapp-Menue)."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            old, state, href = page_js("return [!!window.__mcpMark,document.readyState,location.href]", guard=False)
            if old:
                if settle is not None and time.time() - start > settle:
                    return
            elif state == "complete" and href.startswith("http"):
                return
        except (ToolError, ValueError, TypeError):
            pass  # Seite wechselt gerade
        time.sleep(0.05)


# Oranger Claude-Cursor in der Seite: zeigt vor jeder Aktion, wohin geklickt/getippt wird.
CURSOR_JS = r"""
el.scrollIntoView({block:'center'});
let c=document.getElementById('__claude_cursor');
if(!c){c=document.createElement('div');c.id='__claude_cursor';
 c.innerHTML='<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2l6.5 19 2.6-7.4L20.5 11z" fill="#ff7a00" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
 Object.assign(c.style,{position:'fixed',left:'50%',top:'50%',zIndex:2147483647,pointerEvents:'none',
  transition:'left .35s ease,top .35s ease',filter:'drop-shadow(0 1px 2px rgba(0,0,0,.45))'});
 document.documentElement.appendChild(c);}
const r=el.getBoundingClientRect();
c.style.left=Math.round(r.left+r.width/2-4)+'px'; c.style.top=Math.round(r.top+r.height/2-2)+'px';
const o=el.style.outline; el.style.outline='3px solid #ff7a00'; setTimeout(()=>{el.style.outline=o},1500);
return 1;"""


def show_cursor(i):
    page_js(find_el(i) + CURSOR_JS)
    time.sleep(0.45)  # Zeit, damit man sieht, was gleich passiert


def native_click(x, y):
    # Echte Maus-Ereignisse (isTrusted). Safari nimmt sie nur an, wenn es vorne ist (an die PID geschickte
    # Klicks ins Hintergrundfenster wirken nicht, getestet 2026-11-23). Darum: ~0,5 s Safari nach vorne,
    # klicken, dann Maus und vorheriges Programm zurueck. Braucht Bedienungshilfen-Recht fuer die Claude-App.
    # Sicherheit: x/y kommen aus der Webseite – nur echte Zahlen in das Skript lassen, sonst Code-Einschleusung
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        raise ToolError("Ungültige Klickposition von der Seite")
    if not (-10000 < x < 20000 and -10000 < y < 20000):
        raise ToolError("Klickposition außerhalb des Bildschirms")
    osa(("ObjC.import('AppKit');ObjC.import('CoreGraphics');"
         "var prev=$.NSWorkspace.sharedWorkspace.frontmostApplication;"
         "var old=$.CGEventGetLocation($.CGEventCreate(null));"
         f"var s=Application('Safari');s.windows.byId({int(wj('return w.id();'))}).index=1;s.activate();delay(0.3);"
         f"var p={{x:{x},y:{y}}};"
         "[5,1,2].forEach(function(t){var e=$.CGEventCreateMouseEvent(null,t,p,0);$.CGEventSetIntegerValueField(e,1,1);"
         "$.CGEventPost(0,e);delay(0.05);});delay(0.1);"
         "$.CGWarpMouseCursorPosition(old);prev.activateWithOptions(0);"))


def after(args, msg):
    return snapshot() if args.get("then_snapshot") else msg


# ---------- Werkzeuge ----------

def t_tabs(a):
    gate()
    if a.get("select"):
        wj(f"w.currentTab=w.tabs[{int(a['select']) - 1}];return '';")
    if a.get("adopt"):  # Tab des Nutzers in Claudes Fenster uebernehmen (als Kopie, sein Tab bleibt)
        wn, tn = (int(x) for x in a["adopt"].split(":"))
        url = jxa(f"return s.windows[{wn - 1}].tabs[{tn - 1}].url()||'';")
        check_url(url)
        wj(f"var t=s.Tab({{url:{json.dumps(url)}}});w.tabs.push(t);w.currentTab=t;return '';")
        wait_loaded()
    if a.get("all"):
        return jxa(f"var r=[];s.windows().forEach(function(w,i){{var c=w.id()=={win_id()};w.tabs().forEach(function(t,j){{"
                   "r.push((c?'CLAUDE ':'')+(i+1)+':'+(j+1)+' '+t.name().slice(0,60)+' | '+t.url());});});return r.join('\\n');")
    return wj("var r=[],ci=w.currentTab().index();w.tabs().forEach(function(t,j){"
              "r.push((j+1)+(t.index()===ci?' *':'  ')+' '+t.name().slice(0,60)+' | '+t.url());});return r.join('\\n');")


def close_window():
    """Schliesst nur Claudes eigenes Fenster (nie eins des Nutzers)."""
    wid = win_id()
    if wid:
        jxa(f"try{{s.windows.byId({wid}).close()}}catch(e){{}};return '';")
    WIN_FILE.unlink(missing_ok=True)


def t_close(a):
    close_window()
    return "Claudes Safari-Fenster geschlossen."


def t_open(a):
    gate()
    url = a["url"]
    if not re.match(r"https?://", url):
        url = "https://" + url
    check_url(url)
    if a.get("new_tab"):
        wj(f"var t=s.Tab({{url:{json.dumps(url)}}});w.tabs.push(t);w.currentTab=t;return '';")
    else:
        page_js(MARK + "return 1", guard=False)
        wj(f"w.currentTab().url={json.dumps(url)};return '';")
    wait_loaded()
    check_url(current_url())  # Weiterleitung auf gesperrte Seite
    return after(a, "geoeffnet: " + current_url())


def t_snapshot(a):
    gate()
    return snapshot(a.get("max_items", 120), a.get("all", False), a.get("query"))


def t_text(a):
    gate()
    sel = json.dumps(a.get("selector") or "")
    # ohne Selektor: Hauptinhalt statt ganzer Seite (Menues/Fussleiste kosten nur Tokens)
    txt = page_js(f"const e={sel}?document.querySelector({sel}):(document.querySelector('main,[role=main]')||document.querySelector('article')||document.body);"
                  "if(!e)throw new Error('Selektor nicht gefunden');"
                  "return e.innerText.replace(/\\n\\s*\\n+/g,'\\n');")
    mx = int(a.get("max_chars", 4000))
    return txt[:mx] + (f"\n…(gekuerzt, {len(txt)} Zeichen gesamt)" if len(txt) > mx else "")


def t_click(a):
    gate()
    a["id"] = resolve(a)
    show_cursor(a["id"])
    if not a.get("native"):
        page_js(find_el(a["id"]) + MARK + "el.focus?.();el.click();return 1;")
    else:
        # shortcut: setzt Seitenzoom 100 % und Safari auf dem Hauptbildschirm voraus
        x, y = page_js(find_el(a["id"]) + MARK + "const r=el.getBoundingClientRect();"
                       "return [Math.round(screenX+r.left+r.width/2), Math.round(screenY+outerHeight-innerHeight+r.top+r.height/2)];")
        native_click(x, y)
    wait_loaded(10, settle=0.6)
    return after(a, "geklickt. " + current_url())


def t_type(a):
    gate()
    a["id"] = resolve(a)
    show_cursor(a["id"])
    js = find_el(a["id"]) + """
if(el.type==='password') throw new Error('Passwortfelder fuellt der Nutzer selbst aus');
el.focus();window.__mcpMark=1;
if(el.isContentEditable){document.execCommand('selectAll');document.execCommand('insertText',false,TEXT);}
else{const P=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:el instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype;
 Object.getOwnPropertyDescriptor(P,'value').set.call(el,TEXT);
 el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}
if(SUBMIT){ if(el.form) el.form.requestSubmit(); else ['keydown','keypress','keyup'].forEach(t=>el.dispatchEvent(new KeyboardEvent(t,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true})));}
return 1;"""
    page_js(js.replace("TEXT", json.dumps(a["text"])).replace("SUBMIT", "true" if a.get("submit") else "false"))
    if a.get("submit"):
        wait_loaded(10, settle=1.0)
    return after(a, "eingetragen.")


def t_eval(a):
    gate()
    return json.dumps(page_js(a["code"]), ensure_ascii=False)[:int(a.get("max_chars", 4000))]


def t_screenshot(a):
    gate()
    check_url(current_url())
    # Kein Nach-vorne-Holen: verdeckte Fenster lassen sich aufnehmen, nur nicht minimierte oder auf anderem Space
    wid = str(int(wj("return w.id();")))
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.jpg")
        try:
            run(["screencapture", "-x", "-o", "-t", "jpg", "-l", wid, f])
        except ToolError:
            raise ToolError("Screenshot ging nicht: Claudes Safari-Fenster ist minimiert oder auf einem anderen "
                            "Schreibtisch. Der Nutzer soll es auf diesen Schreibtisch legen (darf hinter anderen Fenstern liegen).")
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
        if src in ("current_tab", "user_tab"):
            gate()
            src = current_url() if src == "current_tab" else jxa("return s.windows[0].currentTab().url()||'';")
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


def t_batch(a):
    """Mehrere Schritte in einem Aufruf (spart Modell-Runden). Bricht beim ersten Fehler ab."""
    global USER_TAB
    out, extra = [], []
    for n, step in enumerate(a["steps"], 1):
        name = step.get("tool", "")
        name = name if name.startswith(("safari_", "media_")) else "safari_" + name
        args = step.get("args") or {}
        if name == "safari_batch" or name not in TOOLS:
            raise ToolError(f"Schritt {n}: unbekanntes Werkzeug {name}")
        USER_TAB = bool(args.get("user_tab", a.get("user_tab")))
        try:
            res = TOOLS[name][0](args)
        except (ToolError, subprocess.TimeoutExpired, KeyError, ValueError) as e:
            out.append(f"{n} {name[7:]}: FEHLER {e} – Rest abgebrochen")
            break
        if isinstance(res, list):  # Bilder/Transkript durchreichen, danach weitermachen
            out.append(f"{n} {name[7:]}: (siehe Anhang)")
            extra += res
        else:
            out.append(f"{n} {name[7:]}: {res}")
    return [{"type": "text", "text": "\n".join(out)}] + extra if extra else "\n".join(out)


S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}
UT = {"user_tab": {"type": "boolean", "description": "NUR wenn der Nutzer es ausdruecklich sagt: im Tab des Nutzers (vorderstes eigenes Fenster) statt in Claudes Fenster arbeiten"}}
SNAP = {**UT, "then_snapshot": {"type": "boolean", "description": "danach direkt Snapshot liefern (spart einen Aufruf)"}}
TOOLS = {
    "safari_tabs": (t_tabs, "Claude arbeitet in einem eigenen Safari-Fenster im Hintergrund (Nutzer arbeitet parallel). "
                            "Listet dessen Tabs (* = aktiv); select=3 wechselt. all=true listet alle Fenster (nur lesen), "
                            "adopt='Fenster:Tab' aus der all-Liste kopiert einen Nutzer-Tab ins Claude-Fenster.",
                    S({"select": {"type": "integer"}, "all": {"type": "boolean"}, "adopt": {"type": "string"}})),
    "safari_batch": (t_batch, "Mehrere Safari-Schritte in EINEM Aufruf (spart Runden/Usage). steps=[{tool:'open',args:{url}},{tool:'type',args:{query:'durchsuchen',text:'x',submit:true}},{tool:'click',args:{query:'Linktext'}},{tool:'text',args:{max_chars:1500}},{tool:'close'}]. Mit query statt id braucht es keinen Snapshot vorher.",
                     S({"steps": {"type": "array", "items": {"type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"}}, "required": ["tool"]}}, **UT}, ["steps"])),
    "safari_close": (t_close, "Schliesst Claudes eigenes Safari-Fenster. Am Ende jeder Safari-Aufgabe aufrufen, ausser der Nutzer will es offen lassen.",
                     S({})),
    "safari_open": (t_open, "Oeffnet URL im aktiven Tab von Claudes Fenster (oder new_tab) und wartet aufs Laden.",
                    S({"url": {"type": "string"}, "new_tab": {"type": "boolean"}, **SNAP}, ["url"])),
    "safari_snapshot": (t_snapshot, "Kompakte Liste der sichtbaren bedienbaren Elemente mit Nummern. Billiger als Screenshot. query filtert (am sparsamsten). all=true: ganze Seite.",
                        S({"max_items": {"type": "integer"}, "all": {"type": "boolean"}, "query": {"type": "string", "description": "nur passende Elemente, Begriffe mit | trennen (spart Tokens)"}, **UT})),
    "safari_text": (t_text, "Sichtbarer Text der Seite oder eines CSS-Selektors, gekuerzt.",
                    S({"selector": {"type": "string"}, "max_chars": {"type": "integer"}, **UT})),
    "safari_click": (t_click, "Klickt Element-Nummer aus dem Snapshot per JS (oranger Cursor zeigt es vorher). native=true: echter Mausklick, holt Safari dafuer ~0,5 s nach vorne und stellt Maus/App danach zurueck – nur wenn JS-Klick nicht wirkt (z.B. Video-Play).",
                     S({"id": {"type": "integer"}, "query": {"type": "string", "description": "statt id: erstes Element, dessen Text/Link/Typ diesen Begriff enthaelt (z.B. 'durchsuchen')"}, "native": {"type": "boolean"}, **SNAP})),
    "safari_type": (t_type, "Setzt Text in Feld (Nummer aus Snapshot). submit=true schickt das Formular ab. Keine Passwortfelder.",
                    S({"id": {"type": "integer"}, "query": {"type": "string", "description": "statt id: erstes Element, dessen Text/Link/Typ diesen Begriff enthaelt (z.B. 'durchsuchen')"}, "text": {"type": "string"}, "submit": {"type": "boolean"}, **SNAP}, ["text"])),
    "safari_eval": (t_eval, "Fuehrt JS-Funktionsrumpf im Tab aus, Ergebnis per return (z.B. scrollen: 'scrollBy(0,800);return scrollY').",
                    S({"code": {"type": "string"}, "max_chars": {"type": "integer"}, **UT}, ["code"])),
    "safari_screenshot": (t_screenshot, "Screenshot des Safari-Fensters (teuer, nur wenn Layout/Bild wichtig).",
                          S({"max_px": {"type": "integer", "description": "laengste Kante, Standard 1024"}, **UT})),
    "media_transcribe": (t_media, "Video/Audio lokal auswerten. source: URL, Dateipfad, 'current_tab' (Claudes Fenster) oder 'user_tab' (vorderstes Safari-Fenster des Nutzers). mode: audio (nur Transkript, Standard), frames (nur Bilder), both.",
                         S({"source": {"type": "string"}, "mode": {"type": "string", "enum": ["audio", "frames", "both"]},
                            "every_s": {"type": "number", "description": "Bildabstand in s, Standard 5"},
                            "max_frames": {"type": "integer", "description": "Standard 8"},
                            "lang": {"type": "string", "description": "de, en … oder auto"},
                            "max_chars": {"type": "integer", "description": "Transkript kuerzen, Standard 20000; Volltext als Datei"}}, ["source"])),
}


# ---------- MCP über stdio ----------

# Wird jedem Client beim Start gezeigt, damit er die Werkzeuge auch ohne Projekt-Notiz bevorzugt.
INSTRUCTIONS = (
    "Real Safari with the user's logins. Prefer these tools for any web research, page reading or "
    "browsing over other browser tools; use another browser only as fallback. Use safari_batch to save "
    "round trips and the query filter on snapshot/text. If a tool says the switch is off, give the user "
    "the command `python3 ~/mcp/safari/server.py on 30` and wait; never switch it on yourself. "
    "Work in the user's own tabs (user_tab) only when they ask. Call safari_close when the task is done. "
    "media_transcribe transcribes video/audio locally."
)

def handle(msg):
    m, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if m == "initialize":
        r = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
             "capabilities": {"tools": {}}, "serverInfo": {"name": "safari", "version": "1.0"},
             "instructions": INSTRUCTIONS}
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
            global USER_TAB
            args = p.get("arguments") or {}
            USER_TAB = bool(args.get("user_tab"))
            try:
                out = fn(args)
            finally:
                USER_TAB = False
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
    global FLAG
    real, FLAG = FLAG, Path(tempfile.mkdtemp()) / "flag"  # eigene Testdatei: echter Schalter bleibt unberuehrt
    try:
        write_flag("0")
        try:
            gate(); raise AssertionError("gate offen")
        except ToolError:
            pass
        r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "safari_tabs", "arguments": {}}})
        assert r["result"]["isError"] and "AUS" in r["result"]["content"][0]["text"]
    finally:
        FLAG = real
    assert osa("function run(){return 6*7}") == "42"  # Swift-Helfer laeuft
    print("selftest ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "on":
        mins = float(sys.argv[2]) if len(sys.argv) > 2 else 30
        write_flag(str(time.time() + mins * 60))
        print(f"Safari-Zugriff AN fuer {mins:g} Min (bis {time.strftime('%H:%M', time.localtime(enabled_until()))})")
    elif cmd == "off":
        write_flag("0")
        try:
            close_window()
        except ToolError:
            pass  # Safari laeuft nicht
        print("Safari-Zugriff AUS, Claudes Fenster geschlossen")
    elif cmd == "status":
        left = enabled_until() - time.time()
        print(f"AN, noch {left / 60:.0f} Min" if left > 0 else "AUS")
    elif cmd == "selftest":
        selftest()
    else:
        serve()
