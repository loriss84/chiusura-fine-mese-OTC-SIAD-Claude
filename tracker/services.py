"""
Business-logic services for Month-End Close Tracker.
"""

from datetime import timedelta
from collections import defaultdict
from django.db import transaction
from django.utils import timezone

from .models import (
    TemplateVersion, TemplateVersionStatus,
    Dependency, CloseCycle, CycleStatus,
    ProcessRun, TaskRun, TaskRunLog, AuditEventType,
    Country, ProcessAssignment, TaskAssignment, RunStatus,
    NotificationOutbox,
)


# ---------------------------------------------------------------------------
# DAG validation
# ---------------------------------------------------------------------------

def _has_cycle(edges):
    """
    Detect cycle in a directed graph represented as {node_id: [successor_ids]}.
    Returns True if a cycle exists.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = defaultdict(int)

    def dfs(node):
        color[node] = GRAY
        for neighbour in edges.get(node, []):
            if color[neighbour] == GRAY:
                return True
            if color[neighbour] == WHITE and dfs(neighbour):
                return True
        color[node] = BLACK
        return False

    for node in list(edges.keys()):
        if color[node] == WHITE:
            if dfs(node):
                return True
    return False


def validate_no_dependency_cycle(version: TemplateVersion):
    """
    Raise ValueError if the dependency graph for *version* contains a cycle.
    """
    deps = Dependency.objects.filter(version=version).values_list("from_process_id", "to_process_id")
    edges = defaultdict(list)
    for frm, to in deps:
        edges[frm].append(to)
    if _has_cycle(edges):
        raise ValueError("Dependency graph contains a cycle. Please remove circular dependencies before publishing.")


# ---------------------------------------------------------------------------
# Publish template version
# ---------------------------------------------------------------------------

@transaction.atomic
def publish_template_version(version: TemplateVersion, published_by):
    """
    Validate and publish a DRAFT TemplateVersion.
    Raises ValueError on validation errors.
    """
    if version.status != TemplateVersionStatus.DRAFT:
        raise ValueError(f"Version {version} is already {version.status}, cannot publish again.")

    validate_no_dependency_cycle(version)

    version.status = TemplateVersionStatus.PUBLISHED
    version.published_at = timezone.now()
    version.save(update_fields=["status", "published_at"])
    return version


# ---------------------------------------------------------------------------
# Create close cycle + generate runs
# ---------------------------------------------------------------------------

@transaction.atomic
def create_close_cycle(period: str, final_deadline, template_version: TemplateVersion, created_by) -> CloseCycle:
    """
    Create a CloseCycle and generate ProcessRun + TaskRun for all countries.

    :param period: "YYYY-MM" string
    :param final_deadline: date object
    :param template_version: a PUBLISHED TemplateVersion
    :param created_by: User
    :returns: the newly created CloseCycle
    """
    if not template_version.is_published:
        raise ValueError("Can only create a cycle from a PUBLISHED template version.")

    cycle = CloseCycle.objects.create(
        period=period,
        final_deadline=final_deadline,
        template_version=template_version,
        status=CycleStatus.OPEN,
        created_by=created_by,
    )

    countries = Country.objects.all()
    process_assignments = {
        (pa.country_id, pa.process_template_id): pa.owner_user
        for pa in ProcessAssignment.objects.filter(version=template_version).select_related("owner_user")
    }
    task_assignments = {
        (ta.country_id, ta.task_template_id): ta.owner_user
        for ta in TaskAssignment.objects.filter(version=template_version).select_related("owner_user")
    }

    process_templates = list(
        template_version.processes.select_related("macro").prefetch_related("tasks")
    )

    for country in countries:
        for pt in process_templates:
            owner = process_assignments.get((country.id, pt.id))
            pr = ProcessRun.objects.create(
                cycle=cycle,
                country=country,
                process_template=pt,
                owner_user=owner,
                status=RunStatus.NOT_STARTED,
                completion_percent=0,
            )
            for tt in pt.tasks.all():
                task_owner = task_assignments.get((country.id, tt.id)) or owner
                due_calculated = final_deadline + timedelta(days=tt.due_offset_days)
                TaskRun.objects.create(
                    process_run=pr,
                    task_template=tt,
                    owner_user=task_owner,
                    status=RunStatus.NOT_STARTED,
                    due_date_calculated=due_calculated,
                )

    return cycle


# ---------------------------------------------------------------------------
# Update task run (status, comment, override)
# ---------------------------------------------------------------------------

@transaction.atomic
def update_task_run(task_run, user, new_status=None, comment=None, due_date_override=None, override_reason=None):
    """
    Update a TaskRun with audit logging.
    Caller must have already checked permissions.
    """
    from .permissions import can_override_due_date

    changed = False

    if new_status and new_status != task_run.status:
        log = TaskRunLog(
            task_run=task_run,
            event_type=AuditEventType.STATUS_CHANGE,
            old_value=task_run.status,
            new_value=new_status,
            changed_by=user,
        )
        task_run.status = new_status
        changed = True
        log.save()

    if comment:
        task_run.last_comment = comment
        TaskRunLog.objects.create(
            task_run=task_run,
            event_type=AuditEventType.COMMENT,
            new_value=comment,
            changed_by=user,
        )
        changed = True

    if due_date_override is not None:
        if not can_override_due_date(user, task_run):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to override the due date.")
        if not override_reason:
            raise ValueError("override_reason is required when overriding due date.")
        old_override = str(task_run.due_date_override) if task_run.due_date_override else ""
        task_run.due_date_override = due_date_override
        task_run.override_reason = override_reason
        TaskRunLog.objects.create(
            task_run=task_run,
            event_type=AuditEventType.DUE_DATE_OVERRIDE,
            old_value=old_override,
            new_value=str(due_date_override),
            comment=override_reason,
            changed_by=user,
        )
        changed = True

    if changed:
        task_run.save()
        task_run.process_run.refresh_status()

    return task_run


# ---------------------------------------------------------------------------
# Notification helpers (outbox only – no real delivery)
# ---------------------------------------------------------------------------

def notify_user(recipient, subject, body):
    NotificationOutbox.objects.create(recipient=recipient, subject=subject, body=body)


def send_overdue_reminders(cycle: CloseCycle, sender):
    """
    Store overdue-reminder notifications in the outbox for a cycle.
    """
    from django.utils import timezone
    today = timezone.now().date()
    overdue_runs = (
        TaskRun.objects.filter(process_run__cycle=cycle)
        .select_related("owner_user", "process_run__country", "task_template")
        .exclude(status__in=[RunStatus.DONE, RunStatus.NA])
    )
    notified = set()
    for tr in overdue_runs:
        if tr.effective_due_date < today and tr.owner_user and tr.owner_user.id not in notified:
            notify_user(
                recipient=tr.owner_user,
                subject=f"[OVERDUE] Task '{tr.task_template.name}' in {tr.process_run.country.code}",
                body=(
                    f"Task '{tr.task_template.name}' for cycle {cycle.period} "
                    f"in {tr.process_run.country.name} is overdue "
                    f"(due {tr.effective_due_date}).\n"
                    f"Please update the status as soon as possible."
                ),
            )
            notified.add(tr.owner_user.id)
