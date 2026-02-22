"""
DRF serializers for Month-End Close Tracker.
"""

from rest_framework import serializers
from .models import (
    Country, CloseCycle, ProcessRun, TaskRun, Evidence,
    TaskRunLog, RunStatus,
)


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ["id", "code", "name"]


class CloseCycleSerializer(serializers.ModelSerializer):
    template_version_label = serializers.SerializerMethodField()

    class Meta:
        model = CloseCycle
        fields = ["id", "period", "final_deadline", "status", "template_version_label", "created_at"]

    def get_template_version_label(self, obj):
        return str(obj.template_version)


class ProcessRunSerializer(serializers.ModelSerializer):
    country_code = serializers.CharField(source="country.code", read_only=True)
    process_name = serializers.CharField(source="process_template.name", read_only=True)
    macro_name = serializers.CharField(source="process_template.macro.name", read_only=True)

    class Meta:
        model = ProcessRun
        fields = [
            "id", "country_code", "macro_name", "process_name",
            "status", "completion_percent", "owner_user",
        ]


class EvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evidence
        fields = ["id", "title", "link_url", "uploaded_by", "uploaded_at"]
        read_only_fields = ["uploaded_by", "uploaded_at"]


class TaskRunLogSerializer(serializers.ModelSerializer):
    changed_by_username = serializers.CharField(source="changed_by.username", read_only=True)

    class Meta:
        model = TaskRunLog
        fields = ["id", "event_type", "old_value", "new_value", "comment", "changed_by_username", "changed_at"]


class TaskRunSerializer(serializers.ModelSerializer):
    country_code = serializers.CharField(source="process_run.country.code", read_only=True)
    process_name = serializers.CharField(source="process_run.process_template.name", read_only=True)
    macro_name = serializers.CharField(source="process_run.process_template.macro.name", read_only=True)
    task_name = serializers.CharField(source="task_template.name", read_only=True)
    effective_due_date = serializers.DateField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    evidences = EvidenceSerializer(many=True, read_only=True)
    logs = TaskRunLogSerializer(many=True, read_only=True)

    class Meta:
        model = TaskRun
        fields = [
            "id", "country_code", "macro_name", "process_name", "task_name",
            "status", "due_date_calculated", "due_date_override", "override_reason",
            "effective_due_date", "is_overdue",
            "completed_at", "last_updated_at", "last_comment",
            "owner_user", "evidences", "logs",
        ]
        read_only_fields = [
            "due_date_calculated", "effective_due_date", "is_overdue",
            "completed_at", "last_updated_at",
        ]


class TaskRunUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=RunStatus.choices, required=False)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    due_date_override = serializers.DateField(required=False, allow_null=True)
    override_reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class EvidenceCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evidence
        fields = ["title", "link_url"]
