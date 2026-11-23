# safari-mcp

🇩🇪 [Deutsch](#deutsch) · 🇬🇧 [English](#english)

---

## Deutsch

Ein MCP-Server, mit dem Claude Code (oder ein anderer MCP-Client) **dein echtes Safari** steuern kann. Er kann Tabs lesen, Seiten öffnen, klicken, Formulare ausfüllen und Screenshots machen. Dazu kommt ein lokales **Video- und Audio-Transkript** mit Whisper.

- **Arbeitet im Hintergrund:** Claude bekommt ein eigenes Safari-Fenster, und du arbeitest parallel weiter, zum Beispiel in Pages. Ein **oranger Cursor** zeigt vor jedem Klick, was Claude anklickt.
- **Spart Tokens:** Statt Screenshots liefert er kompakte Elementlisten mit Suchfilter. Mit `safari_batch` laufen mehrere Schritte in einem Aufruf.
- **Schnell:** Ein kleiner Swift-Helfer (OSAKit) bleibt dauerhaft offen, statt bei jedem Aufruf `osascript` neu zu starten.
- **Schlank:** Python-Standardbibliothek, kein Netzwerk-Port, keine Pakete.

### Sicherheit
Ein KI-Agent in deinem eingeloggten Browser ist mächtig. Webseiten können versteckte Anweisungen enthalten (Prompt Injection). Deshalb gilt:
- **Zeitschalter:** Der Server arbeitet nur, wenn **du** ihn einschaltest (`on 30`). Nach der Zeit schaltet er sich selbst ab. Die KI soll ihn nie selbst einschalten.
- **Eigenes Fenster:** Claude arbeitet nur in seinem eigenen Fenster. Deine Tabs fasst es nur an, wenn du ausdrücklich darum bittest (`user_tab=true`).
- **Sperrliste:** Banken, PayPal, Klarna, Krypto-Börsen, Apple-ID, Google-Konto, Elster und iCloud sind gesperrt. Die Liste lässt sich in `server.py` erweitern (`BLOCK_DOMAINS`, `BLOCK_WORDS`).
- **Keine Passwortfelder:** `safari_type` füllt sie nie aus.
- **Kein Netzwerk-Port:** Der Server läuft nur über stdio als Kindprozess deines MCP-Clients. Aus dem Internet ist er nicht erreichbar.
- **Grenze:** `safari_eval` führt beliebiges JavaScript aus. Die Sperrliste wird vor jeder Aktion geprüft, ein Skript kann danach aber weiternavigieren. Schalte den Server nur ein, solange du ihn brauchst.

### Voraussetzungen
- macOS mit Safari und Python 3.
- In Safari unter Einstellungen → Erweitert „Webentwickler-Funktionen anzeigen“ aktivieren, dann im Menü Entwickler **„JavaScript von Apple Events erlauben“** einschalten.
- Empfohlen: die Xcode Command Line Tools (`xcode-select --install`). Damit baut sich der schnelle Swift-Helfer automatisch. Ohne sie nutzt der Server `osascript`, das ist langsamer.
- macOS fragt beim ersten Mal nach diesen Freigaben für deinen MCP-Client:
  - **Automation** für Safari,
  - **Bedienungshilfen** für echte Klicks (`native=true`),
  - **Bildschirmaufnahme** für Screenshots.
- Nur für Transkripte:
  ```sh
  brew install ffmpeg whisper-cpp yt-dlp
  mkdir -p ~/mcp/safari/models && curl -L -o ~/mcp/safari/models/ggml-large-v3-turbo-q5_0.bin \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
  ```

### Installation (Claude Code)
```sh
git clone https://github.com/rj6zs826fn-web/safari-mcp.git ~/mcp/safari
claude mcp add --scope user safari -- python3 ~/mcp/safari/server.py
python3 ~/mcp/safari/server.py selftest
```
Danach eine neue Claude-Sitzung starten oder die App neu laden. Andere MCP-Clients wie Cursor, VS Code oder LM Studio trägst du genauso ein: Befehl `python3`, Argument der Pfad zu `server.py`.

### Schalter
```sh
python3 ~/mcp/safari/server.py on 30   # 30 Minuten an
python3 ~/mcp/safari/server.py off     # aus, schließt auch Claudes Fenster
python3 ~/mcp/safari/server.py status
```
Mit [AppVol](https://github.com/rj6zs826fn-web/appvol) geht das auch über die Notch (Einstellungen → Safari-MCP → Ordner wählen).

### Werkzeuge
| Werkzeug | Zweck |
|---|---|
| `safari_batch` | mehrere Schritte (open, type, click, text, close …) in **einem** Aufruf |
| `safari_tabs` | Tabs in Claudes Fenster; `all=true` zeigt alle Fenster (nur lesen), `adopt="2:3"` übernimmt einen Tab |
| `safari_open` | URL öffnen und auf das Laden warten |
| `safari_snapshot` | nummerierte Liste der bedienbaren Elemente; `query` filtert (am sparsamsten) |
| `safari_text` | Hauptinhalt der Seite als Text, gekürzt |
| `safari_click` | Element klicken per `id` oder `query`, per JS oder mit `native=true` als echter Mausklick |
| `safari_type` | Feld ausfüllen per `id` oder `query`, optional abschicken (keine Passwortfelder) |
| `safari_eval` | JavaScript ausführen (z. B. scrollen) |
| `safari_screenshot` | Screenshot von Claudes Fenster, ohne es nach vorne zu holen |
| `safari_close` | Claudes Fenster schließen |
| `media_transcribe` | Video/Audio aus URL, Datei, `current_tab` oder `user_tab`; `mode` = `audio`, `frames` oder `both` |

Fast alle Werkzeuge kennen `user_tab=true`: Sie arbeiten dann im vordersten Fenster des Nutzers statt in Claudes Fenster.

Beispiel für `safari_batch`:
```json
{"steps": [
  {"tool": "open",  "args": {"url": "https://de.wikipedia.org"}},
  {"tool": "type",  "args": {"query": "durchsuchen", "text": "Fischereihafen", "submit": true}},
  {"tool": "click", "args": {"query": "Fischereihafen (Bremerhaven)"}},
  {"tool": "text",  "args": {"max_chars": 1500}},
  {"tool": "close"}
]}
```

### Leistung
Gemessen auf einem MacBook Air M4 mit macOS 27.

**Geschwindigkeit pro Aktion:**

| Aktion | Dauer |
|---|---|
| Seite öffnen | ~0,9 s |
| Snapshot | ~0,12 s |
| Text lesen | ~0,10 s |
| Tippen + Abschicken | ~1,0 s |
| Klick (JS) | ~0,9 s, inklusive Laden der Folgeseite |
| Klick (nativ) | ~1,5 s |
| Screenshot | ~0,3 s |
| JXA-Aufruf mit Swift-Helfer statt `osascript` | 24 ms statt 128 ms |

**Tokens und Usage für dieselbe Aufgabe** (Wikipedia: suchen, Artikel öffnen, 1.500 Zeichen lesen):

| | safari-mcp mit Batch | Browser-Werkzeug mit Einzelschritten |
|---|---|---|
| Modell-Runden | **1** | 3 |
| Tokens (Aufruf + Ergebnis) | **~530** | ~1.065 |
| Usage bei 30k Kontext (geschätzt) | **~4.000** | ~11.000 |
| Dauer | ~3,3 s | – |

Die Usage hängt vor allem an der Zahl der Runden, weil jede Runde den ganzen Kontext neu liest. Batch spart deshalb am meisten. Die Tokens sind grob geschätzt (Zeichen geteilt durch 4).

**Transkript:** Ein 2-Minuten-Video braucht mit `large-v3-turbo` etwa 20–30 s, Bilder unter 1 s. DRM-Videos (z. B. Netflix) liefern schwarze Bilder.

### Lizenz
**MIT mit Commons Clause.** Für jeden kostenlos nutzbar, auch zum Ändern, Weitergeben und für die eigene Arbeit. **Verboten ist, die Software zu verkaufen**, also sie selbst oder ein Produkt, dessen Wert im Wesentlichen auf ihr beruht, gegen Geld anzubieten. Maßgeblich ist der Text in [LICENSE](LICENSE).

---

## English

An MCP server that lets Claude Code (or any other MCP client) control **your real Safari**: read tabs, open pages, click, fill in forms and take screenshots. It also transcribes **video and audio locally** with Whisper.

- **Works in the background:** Claude gets its own Safari window while you keep working in other apps. An **orange cursor** shows what Claude is about to click.
- **Saves tokens:** compact element lists with a search filter instead of screenshots, and `safari_batch` to run several steps in one call.
- **Fast:** a small persistent Swift helper (OSAKit) instead of launching `osascript` on every call.
- **Lean:** Python standard library only, no network port, no packages.

### Security
An AI agent in your logged-in browser is powerful, and web pages can contain hidden instructions (prompt injection). Therefore:
- **Timer switch:** the server only works after **you** switch it on (`on 30`) and turns itself off afterwards. The AI should never switch it on itself.
- **Own window:** Claude only works in its own window. It touches your tabs only when you explicitly ask (`user_tab=true`).
- **Blocklist:** banks, PayPal, Klarna, crypto exchanges, Apple ID, Google account, Elster and iCloud are blocked. You can extend the list in `server.py` (`BLOCK_DOMAINS`, `BLOCK_WORDS`).
- **No password fields:** `safari_type` never fills them.
- **No network port:** the server runs only over stdio as a child process of your MCP client and can't be reached from the internet.
- **Limit:** `safari_eval` runs arbitrary JavaScript. The blocklist is checked before each action, but a script can navigate further afterwards. Only switch the server on while you need it.

### Requirements
- macOS with Safari and Python 3.
- In Safari, go to Settings → Advanced and enable “Show features for web developers”. Then turn on **“Allow JavaScript from Apple Events”** in the Develop menu.
- Recommended: the Xcode Command Line Tools (`xcode-select --install`). With them the fast Swift helper builds itself automatically. Without them the server falls back to `osascript`, which is slower.
- On first use, macOS asks to grant your MCP client these permissions:
  - **Automation** for Safari,
  - **Accessibility** for native clicks (`native=true`),
  - **Screen Recording** for screenshots.
- Transcripts only:
  ```sh
  brew install ffmpeg whisper-cpp yt-dlp
  mkdir -p ~/mcp/safari/models && curl -L -o ~/mcp/safari/models/ggml-large-v3-turbo-q5_0.bin \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
  ```

### Installation (Claude Code)
```sh
git clone https://github.com/rj6zs826fn-web/safari-mcp.git ~/mcp/safari
claude mcp add --scope user safari -- python3 ~/mcp/safari/server.py
python3 ~/mcp/safari/server.py selftest
```
Then start a new Claude session or reload the app. Other MCP clients (Cursor, VS Code, LM Studio, …) are set up the same way: command `python3`, argument the path to `server.py`.

### Switch
```sh
python3 ~/mcp/safari/server.py on 30   # on for 30 minutes
python3 ~/mcp/safari/server.py off     # off, also closes Claude's window
python3 ~/mcp/safari/server.py status
```

### Tools
| Tool | Purpose |
|---|---|
| `safari_batch` | several steps (open, type, click, text, close, …) in **one** call |
| `safari_tabs` | tabs in Claude's window; `all=true` lists all windows (read-only), `adopt="2:3"` copies a tab |
| `safari_open` | open a URL and wait for it to load |
| `safari_snapshot` | numbered list of interactive elements; `query` filters it (cheapest) |
| `safari_text` | main page content as text, truncated |
| `safari_click` | click by `id` or `query`, via JS or as a real mouse click with `native=true` |
| `safari_type` | fill a field by `id` or `query`, optionally submit (never password fields) |
| `safari_eval` | run JavaScript (e.g. scrolling) |
| `safari_screenshot` | screenshot of Claude's window without bringing it to the front |
| `safari_close` | close Claude's window |
| `media_transcribe` | video/audio from a URL, a file, `current_tab` or `user_tab`; `mode` = `audio`, `frames` or `both` |

Almost all tools accept `user_tab=true`, which makes them work in the user's front window instead. The `safari_batch` example is in the German section above.

### Performance
Measured on a MacBook Air M4 with macOS 27.

**Speed per action:**

| Action | Time |
|---|---|
| Open page | ~0.9 s |
| Snapshot | ~0.12 s |
| Read text | ~0.10 s |
| Type + submit | ~1.0 s |
| Click (JS) | ~0.9 s, including the next page loading |
| Click (native) | ~1.5 s |
| Screenshot | ~0.3 s |
| JXA call: Swift helper vs. `osascript` | 24 ms vs. 128 ms |

**Tokens and usage for the same task** (Wikipedia: search, open article, read 1,500 characters):

| | safari-mcp with batch | browser tool, step by step |
|---|---|---|
| Model round trips | **1** | 3 |
| Tokens (call + result) | **~530** | ~1,065 |
| Usage at 30k context (estimated) | **~4,000** | ~11,000 |
| Time | ~3.3 s | – |

Usage is driven mostly by the number of round trips, because every round trip re-reads the whole context. That is why batching saves the most. Token counts are rough estimates (characters divided by 4).

**Transcription:** a 2-minute video takes about 20–30 s with `large-v3-turbo`, frames under 1 s. DRM videos (e.g. Netflix) give black frames.

### License
**MIT with Commons Clause.** Free for everyone to use, modify, share and use in their own work. **Selling the software is not allowed**: you may not offer it, or a product whose value derives substantially from it, for a fee. The binding text is in [LICENSE](LICENSE).
