"""Optional web-configuration convention (see x32-recorder's plugins/PLUGIN_DEVELOPMENT.md) -
mixes describe_model_fields() for the plain settings with hand-written list/action/qrcode fields
(target list, signal-cli install/update, device-linking), per PLUGIN_DEVELOPMENT.md's "Erweiterte
Feldtypen" section."""
from recorder.plugin_support import describe_model_fields, model_instance_values

from . import linking
from . import signal_cli_manager
from .models import MessengerSettings, SignalTarget

FIELDS = ["enabled", "device_name", "linked"]
READONLY_FIELDS = {"linked"}

TARGET_ITEM_FIELDS = [
    {"name": "name", "label": "Name", "type": "string"},
    {"name": "group_id", "label": "Signal-Gruppen-ID", "type": "string"},
    {"name": "enabled", "label": "Aktiv", "type": "bool"},
    {"name": "auto_send_on_clip_export", "label": "Auto-Senden nach Schnitt", "type": "bool"},
]


def get_config_schema():
    fields = describe_model_fields(MessengerSettings, FIELDS, READONLY_FIELDS)
    fields.append({
        "name": "signal_cli_installed_version", "label": "signal-cli Version", "type": "string",
        "readonly": True, "help_text": ""
    })
    fields.append({
        "name": "signal_cli_latest_version", "label": "Neueste verfügbare Version", "type": "string",
        "readonly": True, "help_text":
            "Kann neuer sein als die installierte Version, falls für diese Plattform kein "
            "gebündeltes JRE verfügbar ist und die installierte Version deshalb älter ausfällt."
    })
    fields.append({
        "name": "jre_installed_version", "label": "Gebündelte Java-Version", "type": "string",
        "readonly": True, "help_text":
            "Wird automatisch mit signal-cli installiert (Temurin/Adoptium) - unabhängig von einer "
            "eventuell system-weit installierten Java-Laufzeitumgebung. '-' bedeutet: kein "
            "Bündel für diese Plattform verfügbar, es wird stattdessen System-Java verwendet."
    })
    fields.append({
        "name": "check_signal_cli_update", "label": "Nach Update suchen", "type": "action", "help_text": ""
    })
    fields.append({
        "name": "install_signal_cli", "label": "signal-cli installieren/aktualisieren", "type": "action",
        "help_text": "Lädt die neueste signal-cli-Version von GitHub (AsamK/signal-cli) sowie eine "
                     "passende Java-Laufzeitumgebung (Temurin/Adoptium) herunter, falls für diese "
                     "Plattform verfügbar.",
    })
    fields.append({
        "name": "targets", "label": "Ziel-Gruppen", "type": "list", "help_text":
            "Signal-Gruppen-ID über 'Gruppen laden' (nach dem Verknüpfen) ermitteln oder von Hand eintragen.",
        "item_fields": TARGET_ITEM_FIELDS,
    })
    fields.append({
        "name": "link", "label": "Signal verknüpfen", "type": "action",
        "help_text": "Startet die Geräte-Verknüpfung - danach unten den QR-Code mit der Signal-App scannen "
                     "(Einstellungen -> Verknüpfte Geräte -> Gerät verknüpfen).",
    })
    fields.append({
        "name": "link_status", "label": "Verknüpfungsstatus", "type": "string", "readonly": True, "help_text": ""
    })
    fields.append({
        "name": "link_error", "label": "Fehlermeldung", "type": "string", "readonly": True,
        "help_text": "Nur gesetzt, wenn die letzte Verknüpfung fehlgeschlagen ist."
    })
    fields.append({
        "name": "link_qr_svg", "label": "QR-Code", "type": "qrcode", "help_text": ""
    })
    return {"fields": fields}


def _targets_as_dicts():
    return [
        {
            "id": t.id,
            "name": t.name,
            "group_id": t.group_id,
            "enabled": t.enabled,
            "auto_send_on_clip_export": t.auto_send_on_clip_export,
        }
        for t in SignalTarget.objects.all()
    ]


def get_config_values():
    values = model_instance_values(MessengerSettings.get_solo(), FIELDS)
    values["signal_cli_installed_version"] = signal_cli_manager.installed_version() or "nicht installiert"
    values["jre_installed_version"] = signal_cli_manager.installed_jre_version() or "-"
    last_check = signal_cli_manager.get_last_check()
    values["signal_cli_latest_version"] = last_check["latest_version"] or last_check["error"] or "noch nicht geprüft"
    values["targets"] = _targets_as_dicts()
    link_state = linking.get_state()
    values["link_status"] = link_state["status"]
    values["link_error"] = link_state["error"] if link_state["status"] == "failed" else ""
    values["link_qr_svg"] = link_state["qr_svg"] if link_state["status"] == "waiting_scan" else None
    return values


def _save_targets(rows):
    """Diffs the submitted rows against the DB: rows with an id are updated, rows without one are
    created, and any existing row not present in the submission is deleted - the frontend always
    submits the complete list (see SettingsPage.vue's 'list' field type)."""
    seen_ids = set()
    for row in rows or []:
        defaults = {
            "name": row.get("name", ""),
            "group_id": row.get("group_id", ""),
            "enabled": bool(row.get("enabled")),
            "auto_send_on_clip_export": bool(row.get("auto_send_on_clip_export")),
        }
        target_id = row.get("id")
        if target_id:
            SignalTarget.objects.filter(id=target_id).update(**defaults)
            seen_ids.add(target_id)
        else:
            target = SignalTarget.objects.create(**defaults)
            seen_ids.add(target.id)
    SignalTarget.objects.exclude(id__in=seen_ids).delete()


def update_config_values(data):
    settings = MessengerSettings.get_solo()
    for key, value in data.items():
        if key in FIELDS and key not in READONLY_FIELDS:
            setattr(settings, key, value)
    settings.full_clean(exclude=list(READONLY_FIELDS))
    settings.save()
    if "targets" in data:
        _save_targets(data["targets"])
    return get_config_values()


def handle_config_action(action_name, data):
    if action_name == "link":
        settings = MessengerSettings.get_solo()
        linking.start_linking(settings.device_name)
    elif action_name == "check_signal_cli_update":
        signal_cli_manager.refresh_latest_version()
    elif action_name == "install_signal_cli":
        signal_cli_manager.install_latest()
    else:
        raise ValueError("Diese Aktion gibt es nicht")
