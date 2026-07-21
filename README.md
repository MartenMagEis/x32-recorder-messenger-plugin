# x32-recorder Messenger-Plugin

Ein [x32-recorder](https://github.com/tobire42/x32-recorder)-Plugin (Art `django_app`): verschickt den
Browser-Mix (`playback.mp3`) einer Aufnahme oder eines ausgeschnittenen Song-Clips an konfigurierbare
Signal-Gruppen - manuell per "Senden"-Button oder automatisch direkt nach dem Schneiden.

Nutzt [signal-cli](https://github.com/AsamK/signal-cli) über das eigene Signal-Konto, als Zweitgerät
verknüpft (QR-Code-Scan wie bei Signal Desktop) - keine eigene Telefonnummer nötig.

**Status**: in Entwicklung, noch nicht funktionsfähig.

## Voraussetzung

`signal-cli` (Java-basiert, braucht ein JRE) muss auf der Maschine installiert sein, auf der
x32-recorders Django-Prozess läuft.

## Geheimdaten

Alle Signal-Sitzungs-/Identitätsdaten liegen ausschließlich in `signal-cli-data/` innerhalb dieses
Plugin-Ordners und werden über keinen API-Endpunkt ausgelesen oder angezeigt - Ausnahme ist der
QR-Code während der einmaligen Geräte-Verknüpfung selbst. Mehr dazu in x32-recorders
`plugins/PLUGIN_DEVELOPMENT.md`.
