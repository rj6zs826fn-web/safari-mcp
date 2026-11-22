# safari-mcp

Ein MCP-Server, mit dem Claude Code (oder ein anderer MCP-Client) **dein echtes Safari** steuern kann: Tabs lesen, Seiten öffnen, klicken, Formulare ausfüllen, Screenshots machen. Dazu kommt ein lokales **Video- und Audio-Transkript** mit Whisper. Es ist eine einzige Python-Datei, nur mit der Standardbibliothek und ohne Netzwerk-Port.

*MCP server that lets Claude control your real Safari (JXA + native clicks) and transcribe video/audio locally.*

## Sicherheit zuerst
Ein KI-Agent in deinem eingeloggten Browser ist mächtig. Webseiten können versteckte Anweisungen enthalten (Prompt Injection). Deshalb gilt:
- **Zeitschalter:** Der Server arbeitet nur, wenn du ihn selbst einschaltest (`python3 server.py on 30`). Nach der eingestellten Zeit schaltet er sich von selbst aus.
- **Sperrliste:** Banken, PayPal, Klarna, Krypto-Börsen, Apple-ID, Google-Konto, Elster und iCloud sind gesperrt. Erweitern kannst du die Liste oben in `server.py` (`BLOCK_DOMAINS`, `BLOCK_WORDS`).
- **Keine Passwortfelder:** `safari_type` füllt sie nie aus.
- **Kein Netzwerk-Port:** Der Server läuft nur über stdio als Kindprozess deines MCP-Clients.
- **Grenzen:** `safari_eval` führt beliebiges JavaScript auf der aktuellen Seite aus. Die Sperrliste prüft die Seite vor jeder Aktion, aber ein Skript kann danach weiternavigieren. Schalte den Zugriff nur ein, solange du ihn brauchst. Für Logins empfiehlt sich ein eigenes Safari-Profil, das nur die nötigen Konten enthält.

## Voraussetzungen
- macOS mit Safari, Python 3
- In Safari: Einstellungen → Erweitert → „Webentwickler-Funktionen anzeigen“, dann im Menü Entwickler → **„JavaScript von Apple Events erlauben“**. Das musst du selbst einschalten.
- Für echte Klicks (`native=true`) braucht dein MCP-Client die Freigabe unter **Bedienungshilfen**, für Screenshots die Freigabe für **Bildschirmaufnahme**. macOS fragt beim ersten Mal danach.
- Nur für Transkripte: `brew install ffmpeg whisper-cpp yt-dlp` und ein Whisper-Modell:
  ```sh
  mkdir -p models && curl -L -o models/ggml-large-v3-turbo-q5_0.bin \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
  ```

## Einrichten (Claude Code)
```sh
git clone https://github.com/rj6zs826fn-web/safari-mcp.git ~/mcp/safari
claude mcp add --scope user safari -- python3 ~/mcp/safari/server.py
python3 ~/mcp/safari/server.py selftest
```
Danach eine neue Claude-Sitzung starten.

## Schalter
```sh
python3 ~/mcp/safari/server.py on 30   # 30 Minuten an
python3 ~/mcp/safari/server.py off
python3 ~/mcp/safari/server.py status
```
Mit [AppVol](https://github.com/rj6zs826fn-web/appvol) geht das auch über die Notch (Einstellungen → Safari-MCP → Ordner wählen).

## Werkzeuge
| Werkzeug | Zweck |
|---|---|
| `safari_tabs` | Tabs auflisten, mit `select="1:3"` wechseln |
| `safari_open` | URL öffnen (optional neuer Tab) |
| `safari_snapshot` | nummerierte Liste der bedienbaren Elemente (spart Tokens gegenüber Screenshots) |
| `safari_text` | sichtbarer Text, gekürzt |
| `safari_click` | Element klicken, per JS oder mit `native=true` als echter Mausklick |
| `safari_type` | Feld ausfüllen, optional abschicken (keine Passwortfelder) |
| `safari_eval` | JavaScript ausführen (z. B. scrollen) |
| `safari_screenshot` | Screenshot des Safari-Fensters |
| `media_transcribe` | Video/Audio aus URL, Datei oder `current_tab`; `mode` = `audio` (nur Transkript), `frames` (nur Bilder), `both` |

Richtwerte auf einem M4: 2 Minuten Video ≈ 20–30 s für das Transkript, Bilder unter 1 s. DRM-Videos (z. B. Netflix) liefern schwarze Bilder.

## Lizenz
MIT
