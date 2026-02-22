"""
Views for Month-End Close Tracker.
"""

import csv
from datetime import date
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views as auth_views
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden
from django.db.models import Count, Q
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_POST

from .models import (
    Country, CloseCycle, ProcessRun, TaskRun, Evidence,
    RunStatus, CycleStatus, TemplateVersion, TemplateVersionStatus,
    TaskRunLog, AuditEventType,
)
from .permissions import (
    is_hq, is_admin, is_cfo_for_country, can_update_task,
    can_override_due_date, get_accessible_countries, assert_country_access,
    require_hq,
)
from .services import create_close_cycle, update_task_run, publish_template_version


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginView(auth_views.LoginView):
    template_name = "tracker/login.html"


# ---------------------------------------------------------------------------
# Dashboard redirect
# ---------------------------------------------------------------------------

@login_required
def dashboard_redirect(request):
    if is_hq(request.user):
        return redirect("hq_dashboard")
    return redirect("country_dashboard")


# ---------------------------------------------------------------------------
# HQ Dashboard
# ---------------------------------------------------------------------------

@login_required
@require_hq
def hq_dashboard(request):
    cycles = CloseCycle.objects.select_related("template_version__family").order_by("-period")
    active_cycle = cycles.filter(status=CycleStatus.OPEN).first()

    countries = Country.objects.all()
    macro_names = []
    heatmap = []
    overdue_tasks = []
    blocked_tasks = []

    if active_cycle:
        today = timezone.now().date()
        pr_qs = (
            ProcessRun.objects.filter(cycle=active_cycle)
            .select_related("country", "process_template__macro")
            .order_by("country__code", "process_template__macro__order")
        )

        country_macro_map = {}
        for pr in pr_qs:
            cc = pr.country.code
            mn = pr.process_template.macro.name
            country_macro_map.setdefault(cc, {}).setdefault(mn, []).append(pr.status)

        macro_names = list(
            active_cycle.template_version.macros.values_list("name", flat=True).order_by("order")
        )

        for country in countries:
            macro_cells = []
            for mn in macro_names:
                statuses = country_macro_map.get(country.code, {}).get(mn, [])
                if not statuses:
                    cell_status = "NONE"
                elif RunStatus.BLOCKED in statuses:
                    cell_status = RunStatus.BLOCKED
                elif all(s in (RunStatus.DONE, RunStatus.NA) for s in statuses):
                    cell_status = RunStatus.DONE
                elif any(s == RunStatus.IN_PROGRESS for s in statuses):
                    cell_status = RunStatus.IN_PROGRESS
                else:
                    cell_status = RunStatus.NOT_STARTED
                macro_cells.append({"name": mn, "status": cell_status})
            heatmap.append({"country": country, "macros": macro_cells})

        overdue_tasks = (
            TaskRun.objects.filter(process_run__cycle=active_cycle)
            .exclude(status__in=[RunStatus.DONE, RunStatus.NA])
            .select_related(
                "process_run__country", "task_template",
                "process_run__process_template", "owner_user",
            )
            .order_by("due_date_calculated")
        )
        overdue_tasks = [t for t in overdue_tasks if t.effective_due_date < today][:20]

        blocked_tasks = (
            TaskRun.objects.filter(process_run__cycle=active_cycle, status=RunStatus.BLOCKED)
            .select_related(
                "process_run__country", "task_template",
                "process_run__process_template", "owner_user",
            )[:20]
        )

    return render(request, "tracker/hq_dashboard.html", {
        "cycles": cycles[:10],
        "active_cycle": active_cycle,
        "countries": countries,
        "macro_names": macro_names,
        "heatmap": heatmap,
        "overdue_tasks": overdue_tasks,
        "blocked_tasks": blocked_tasks,
    })


# ---------------------------------------------------------------------------
# Country Dashboard
# ---------------------------------------------------------------------------

@login_required
def country_dashboard(request, country_code=None):
    if country_code:
        country = get_object_or_404(Country, code=country_code.upper())
        assert_country_access(request.user, country)
    else:
        countries = get_accessible_countries(request.user)
        if not countries.exists():
            return render(request, "tracker/no_country.html")
        country = countries.first()

    active_cycle = CloseCycle.objects.filter(status=CycleStatus.OPEN).order_by("-period").first()
    process_runs = []
    today = timezone.now().date()

    if active_cycle:
        process_runs = (
            ProcessRun.objects.filter(cycle=active_cycle, country=country)
            .select_related("process_template__macro", "owner_user")
            .prefetch_related("task_runs")
            .order_by("process_template__macro__order", "process_template__order")
        )

    accessible_countries = get_accessible_countries(request.user)

    return render(request, "tracker/country_dashboard.html", {
        "country": country,
        "active_cycle": active_cycle,
        "process_runs": list(process_runs),
        "today": today,
        "accessible_countries": accessible_countries,
        "is_cfo": is_cfo_for_country(request.user, country),
    })


# ---------------------------------------------------------------------------
# My Tasks
# ---------------------------------------------------------------------------

@login_required
def my_tasks(request):
    today = timezone.now().date()
    task_runs = (
        TaskRun.objects.filter(owner_user=request.user)
        .select_related(
            "process_run__cycle", "process_run__country",
            "process_run__process_template__macro", "task_template",
        )
        .exclude(status__in=[RunStatus.DONE, RunStatus.NA])
        .order_by("due_date_calculated")
    )
    return render(request, "tracker/my_tasks.html", {
        "task_runs": list(task_runs),
        "today": today,
        "RunStatus": RunStatus,
    })


# ---------------------------------------------------------------------------
# Cycle list + create
# ---------------------------------------------------------------------------

@login_required
def cycle_list(request):
    cycles = CloseCycle.objects.select_related("template_version__family", "created_by").order_by("-period")
    return render(request, "tracker/cycle_list.html", {"cycles": cycles})


@login_required
@require_hq
def cycle_create(request):
    published_versions = TemplateVersion.objects.filter(
        status=TemplateVersionStatus.PUBLISHED
    ).select_related("family").order_by("-published_at")

    if request.method == "POST":
        period = request.POST.get("period", "").strip()
        final_deadline_str = request.POST.get("final_deadline", "").strip()
        version_id = request.POST.get("template_version")
        try:
            final_deadline = date.fromisoformat(final_deadline_str)
            version = TemplateVersion.objects.get(pk=version_id)
            cycle = create_close_cycle(period, final_deadline, version, request.user)
            messages.success(request, f"Cycle {cycle.period} created and runs generated.")
            return redirect("cycle_list")
        except Exception as e:
            messages.error(request, str(e))

    return render(request, "tracker/cycle_create.html", {
        "published_versions": published_versions,
    })


# ---------------------------------------------------------------------------
# Cycle country detail
# ---------------------------------------------------------------------------

@login_required
def cycle_country_detail(request, cycle_id, country_code):
    cycle = get_object_or_404(CloseCycle, pk=cycle_id)
    country = get_object_or_404(Country, code=country_code.upper())
    assert_country_access(request.user, country)

    process_runs = (
        ProcessRun.objects.filter(cycle=cycle, country=country)
        .select_related("process_template__macro", "owner_user")
        .prefetch_related("task_runs__task_template")
        .order_by("process_template__macro__order", "process_template__order")
    )
    today = timezone.now().date()

    return render(request, "tracker/cycle_country_detail.html", {
        "cycle": cycle,
        "country": country,
        "process_runs": list(process_runs),
        "today": today,
        "is_cfo": is_cfo_for_country(request.user, country),
        "is_hq_user": is_hq(request.user),
    })


# ---------------------------------------------------------------------------
# Task Detail
# ---------------------------------------------------------------------------

@login_required
def task_detail(request, task_id):
    task_run = get_object_or_404(
        TaskRun.objects.select_related(
            "process_run__cycle", "process_run__country",
            "process_run__process_template__macro",
            "task_template", "owner_user",
        ).prefetch_related("evidences", "logs__changed_by"),
        pk=task_id,
    )
    assert_country_access(request.user, task_run.country)

    can_edit = can_update_task(request.user, task_run)
    can_override = can_override_due_date(request.user, task_run)
    today = timezone.now().date()

    if request.method == "POST" and can_edit:
        action = request.POST.get("action")

        if action == "update_status":
            new_status = request.POST.get("status")
            comment = request.POST.get("comment", "").strip()
            if new_status in RunStatus.values:
                update_task_run(task_run, request.user, new_status=new_status, comment=comment or None)
                if request.htmx:
                    task_run.refresh_from_db()
                    return render(request, "tracker/partials/task_status_row.html", {
                        "task_run": task_run,
                        "can_edit": can_edit,
                        "can_override": can_override,
                        "today": today,
                        "RunStatus": RunStatus,
                    })
                messages.success(request, "Status updated.")

        elif action == "add_comment":
            comment = request.POST.get("comment", "").strip()
            if comment:
                update_task_run(task_run, request.user, comment=comment)
                messages.success(request, "Comment added.")

        elif action == "override_due_date" and can_override:
            override_str = request.POST.get("due_date_override", "").strip()
            reason = request.POST.get("override_reason", "").strip()
            try:
                override_date = date.fromisoformat(override_str)
                update_task_run(task_run, request.user, due_date_override=override_date, override_reason=reason)
                messages.success(request, "Due date overridden.")
            except ValueError as e:
                messages.error(request, str(e))

        elif action == "add_evidence":
            title = request.POST.get("evidence_title", "").strip()
            link = request.POST.get("evidence_link", "").strip()
            if title and link:
                Evidence.objects.create(task_run=task_run, title=title, link_url=link, uploaded_by=request.user)
                messages.success(request, "Evidence added.")

        return redirect("task_detail", task_id=task_id)

    logs = task_run.logs.select_related("changed_by").order_by("-changed_at")[:20]

    return render(request, "tracker/task_detail.html", {
        "task_run": task_run,
        "can_edit": can_edit,
        "can_override": can_override,
        "today": today,
        "RunStatus": RunStatus,
        "logs": logs,
        "status_choices": RunStatus.choices,
    })


# ---------------------------------------------------------------------------
# HTMX: quick status update from my-tasks list
# ---------------------------------------------------------------------------

@login_required
@require_POST
def htmx_update_status(request, task_id):
    task_run = get_object_or_404(TaskRun.objects.select_related(
        "process_run__country", "task_template", "owner_user",
        "process_run__cycle", "process_run__process_template__macro",
    ), pk=task_id)
    assert_country_access(request.user, task_run.country)
    if not can_update_task(request.user, task_run):
        return HttpResponseForbidden("No permission to update this task.")

    new_status = request.POST.get("status")
    comment = request.POST.get("comment", "").strip()
    if new_status in RunStatus.values:
        update_task_run(task_run, request.user, new_status=new_status, comment=comment or None)

    today = timezone.now().date()
    return render(request, "tracker/partials/task_row.html", {
        "task_run": task_run,
        "today": today,
        "RunStatus": RunStatus,
        "can_edit": True,
    })


# ---------------------------------------------------------------------------
# Export CSV (HQ only)
# ---------------------------------------------------------------------------

@login_required
@require_hq
def export_csv(request, cycle_id):
    cycle = get_object_or_404(CloseCycle, pk=cycle_id)
    task_runs = (
        TaskRun.objects.filter(process_run__cycle=cycle)
        .select_related(
            "process_run__country",
            "process_run__process_template__macro",
            "task_template",
            "owner_user",
        )
        .order_by(
            "process_run__country__code",
            "process_run__process_template__macro__order",
            "process_run__process_template__order",
            "task_template__order",
        )
    )
    today = timezone.now().date()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="cycle_{cycle.period}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Country", "Macro", "Process", "Task", "Owner",
        "Status", "Due Calculated", "Due Override", "Effective Due",
        "Overdue", "Completed At", "Last Comment",
    ])
    for tr in task_runs:
        pr = tr.process_run
        writer.writerow([
            pr.country.code,
            pr.process_template.macro.name,
            pr.process_template.name,
            tr.task_template.name,
            tr.owner_user.username if tr.owner_user else "",
            tr.status,
            tr.due_date_calculated.isoformat(),
            tr.due_date_override.isoformat() if tr.due_date_override else "",
            tr.effective_due_date.isoformat(),
            "YES" if tr.is_overdue else "NO",
            tr.completed_at.isoformat() if tr.completed_at else "",
            tr.last_comment,
        ])
    return response
