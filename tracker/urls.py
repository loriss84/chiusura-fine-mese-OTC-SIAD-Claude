"""
URL configuration for tracker app.
"""

from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views, api_views

urlpatterns = [
    # Auth
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),

    # Dashboard
    path("dashboard/", views.dashboard_redirect, name="dashboard"),
    path("dashboard/hq/", views.hq_dashboard, name="hq_dashboard"),
    path("dashboard/country/", views.country_dashboard, name="country_dashboard"),
    path("dashboard/country/<str:country_code>/", views.country_dashboard, name="country_dashboard_code"),

    # My tasks
    path("my-tasks/", views.my_tasks, name="my_tasks"),

    # Cycles
    path("cycles/", views.cycle_list, name="cycle_list"),
    path("cycles/create/", views.cycle_create, name="cycle_create"),
    path("cycles/<int:cycle_id>/country/<str:country_code>/", views.cycle_country_detail, name="cycle_country_detail"),
    path("cycles/<int:cycle_id>/export/", views.export_csv, name="export_csv"),

    # Tasks
    path("task/<int:task_id>/", views.task_detail, name="task_detail"),
    path("task/<int:task_id>/update-status/", views.htmx_update_status, name="htmx_update_status"),

    # ---- API ----
    path("api/countries/", api_views.CountryListAPIView.as_view(), name="api_countries"),
    path("api/cycles/", api_views.CycleListAPIView.as_view(), name="api_cycles"),
    path("api/cycles/<int:pk>/", api_views.CycleDetailAPIView.as_view(), name="api_cycle_detail"),
    path("api/cycles/<int:cycle_id>/process-runs/", api_views.ProcessRunListAPIView.as_view(), name="api_process_runs"),
    path("api/task-runs/", api_views.TaskRunListAPIView.as_view(), name="api_task_runs"),
    path("api/task-runs/<int:pk>/", api_views.TaskRunDetailAPIView.as_view(), name="api_task_run_detail"),
    path("api/task-runs/<int:pk>/update/", api_views.TaskRunUpdateAPIView.as_view(), name="api_task_run_update"),
    path("api/task-runs/<int:task_id>/evidences/", api_views.EvidenceCreateAPIView.as_view(), name="api_evidence_create"),
    path("api/dashboard/hq/", api_views.HQDashboardSummaryAPIView.as_view(), name="api_hq_dashboard"),
]
