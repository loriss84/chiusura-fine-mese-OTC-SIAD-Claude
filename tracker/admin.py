"""
Django admin configuration for Month-End Close Tracker.
"""

from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Country, Membership, UserGlobalRole,
    TemplateFamily, TemplateVersion, MacroTemplate, ProcessTemplate,
    TaskTemplate, Dependency, ProcessAssignment, TaskAssignment,
    CloseCycle, ProcessRun, TaskRun, Evidence, TaskRunLog,
    NotificationOutbox,
)
from .services import publish_template_version


# ---------------------------------------------------------------------------
# Org
# ---------------------------------------------------------------------------

@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "country", "role")
    list_filter = ("role", "country")
    autocomplete_fields = ("user", "country")


@admin.register(UserGlobalRole)
class UserGlobalRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)
    autocomplete_fields = ("user",)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

class MacroTemplateInline(admin.TabularInline):
    model = MacroTemplate
    extra = 1


class DependencyInline(admin.TabularInline):
    model = Dependency
    fk_name = "version"
    extra = 1


@admin.register(TemplateFamily)
class TemplateFamilyAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(TemplateVersion)
class TemplateVersionAdmin(admin.ModelAdmin):
    list_display = ("family", "version_number", "status", "created_by", "created_at", "published_at")
    list_filter = ("status", "family")
    readonly_fields = ("created_at", "published_at", "created_by")
    inlines = [MacroTemplateInline, DependencyInline]
    actions = ["publish_versions"]

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Publish selected draft versions")
    def publish_versions(self, request, queryset):
        published = 0
        errors = []
        for version in queryset:
            try:
                publish_template_version(version, request.user)
                published += 1
            except ValueError as e:
                errors.append(f"{version}: {e}")
        if published:
            self.message_user(request, f"{published} version(s) published.")
        for err in errors:
            self.message_user(request, err, level="error")


@admin.register(MacroTemplate)
class MacroTemplateAdmin(admin.ModelAdmin):
    list_display = ("version", "name", "order")
    list_filter = ("version__family",)


class TaskTemplateInline(admin.TabularInline):
    model = TaskTemplate
    extra = 1


@admin.register(ProcessTemplate)
class ProcessTemplateAdmin(admin.ModelAdmin):
    list_display = ("version", "macro", "name", "order", "is_gating")
    list_filter = ("version__family", "macro")
    inlines = [TaskTemplateInline]


@admin.register(TaskTemplate)
class TaskTemplateAdmin(admin.ModelAdmin):
    list_display = ("process", "name", "due_offset_days", "requires_evidence")
    list_filter = ("version__family",)


@admin.register(Dependency)
class DependencyAdmin(admin.ModelAdmin):
    list_display = ("version", "from_process", "to_process")


@admin.register(ProcessAssignment)
class ProcessAssignmentAdmin(admin.ModelAdmin):
    list_display = ("version", "country", "process_template", "owner_user")
    list_filter = ("country", "version__family")


@admin.register(TaskAssignment)
class TaskAssignmentAdmin(admin.ModelAdmin):
    list_display = ("version", "country", "task_template", "owner_user")
    list_filter = ("country", "version__family")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

@admin.register(CloseCycle)
class CloseCycleAdmin(admin.ModelAdmin):
    list_display = ("period", "final_deadline", "template_version", "status", "created_by", "created_at")
    list_filter = ("status",)
    readonly_fields = ("created_at", "created_by")

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class TaskRunInline(admin.TabularInline):
    model = TaskRun
    extra = 0
    readonly_fields = ("status", "effective_due_date", "is_overdue_display")

    def effective_due_date(self, obj):
        return obj.effective_due_date

    def is_overdue_display(self, obj):
        return obj.is_overdue
    is_overdue_display.boolean = True
    is_overdue_display.short_description = "Overdue"


@admin.register(ProcessRun)
class ProcessRunAdmin(admin.ModelAdmin):
    list_display = ("cycle", "country", "process_template", "status", "completion_percent", "owner_user")
    list_filter = ("country", "status", "cycle")


class EvidenceInline(admin.TabularInline):
    model = Evidence
    extra = 0
    readonly_fields = ("uploaded_by", "uploaded_at")


class TaskRunLogInline(admin.TabularInline):
    model = TaskRunLog
    extra = 0
    readonly_fields = ("event_type", "old_value", "new_value", "comment", "changed_by", "changed_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(TaskRun)
class TaskRunAdmin(admin.ModelAdmin):
    list_display = (
        "task_template", "process_run", "status",
        "due_date_calculated", "due_date_override", "owner_user",
    )
    list_filter = ("status", "process_run__country", "process_run__cycle")
    readonly_fields = ("due_date_calculated", "completed_at", "last_updated_at")
    inlines = [EvidenceInline, TaskRunLogInline]


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("title", "task_run", "uploaded_by", "uploaded_at")
    readonly_fields = ("uploaded_at",)


@admin.register(TaskRunLog)
class TaskRunLogAdmin(admin.ModelAdmin):
    list_display = ("task_run", "event_type", "old_value", "new_value", "changed_by", "changed_at")
    list_filter = ("event_type",)
    readonly_fields = ("changed_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(NotificationOutbox)
class NotificationOutboxAdmin(admin.ModelAdmin):
    list_display = ("recipient", "subject", "is_sent", "created_at", "sent_at")
    list_filter = ("is_sent",)
    readonly_fields = ("created_at",)
