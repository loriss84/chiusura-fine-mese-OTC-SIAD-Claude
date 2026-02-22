"""
Month-End Close Tracker – data models.
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

User = get_user_model()

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class CountryRole(models.TextChoices):
    COUNTRY_CFO = "COUNTRY_CFO", "Country CFO"
    PROCESS_OWNER = "PROCESS_OWNER", "Process Owner"


class GlobalRole(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    HQ_PROJECT_OWNER = "HQ_PROJECT_OWNER", "HQ Project Owner"


# ---------------------------------------------------------------------------
# Org
# ---------------------------------------------------------------------------

class Country(models.Model):
    code = models.CharField(max_length=3, unique=True)  # e.g. IT, AT, DE
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "countries"

    def __str__(self):
        return f"{self.code} – {self.name}"


class Membership(models.Model):
    """Country-scoped role for a user."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=CountryRole.choices)

    class Meta:
        unique_together = [("user", "country", "role")]

    def __str__(self):
        return f"{self.user.username} / {self.country.code} / {self.role}"


class UserGlobalRole(models.Model):
    """Global (cross-country) role."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="global_role")
    role = models.CharField(max_length=20, choices=GlobalRole.choices)

    def __str__(self):
        return f"{self.user.username} / {self.role}"


# ---------------------------------------------------------------------------
# Template family & versioning
# ---------------------------------------------------------------------------

class TemplateFamily(models.Model):
    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "template families"

    def __str__(self):
        return self.name


class TemplateVersionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"


class TemplateVersion(models.Model):
    family = models.ForeignKey(TemplateFamily, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=TemplateVersionStatus.choices,
        default=TemplateVersionStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="template_versions_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("family", "version_number")]
        ordering = ["family", "-version_number"]

    def __str__(self):
        return f"{self.family.name} v{self.version_number} ({self.status})"

    @property
    def is_published(self):
        return self.status == TemplateVersionStatus.PUBLISHED


class MacroTemplate(models.Model):
    version = models.ForeignKey(TemplateVersion, on_delete=models.CASCADE, related_name="macros")
    name = models.CharField(max_length=100)  # AR, AP, GL, …
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["version", "order", "name"]
        unique_together = [("version", "name")]

    def __str__(self):
        return f"{self.version} / {self.name}"


class ProcessTemplate(models.Model):
    version = models.ForeignKey(TemplateVersion, on_delete=models.CASCADE, related_name="processes")
    macro = models.ForeignKey(MacroTemplate, on_delete=models.CASCADE, related_name="processes")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    is_gating = models.BooleanField(
        default=False, help_text="If True, downstream processes wait for this one."
    )

    class Meta:
        ordering = ["version", "macro", "order", "name"]
        unique_together = [("version", "macro", "name")]

    def __str__(self):
        return f"{self.macro.name} / {self.name}"


class TaskTemplate(models.Model):
    version = models.ForeignKey(TemplateVersion, on_delete=models.CASCADE, related_name="tasks")
    process = models.ForeignKey(ProcessTemplate, on_delete=models.CASCADE, related_name="tasks")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    due_offset_days = models.IntegerField(
        default=0,
        help_text="Days relative to final_deadline (negative = before, 0 = on deadline).",
    )
    requires_evidence = models.BooleanField(default=False)

    class Meta:
        ordering = ["version", "process", "order", "name"]

    def __str__(self):
        return f"{self.process.name} / {self.name}"


class Dependency(models.Model):
    """from_process must be DONE/NA before to_process can start (MVP: warning only)."""
    version = models.ForeignKey(TemplateVersion, on_delete=models.CASCADE, related_name="dependencies")
    from_process = models.ForeignKey(
        ProcessTemplate, on_delete=models.CASCADE, related_name="dependencies_as_predecessor"
    )
    to_process = models.ForeignKey(
        ProcessTemplate, on_delete=models.CASCADE, related_name="dependencies_as_successor"
    )

    class Meta:
        unique_together = [("version", "from_process", "to_process")]

    def __str__(self):
        return f"{self.from_process.name} → {self.to_process.name}"


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

class ProcessAssignment(models.Model):
    version = models.ForeignKey(
        TemplateVersion, on_delete=models.CASCADE, related_name="process_assignments"
    )
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="process_assignments")
    process_template = models.ForeignKey(
        ProcessTemplate, on_delete=models.CASCADE, related_name="assignments"
    )
    owner_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="process_assignments"
    )

    class Meta:
        unique_together = [("version", "country", "process_template")]

    def __str__(self):
        return f"{self.country.code} / {self.process_template.name}"


class TaskAssignment(models.Model):
    version = models.ForeignKey(
        TemplateVersion, on_delete=models.CASCADE, related_name="task_assignments"
    )
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="task_assignments")
    task_template = models.ForeignKey(
        TaskTemplate, on_delete=models.CASCADE, related_name="assignments"
    )
    owner_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="task_assignments"
    )

    class Meta:
        unique_together = [("version", "country", "task_template")]

    def __str__(self):
        return f"{self.country.code} / {self.task_template.name}"


# ---------------------------------------------------------------------------
# Run models
# ---------------------------------------------------------------------------

class CycleStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    OPEN = "OPEN", "Open"
    LOCKED = "LOCKED", "Locked"
    ARCHIVED = "ARCHIVED", "Archived"


class CloseCycle(models.Model):
    period = models.CharField(max_length=7, help_text="YYYY-MM, e.g. 2026-03")
    final_deadline = models.DateField()
    template_version = models.ForeignKey(
        TemplateVersion, on_delete=models.PROTECT, related_name="cycles"
    )
    status = models.CharField(max_length=10, choices=CycleStatus.choices, default=CycleStatus.DRAFT)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="cycles_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period"]
        unique_together = [("period", "template_version")]

    def __str__(self):
        return f"Cycle {self.period} (deadline {self.final_deadline})"


class RunStatus(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not Started"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    DONE = "DONE", "Done"
    BLOCKED = "BLOCKED", "Blocked"
    NA = "NA", "N/A"


class ProcessRun(models.Model):
    cycle = models.ForeignKey(CloseCycle, on_delete=models.CASCADE, related_name="process_runs")
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="process_runs")
    process_template = models.ForeignKey(
        ProcessTemplate, on_delete=models.PROTECT, related_name="runs"
    )
    owner_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="process_runs"
    )
    # Derived – updated by refresh_status()
    status = models.CharField(max_length=15, choices=RunStatus.choices, default=RunStatus.NOT_STARTED)
    completion_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        unique_together = [("cycle", "country", "process_template")]
        ordering = ["country", "process_template__macro__order", "process_template__order"]

    def __str__(self):
        return f"{self.cycle} / {self.country.code} / {self.process_template.name}"

    def refresh_status(self):
        """Recompute aggregated status and completion from child TaskRuns."""
        runs = list(self.task_runs.all())
        if not runs:
            self.status = RunStatus.NOT_STARTED
            self.completion_percent = 0
        else:
            statuses = [r.status for r in runs]
            done_na = sum(1 for s in statuses if s in (RunStatus.DONE, RunStatus.NA))
            total = len(statuses)
            self.completion_percent = round(done_na / total * 100, 2)
            if RunStatus.BLOCKED in statuses:
                self.status = RunStatus.BLOCKED
            elif done_na == total:
                self.status = RunStatus.DONE
            elif RunStatus.IN_PROGRESS in statuses or RunStatus.DONE in statuses:
                self.status = RunStatus.IN_PROGRESS
            else:
                self.status = RunStatus.NOT_STARTED
        self.save(update_fields=["status", "completion_percent"])


class TaskRun(models.Model):
    process_run = models.ForeignKey(ProcessRun, on_delete=models.CASCADE, related_name="task_runs")
    task_template = models.ForeignKey(
        TaskTemplate, on_delete=models.PROTECT, related_name="runs"
    )
    owner_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="task_runs"
    )
    status = models.CharField(max_length=15, choices=RunStatus.choices, default=RunStatus.NOT_STARTED)
    due_date_calculated = models.DateField()
    due_date_override = models.DateField(null=True, blank=True)
    override_reason = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_updated_at = models.DateTimeField(auto_now=True)
    last_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["process_run", "task_template__order"]

    def __str__(self):
        return f"{self.process_run} / {self.task_template.name}"

    @property
    def country(self):
        return self.process_run.country

    @property
    def effective_due_date(self):
        return self.due_date_override if self.due_date_override else self.due_date_calculated

    @property
    def is_overdue(self):
        if self.status in (RunStatus.DONE, RunStatus.NA):
            return False
        return self.effective_due_date < timezone.now().date()

    def clean(self):
        if self.due_date_override and not self.override_reason:
            raise ValidationError("override_reason is required when due_date_override is set.")

    def save(self, *args, **kwargs):
        if self.status in (RunStatus.DONE, RunStatus.NA) and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status not in (RunStatus.DONE, RunStatus.NA):
            self.completed_at = None
        super().save(*args, **kwargs)


class Evidence(models.Model):
    task_run = models.ForeignKey(TaskRun, on_delete=models.CASCADE, related_name="evidences")
    title = models.CharField(max_length=200)
    link_url = models.URLField()
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="evidences_uploaded"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class AuditEventType(models.TextChoices):
    STATUS_CHANGE = "STATUS_CHANGE", "Status Change"
    DUE_DATE_OVERRIDE = "DUE_DATE_OVERRIDE", "Due Date Override"
    COMMENT = "COMMENT", "Comment"
    OWNER_CHANGE = "OWNER_CHANGE", "Owner Change"


class TaskRunLog(models.Model):
    task_run = models.ForeignKey(TaskRun, on_delete=models.CASCADE, related_name="logs")
    event_type = models.CharField(max_length=20, choices=AuditEventType.choices)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    comment = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="audit_logs"
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.task_run} / {self.event_type} @ {self.changed_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Notification outbox (MVP: no real sending)
# ---------------------------------------------------------------------------

class NotificationOutbox(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    subject = models.CharField(max_length=300)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    is_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"→ {self.recipient.username}: {self.subject}"
