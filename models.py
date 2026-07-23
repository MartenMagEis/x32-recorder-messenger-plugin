from django.db import models


class MessengerSettings(models.Model):
    """Singleton config, mirrors x32-recorder-ups-plugin's get_solo() pattern - editable via
    x32-recorder's Settings page (see plugin_config.py) or Django Admin."""

    enabled = models.BooleanField(
        "Versand aktiviert",
        default=False,
        help_text="Nicht zu verwechseln mit dem 'Aktiviert' oben in der Plugin-Zeile (das lädt nur "
                   "das Plugin) - hier: solange aus, sendet dieses Plugin nichts, auch nicht "
                   "automatisch nach dem Schneiden, selbst wenn Ziel-Gruppen konfiguriert sind."
    )
    device_name = models.CharField(
        "Geräte-Name",
        max_length=64, default="x32-recorder",
        help_text="Name, unter dem dieses Gerät in der Signal-App des verknüpften Kontos erscheint."
    )
    linked = models.BooleanField(
        "Verknüpft",
        default=False,
        help_text="Nur eine Anzeige, kein Schalter - wird beim erfolgreichen Abschluss der "
                   "Geräte-Verknüpfung automatisch gesetzt (siehe linking.py)."
    )

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Messenger-Plugin-Einstellungen"


class SignalTarget(models.Model):
    """Eine konfigurierte Signal-Zielgruppe - mehrere pro Installation möglich. group_id ist keine
    geheime Anmeldeinformation (ohne verknüpftes Gerät nutzlos), siehe README."""

    name = models.CharField("Name", max_length=128)
    group_id = models.CharField(
        "Signal-Gruppen-ID", max_length=256, blank=True, default="",
        help_text="Über 'Gruppen laden' im Verknüpfen-Schritt auswählen, oder von Hand eintragen."
    )
    enabled = models.BooleanField("Aktiv", default=True)
    auto_send_on_clip_export = models.BooleanField(
        "Auto-Senden nach Schnitt", default=False,
        help_text="Frisch geschnittene Song-Clips automatisch an dieses Ziel senden, sobald ihr "
                   "Browser-Mix fertig ist."
    )

    def __str__(self):
        return self.name


class SendLog(models.Model):
    """Historie + Dedup-Grundlage für den Auto-Send-Poller (siehe auto_send.py) - kein
    Retry-Backoff in dieser ersten Version, ein Fehlschlag wird beim nächsten Tick erneut
    versucht, solange kein erfolgreicher Eintrag für dasselbe (recording, target)-Paar existiert."""

    recording = models.ForeignKey(
        "recorder.Recording", on_delete=models.CASCADE, related_name="messenger_send_logs"
    )
    target = models.ForeignKey(SignalTarget, on_delete=models.CASCADE, related_name="send_logs")
    sent_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=False)
    error = models.CharField(max_length=512, blank=True, default="")

    def __str__(self):
        return f"{self.recording_id} -> {self.target.name} ({'ok' if self.success else 'error'})"
