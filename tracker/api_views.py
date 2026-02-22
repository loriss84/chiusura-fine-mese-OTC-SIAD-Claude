"""
DRF API views for Month-End Close Tracker.
"""

from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied

from .models import Country, CloseCycle, ProcessRun, TaskRun, Evidence, CycleStatus, RunStatus
from .serializers import (
    CountrySerializer, CloseCycleSerializer, ProcessRunSerializer,
    TaskRunSerializer, TaskRunUpdateSerializer, EvidenceCreateSerializer,
)
from .permissions import (
    is_hq, get_accessible_countries, assert_country_access,
    can_update_task, can_override_due_date,
)
from .services import update_task_run


# ---------------------------------------------------------------------------
# Countries
# ---------------------------------------------------------------------------

class CountryListAPIView(generics.ListAPIView):
    serializer_class = CountrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return get_accessible_countries(self.request.user)


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

class CycleListAPIView(generics.ListAPIView):
    serializer_class = CloseCycleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CloseCycle.objects.select_related("template_version__family").order_by("-period")


class CycleDetailAPIView(generics.RetrieveAPIView):
    serializer_class = CloseCycleSerializer
    permission_classes = [IsAuthenticated]
    queryset = CloseCycle.objects.select_related("template_version__family")


# ---------------------------------------------------------------------------
# Process Runs
# ---------------------------------------------------------------------------

class ProcessRunListAPIView(generics.ListAPIView):
    serializer_class = ProcessRunSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = ProcessRun.objects.select_related(
            "country", "process_template__macro"
        )
        if not is_hq(self.request.user):
            from .models import Membership
            country_ids = Membership.objects.filter(user=self.request.user).values_list("country_id", flat=True)
            qs = qs.filter(country_id__in=list(country_ids))

        cycle_id = self.kwargs.get("cycle_id")
        if cycle_id:
            qs = qs.filter(cycle_id=cycle_id)

        country_code = self.request.query_params.get("country")
        if country_code:
            qs = qs.filter(country__code=country_code.upper())

        return qs.order_by("country__code", "process_template__macro__order", "process_template__order")


# ---------------------------------------------------------------------------
# Task Runs
# ---------------------------------------------------------------------------

class TaskRunListAPIView(generics.ListAPIView):
    serializer_class = TaskRunSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = TaskRun.objects.select_related(
            "process_run__country", "process_run__process_template__macro",
            "task_template", "owner_user",
        ).prefetch_related("evidences")

        if not is_hq(self.request.user):
            from .models import Membership
            country_ids = Membership.objects.filter(user=self.request.user).values_list("country_id", flat=True)
            qs = qs.filter(process_run__country_id__in=list(country_ids))

        # Filter: my tasks
        if self.request.query_params.get("mine") == "1":
            qs = qs.filter(owner_user=self.request.user)

        # Filter: overdue
        if self.request.query_params.get("overdue") == "1":
            from django.utils import timezone
            today = timezone.now().date()
            qs = qs.exclude(status__in=[RunStatus.DONE, RunStatus.NA]).filter(
                due_date_calculated__lt=today
            )

        # Filter: cycle
        cycle_id = self.request.query_params.get("cycle_id")
        if cycle_id:
            qs = qs.filter(process_run__cycle_id=cycle_id)

        return qs.order_by("due_date_calculated")


class TaskRunDetailAPIView(generics.RetrieveAPIView):
    serializer_class = TaskRunSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        task_run = get_object_or_404(
            TaskRun.objects.select_related(
                "process_run__country", "process_run__process_template__macro",
                "task_template", "owner_user",
            ).prefetch_related("evidences", "logs__changed_by"),
            pk=self.kwargs["pk"],
        )
        assert_country_access(self.request.user, task_run.country)
        return task_run


class TaskRunUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        task_run = get_object_or_404(
            TaskRun.objects.select_related("process_run__country", "task_template"),
            pk=pk,
        )
        try:
            assert_country_access(request.user, task_run.country)
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)

        if not can_update_task(request.user, task_run):
            return Response({"detail": "No permission to update this task."}, status=status.HTTP_403_FORBIDDEN)

        ser = TaskRunUpdateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        data = ser.validated_data
        try:
            update_task_run(
                task_run,
                request.user,
                new_status=data.get("status"),
                comment=data.get("comment"),
                due_date_override=data.get("due_date_override"),
                override_reason=data.get("override_reason"),
            )
        except (PermissionDenied, ValueError) as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        task_run.refresh_from_db()
        return Response(TaskRunSerializer(task_run).data)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class EvidenceCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id):
        task_run = get_object_or_404(
            TaskRun.objects.select_related("process_run__country"),
            pk=task_id,
        )
        try:
            assert_country_access(request.user, task_run.country)
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)

        if not can_update_task(request.user, task_run):
            return Response({"detail": "No permission."}, status=status.HTTP_403_FORBIDDEN)

        ser = EvidenceCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        evidence = ser.save(task_run=task_run, uploaded_by=request.user)
        return Response(EvidenceCreateSerializer(evidence).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Dashboard summary (HQ)
# ---------------------------------------------------------------------------

class HQDashboardSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .permissions import is_hq as _is_hq
        if not _is_hq(request.user):
            return Response({"detail": "HQ access required."}, status=status.HTTP_403_FORBIDDEN)

        from django.utils import timezone
        today = timezone.now().date()
        active_cycle = CloseCycle.objects.filter(status=CycleStatus.OPEN).order_by("-period").first()
        if not active_cycle:
            return Response({"active_cycle": None})

        total = TaskRun.objects.filter(process_run__cycle=active_cycle).count()
        done = TaskRun.objects.filter(
            process_run__cycle=active_cycle, status__in=[RunStatus.DONE, RunStatus.NA]
        ).count()
        blocked = TaskRun.objects.filter(
            process_run__cycle=active_cycle, status=RunStatus.BLOCKED
        ).count()
        overdue = [
            t for t in TaskRun.objects.filter(process_run__cycle=active_cycle)
            .exclude(status__in=[RunStatus.DONE, RunStatus.NA])
            if t.effective_due_date < today
        ]

        return Response({
            "active_cycle": CloseCycleSerializer(active_cycle).data,
            "total_tasks": total,
            "done_tasks": done,
            "blocked_tasks": blocked,
            "overdue_tasks": len(overdue),
            "completion_percent": round(done / total * 100, 1) if total else 0,
        })
