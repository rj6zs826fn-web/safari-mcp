// Dauerhafter JXA-Ausfuehrer fuer server.py: ersetzt einen osascript-Start pro Aufruf (~150 ms)
// durch einen laufenden Prozess. Protokoll: je Zeile JSON {"s": "<JXA-Quelltext>"} rein,
// je Zeile {"ok": true, "r": "<Ergebnis>"} oder {"ok": false, "e": "<Fehler>"} raus.
// Bauen (macht server.py automatisch): xcrun swiftc -O helper.swift -o helper
import Foundation
import OSAKit

let lang = OSALanguage(forName: "JavaScript")!

func reply(_ obj: [String: Any]) {
    var data = try! JSONSerialization.data(withJSONObject: obj)
    data.append(0x0A)
    FileHandle.standardOutput.write(data)
}

while let line = readLine() {
    guard let d = line.data(using: .utf8),
          let req = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
          let src = req["s"] as? String else {
        reply(["ok": false, "e": "ungueltige Anfrage"])
        continue
    }
    var err: NSDictionary?
    if let res = OSAScript(source: src, language: lang).executeAndReturnError(&err) {
        reply(["ok": true, "r": res.stringValue ?? ""])
    } else {
        reply(["ok": false, "e": (err?[OSAScriptErrorMessageKey] as? String) ?? String(describing: err)])
    }
}
