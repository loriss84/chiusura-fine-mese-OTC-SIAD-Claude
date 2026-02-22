"""
Automated tests for Month-End Close Tracker.

Covers:
  1. Country isolation (multi-tenant security)
  2. Override due date permissions
  3. Effective due date calculation
  4. DAG cycle detection on publish
  5. ProcessRun status aggregation
"""

from datetime import date, timedelta
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import (
    Country, Membership, UserGlobalRole,
    GlobalRole, CountryRole,
    TemplateFamily, TemplateVersion, TemplateVersionStatus,
    MacroTemplate, ProcessTemplate, TaskTemplate, Dependency,
    CloseCycle, ProcessRun, TaskRun, RunStatus, CycleStatus,
)
from .permissions import (
    get_accessible_countries, assert_country_access,
    can_override_due_date, can_update_task, is_hq,
)
from .services import (
    publish_template_version, create_close_cycle,
    update_task_run, validate_no_dependency_cycle,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(username, **kwargs):
    u = User.objects.create_user(username=username, password="testpass", **kwargs)
    return u


def make_country(code, name=None):
    return Country.objects.create(code=code, name=name or code)


def make_membership(user, country, role):
    return Membership.objects.create(user=user, country=country, role=role)


def make_global_role(user, role):
    return UserGlobalRole.objects.create(user=user, role=role)


def build_minimal_template(admin_user):
    """Return a DRAFT TemplateVersion with one macro, one process, one task."""
    family = TemplateFamily.objects.create(name="Test Family")
    version = TemplateVersion.objects.create(
        family=family, version_number=1,
        status=TemplateVersionStatus.DRAFT, created_by=admin_user,
    )
    macro = MacroTemplate.objects.create(version=version, name="AR", order=10)
    process = ProcessTemplate.objects.create(
        version=version, macro=macro, name="Test Process", order=10
    )
    TaskTemplate.objects.create(
        version=version, process=process, name="Task A",
        due_offset_days=-5, order=10,
    )
    TaskTemplate.objects.create(
        version=version, process=process, name="Task B",
        due_offset_days=0, order=20,
    )
    return version


# ---------------------------------------------------------------------------
# Test: Country isolation
# ---------------------------------------------------------------------------

class CountryIsolationTest(TestCase):
    def setUp(self):
        self.country_it = make_country("IT", "Italy")
        self.country_de = make_country("DE", "Germany")

        self.cfo_it = make_user("cfo_it")
        make_membership(self.cfo_it, self.country_it, CountryRole.COUNTRY_CFO)

        self.cfo_de = make_user("cfo_de")
        make_membership(self.cfo_de, self.country_de, CountryRole.COUNTRY_CFO)

        self.hq_user = make_user("hq_user")
        make_global_role(self.hq_user, GlobalRole.HQ_PROJECT_OWNER)

    def test_cfo_it_sees_only_italy(self):
        accessible = get_accessible_countries(self.cfo_it)
        codes = list(accessible.values_list("code", flat=True))
        self.assertIn("IT", codes)
        self.assertNotIn("DE", codes)

    def test_cfo_de_sees_only_germany(self):
        accessible = get_accessible_countries(self.cfo_de)
        codes = list(accessible.values_list("code", flat=True))
        self.assertIn("DE", codes)
        self.assertNotIn("IT", codes)

    def test_hq_sees_all_countries(self):
        accessible = get_accessible_countries(self.hq_user)
        codes = list(accessible.values_list("code", flat=True))
        self.assertIn("IT", codes)
        self.assertIn("DE", codes)

    def test_cfo_it_cannot_access_de(self):
        with self.assertRaises(PermissionDenied):
            assert_country_access(self.cfo_it, self.country_de)

    def test_hq_can_access_any_country(self):
        # Should not raise
        assert_country_access(self.hq_user, self.country_it)
        assert_country_access(self.hq_user, self.country_de)

    def test_task_run_country_isolation(self):
        """A CFO for IT cannot update a TaskRun in DE."""
        admin = make_user("admin_u", is_superuser=True)
        version = build_minimal_template(admin)
        publish_template_version(version, admin)
        cycle = create_close_cycle("2026-03", date(2026, 3, 10), version, admin)

        # Get a TaskRun for DE
        pr_de = ProcessRun.objects.get(cycle=cycle, country=self.country_de)
        tr_de = pr_de.task_runs.first()

        # CFO IT should not be able to update this
        self.assertFalse(can_update_task(self.cfo_it, tr_de))

    def test_process_run_country_scoped(self):
        """ProcessRun queryset for cfo_it should only include IT runs."""
        admin = make_user("admin_u2", is_superuser=True)
        version = build_minimal_template(admin)
        publish_template_version(version, admin)
        cycle = create_close_cycle("2026-04", date(2026, 4, 10), version, admin)

        it_runs = ProcessRun.objects.filter(cycle=cycle, country=self.country_it)
        de_runs = ProcessRun.objects.filter(cycle=cycle, country=self.country_de)
        self.assertTrue(it_runs.exists())
        self.assertTrue(de_runs.exists())

        # Verify isolation: cfo_it's accessible countries don't include DE
        it_codes = list(get_accessible_countries(self.cfo_it).values_list("code", flat=True))
        self.assertNotIn("DE", it_codes)


# ---------------------------------------------------------------------------
# Test: Override due date permissions
# ---------------------------------------------------------------------------

class OverrideDueDatePermissionTest(TestCase):
    def setUp(self):
        self.country_it = make_country("IT2", "Italy2")
        self.country_de = make_country("DE2", "Germany2")

        self.admin_user = make_user("admin_ov")
        make_global_role(self.admin_user, GlobalRole.ADMIN)

        self.hq_user = make_user("hq_ov")
        make_global_role(self.hq_user, GlobalRole.HQ_PROJECT_OWNER)

        self.cfo_it = make_user("cfo_it_ov")
        make_membership(self.cfo_it, self.country_it, CountryRole.COUNTRY_CFO)

        self.po_it = make_user("po_it_ov")
        make_membership(self.po_it, self.country_it, CountryRole.PROCESS_OWNER)

        # Build a TaskRun for Italy
        admin = make_user("admin_seed_ov", is_superuser=True)
        version = build_minimal_template(admin)
        publish_template_version(version, admin)
        cycle = create_close_cycle("2026-05", date(2026, 5, 10), version, admin)
        pr_it = ProcessRun.objects.get(cycle=cycle, country=self.country_it)
        self.task_run_it = pr_it.task_runs.first()

        pr_de = ProcessRun.objects.get(cycle=cycle, country=self.country_de)
        self.task_run_de = pr_de.task_runs.first()

    def test_admin_can_override_any(self):
        self.assertTrue(can_override_due_date(self.admin_user, self.task_run_it))
        self.assertTrue(can_override_due_date(self.admin_user, self.task_run_de))

    def test_hq_can_override_any(self):
        self.assertTrue(can_override_due_date(self.hq_user, self.task_run_it))
        self.assertTrue(can_override_due_date(self.hq_user, self.task_run_de))

    def test_cfo_can_override_own_country(self):
        self.assertTrue(can_override_due_date(self.cfo_it, self.task_run_it))

    def test_cfo_cannot_override_other_country(self):
        self.assertFalse(can_override_due_date(self.cfo_it, self.task_run_de))

    def test_process_owner_cannot_override(self):
        self.assertFalse(can_override_due_date(self.po_it, self.task_run_it))

    def test_override_requires_reason(self):
        with self.assertRaises(ValueError):
            update_task_run(
                self.task_run_it, self.hq_user,
                due_date_override=date(2026, 5, 15),
                override_reason="",  # empty reason
            )

    def test_override_saves_correctly(self):
        new_date = date(2026, 5, 20)
        update_task_run(
            self.task_run_it, self.hq_user,
            due_date_override=new_date,
            override_reason="Business delay approved by CFO",
        )
        self.task_run_it.refresh_from_db()
        self.assertEqual(self.task_run_it.due_date_override, new_date)
        self.assertEqual(self.task_run_it.override_reason, "Business delay approved by CFO")


# ---------------------------------------------------------------------------
# Test: Effective due date
# ---------------------------------------------------------------------------

class EffectiveDueDateTest(TestCase):
    def setUp(self):
        self.country_it = make_country("IT3", "Italy3")
        admin = make_user("admin_dd", is_superuser=True)
        version = build_minimal_template(admin)
        publish_template_version(version, admin)
        self.final_deadline = date(2026, 6, 10)
        cycle = create_close_cycle("2026-06", self.final_deadline, version, admin)
        pr = ProcessRun.objects.get(cycle=cycle, country=self.country_it)
        self.task_runs = list(pr.task_runs.order_by("task_template__order"))

    def test_calculated_due_date_uses_offset(self):
        # Task A: offset -5, Task B: offset 0
        tr_a = self.task_runs[0]
        tr_b = self.task_runs[1]
        self.assertEqual(tr_a.due_date_calculated, self.final_deadline + timedelta(days=-5))
        self.assertEqual(tr_b.due_date_calculated, self.final_deadline)

    def test_effective_due_date_without_override(self):
        tr = self.task_runs[0]
        self.assertIsNone(tr.due_date_override)
        self.assertEqual(tr.effective_due_date, tr.due_date_calculated)

    def test_effective_due_date_with_override(self):
        tr = self.task_runs[0]
        override = date(2026, 6, 20)
        tr.due_date_override = override
        tr.override_reason = "Extended"
        tr.save()
        self.assertEqual(tr.effective_due_date, override)

    def test_is_overdue_not_done(self):
        tr = self.task_runs[0]
        # Force overdue by setting a past calculated date
        tr.due_date_calculated = date(2020, 1, 1)
        tr.save()
        self.assertTrue(tr.is_overdue)

    def test_is_overdue_false_when_done(self):
        tr = self.task_runs[0]
        tr.due_date_calculated = date(2020, 1, 1)
        tr.status = RunStatus.DONE
        tr.save()
        self.assertFalse(tr.is_overdue)


# ---------------------------------------------------------------------------
# Test: DAG cycle detection
# ---------------------------------------------------------------------------

class DependencyCycleTest(TestCase):
    def setUp(self):
        self.admin = make_user("admin_dag", is_superuser=True)
        family = TemplateFamily.objects.create(name="DAG Test Family")
        self.version = TemplateVersion.objects.create(
            family=family, version_number=1,
            status=TemplateVersionStatus.DRAFT, created_by=self.admin,
        )
        macro = MacroTemplate.objects.create(version=self.version, name="GL", order=10)
        self.p1 = ProcessTemplate.objects.create(version=self.version, macro=macro, name="P1", order=10)
        self.p2 = ProcessTemplate.objects.create(version=self.version, macro=macro, name="P2", order=20)
        self.p3 = ProcessTemplate.objects.create(version=self.version, macro=macro, name="P3", order=30)
        TaskTemplate.objects.create(version=self.version, process=self.p1, name="T1", due_offset_days=0)
        TaskTemplate.objects.create(version=self.version, process=self.p2, name="T2", due_offset_days=0)
        TaskTemplate.objects.create(version=self.version, process=self.p3, name="T3", due_offset_days=0)

    def test_no_cycle_allowed(self):
        """A linear chain P1 -> P2 -> P3 should publish successfully."""
        Dependency.objects.create(version=self.version, from_process=self.p1, to_process=self.p2)
        Dependency.objects.create(version=self.version, from_process=self.p2, to_process=self.p3)
        # Should not raise
        publish_template_version(self.version, self.admin)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, TemplateVersionStatus.PUBLISHED)

    def test_cycle_detected(self):
        """A cycle P1 -> P2 -> P3 -> P1 should raise ValueError."""
        Dependency.objects.create(version=self.version, from_process=self.p1, to_process=self.p2)
        Dependency.objects.create(version=self.version, from_process=self.p2, to_process=self.p3)
        Dependency.objects.create(version=self.version, from_process=self.p3, to_process=self.p1)
        with self.assertRaises(ValueError, msg="cycle"):
            validate_no_dependency_cycle(self.version)

    def test_self_loop_detected(self):
        """P1 -> P1 is a cycle."""
        Dependency.objects.create(version=self.version, from_process=self.p1, to_process=self.p1)
        with self.assertRaises(ValueError):
            validate_no_dependency_cycle(self.version)

    def test_cannot_publish_cyclic_version(self):
        """publish_template_version should reject a cyclic graph."""
        Dependency.objects.create(version=self.version, from_process=self.p1, to_process=self.p2)
        Dependency.objects.create(version=self.version, from_process=self.p2, to_process=self.p1)
        with self.assertRaises(ValueError):
            publish_template_version(self.version, self.admin)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, TemplateVersionStatus.DRAFT)


# ---------------------------------------------------------------------------
# Test: ProcessRun status aggregation
# ---------------------------------------------------------------------------

class ProcessRunStatusTest(TestCase):
    def setUp(self):
        self.country = make_country("IT4", "Italy4")
        self.admin = make_user("admin_pr", is_superuser=True)
        version = build_minimal_template(self.admin)
        publish_template_version(version, self.admin)
        cycle = create_close_cycle("2026-07", date(2026, 7, 10), version, self.admin)
        self.pr = ProcessRun.objects.get(cycle=cycle, country=self.country)
        self.task_runs = list(self.pr.task_runs.order_by("task_template__order"))

    def _set_statuses(self, *statuses):
        for tr, st in zip(self.task_runs, statuses):
            tr.status = st
            tr.save()
        self.pr.refresh_status()

    def test_all_not_started(self):
        self._set_statuses(RunStatus.NOT_STARTED, RunStatus.NOT_STARTED)
        self.assertEqual(self.pr.status, RunStatus.NOT_STARTED)
        self.assertEqual(self.pr.completion_percent, 0)

    def test_one_in_progress(self):
        self._set_statuses(RunStatus.IN_PROGRESS, RunStatus.NOT_STARTED)
        self.assertEqual(self.pr.status, RunStatus.IN_PROGRESS)

    def test_all_done(self):
        self._set_statuses(RunStatus.DONE, RunStatus.DONE)
        self.assertEqual(self.pr.status, RunStatus.DONE)
        self.assertEqual(self.pr.completion_percent, 100)

    def test_mixed_done_na(self):
        self._set_statuses(RunStatus.DONE, RunStatus.NA)
        self.assertEqual(self.pr.status, RunStatus.DONE)
        self.assertEqual(self.pr.completion_percent, 100)

    def test_any_blocked_overrides(self):
        self._set_statuses(RunStatus.DONE, RunStatus.BLOCKED)
        self.assertEqual(self.pr.status, RunStatus.BLOCKED)

    def test_completion_percent_partial(self):
        self._set_statuses(RunStatus.DONE, RunStatus.IN_PROGRESS)
        self.assertEqual(float(self.pr.completion_percent), 50.0)
