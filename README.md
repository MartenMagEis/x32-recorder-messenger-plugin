# x32-recorder Messenger-Plugin

Ein [x32-recorder](https://github.com/tobire42/x32-recorder)-Plugin (Art `django_app`): verschickt den
Browser-Mix (`playback.mp3`) einer Aufnahme oder eines ausgeschnittenen Song-Clips an konfigurierbare
Signal-Gruppen - manuell über einen "Senden"-Button in x32-recorders Download-Menüs, oder automatisch
direkt nach dem Schneiden.

Nutzt [signal-cli](https://github.com/AsamK/signal-cli) über das eigene Signal-Konto, als Zweitgerät
verknüpft (QR-Code-Scan wie bei Signal Desktop) - keine eigene Telefonnummer nötig.

## Voraussetzung: eine Java-Laufzeitumgebung (JRE)

[signal-cli](https://github.com/AsamK/signal-cli) selbst lädt und aktualisiert dieses Plugin
automatisch von GitHub (siehe Einrichtung unten) - lediglich das Java-Laufzeitumgebung dafür
bleibt eine manuelle, einmalige Voraussetzung (Größe/Plattformabhängigkeit macht ein automatisches
Mitliefern unpraktikabel). Auf einem Raspberry Pi z.B.:
```
sudo apt install default-jre
```

## Installation

1. Über die x32-recorder Settings-Seite → "Plugins" → GitHub-Link:
   `https://github.com/MartenMagEis/x32-recorder-messenger-plugin.git` - `plugin.json` wird
   automatisch erkannt (Art `django_app`).
2. Zusätzliche Python-Abhängigkeit installieren:
   ```
   uv pip install -r plugins/x32_recorder_messenger_plugin/requirements.txt
   ```
3. x32-recorder-Dienste neu starten, damit die Django-App geladen wird.

## Einrichtung (alles über die x32-recorder Settings-Seite)

1. Plugin-Karte → "Konfigurieren" öffnen.
2. "signal-cli installieren/aktualisieren" klicken - lädt die neueste Version von GitHub
   herunter und entpackt sie in den Plugin-Ordner (`signal-cli-bin/current/`). Ein periodischer
   Hintergrund-Check zeigt danach automatisch an, wenn eine neuere Version verfügbar ist (wendet
   sie aber nie von selbst an - "Neueste verfügbare Version" vs. "signal-cli Version" vergleichen
   und bei Bedarf erneut klicken).
3. "Signal verknüpfen" klicken - ein QR-Code erscheint.
4. Mit der Signal-App auf dem Handy scannen: Einstellungen → Verknüpfte Geräte → Gerät
   verknüpfen. Der QR-Code ist nur für diesen einen Verknüpfungsvorgang gültig und verschwindet
   danach wieder aus der Oberfläche (siehe Geheimdaten-Hinweis unten).
5. Nach erfolgreicher Verknüpfung: mindestens eine Ziel-Gruppe eintragen (Name + Signal-Gruppen-ID)
   und "Aktiviert" einschalten.
6. Optional pro Ziel "Auto-Senden nach Schnitt" aktivieren, damit frisch geschnittene Song-Clips
   automatisch dorthin gehen, sobald ihr Browser-Mix fertig ist.
7. "Aktiviert" (oben, Plugin-weit) einschalten - erst dann sendet das Plugin überhaupt etwas.

Solange keine Signal-Gruppen-ID bekannt ist: `plugins/x32_recorder_messenger_plugin/signal-cli-bin/current/bin/signal-cli
--config plugins/x32_recorder_messenger_plugin/signal-cli-data listGroups -o json` auf der
Maschine ausführen, auf der x32-recorder läuft, um die IDs der eigenen Gruppen zu sehen.

## Geheimdaten

Alle Signal-Sitzungs-/Identitätsdaten liegen ausschließlich in
`plugins/x32_recorder_messenger_plugin/signal-cli-data/` und werden über keinen API-Endpunkt
ausgelesen oder angezeigt - `signal_backend.py` übergibt das Verzeichnis nur als Kommandozeilen-
Argument an `signal-cli`, liest seinen Inhalt aber nie ein. Die einzige während der Einrichtung
sichtbare sensible Information ist der Verknüpfungs-QR-Code selbst (`linking.py`) - der wird nach
erfolgreichem Abschluss aus dem In-Memory-Status gelöscht und danach nie wieder ausgeliefert,
auch nicht bei einem erneuten Laden der Konfigurationsseite. Die Signal-Gruppen-ID ist keine
geheime Anmeldeinformation (ohne verknüpftes Gerät nutzlos) und wird deshalb normal in der
Ziel-Gruppen-Liste angezeigt.

## Warum das ohne Änderungen am C-Controller funktioniert

Der versendete Browser-Mix (`playback.mp3`) wird bereits vom C-Controller erzeugt, sobald
x32-recorders `playback_status` einer Aufnahme auf "wird vorbereitet" gesetzt wird (derselbe
Mechanismus wie beim manuellen "Browser-Mix vorbereiten"). Dieses Plugin setzt bei Bedarf nur
dieses eine Datenbankfeld und wartet dann, bis der Controller die Datei fertig geschrieben hat -
genau wie x32-recorders eigene Frontend-Buttons das auch tun.

## Testen ohne echten Versand

`enabled` auf dem Plugin-weiten Schalter (nicht pro Ziel) lässt sich jederzeit ausschalten, um den
Auto-Send-Poller stillzulegen, ohne die Zielgruppen-Konfiguration zu verlieren - so lässt sich die
Einrichtung (Verknüpfung, Zielgruppen eintragen) in Ruhe vorbereiten, bevor tatsächlich etwas an
die Band-Gruppe geschickt wird. Für einen risikofreien ersten Test empfiehlt sich eine private
Test-Gruppe mit nur dem eigenen Account.
