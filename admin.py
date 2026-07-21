from django.contrib import admin

from .models import MessengerSettings, SignalTarget, SendLog


@admin.register(MessengerSettings)
class MessengerSettingsAdmin(admin.ModelAdmin):
    readonly_fields = ("linked",)


@admin.register(SignalTarget)
class SignalTargetAdmin(admin.ModelAdmin):
    list_display = ("name", "group_id", "enabled", "auto_send_on_clip_export")


@admin.register(SendLog)
class SendLogAdmin(admin.ModelAdmin):
    list_display = ("recording", "target", "sent_at", "success")
    readonly_fields = ("recording", "target", "sent_at", "success", "error")
