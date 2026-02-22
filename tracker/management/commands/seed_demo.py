"""
Management command: seed_demo

Creates demo data for development:
- 9 countries
- 4 demo users (admin, hq_owner, cfo_it, process_owner_it)
- TemplateFamily + TemplateVersion v1 with Macros AR + AP
- Processes and tasks with offsets
- Dependencies
- Publishes the template
- Creates CloseCycle 2026-03 and generates all runs
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from datetime import date

from tracker.models import (
    Country, Membership, UserGlobalRole, GlobalRole, CountryRole,
    TemplateFamily, TemplateVersion, TemplateVersionStatus,
    MacroTemplate, ProcessTemplate, TaskTemplate, Dependency,
    ProcessAssignment, TaskAssignment,
)
from tracker.services import publish_template_version, create_close_cycle

User = get_user_model()

COUNTRIES = [
    ("AT", "Austria"),
    ("DE", "Germany"),
    ("CZ", "Czech Republic"),
    ("SK", "Slovakia"),
    ("RO", "Romania"),
    ("BG", "Bulgaria"),
    ("PL", "Poland"),
    ("HU", "Hungary"),
    ("IT", "Italy"),
]


class Command(BaseCommand):
    help = "Seed demo data for development"

    def handle(self, *args, **options):
        with transaction.atomic():
            self._create_countries()
            users = self._create_users()
            version = self._create_template(users["admin"])
            publish_template_version(version, users["admin"])
            self.stdout.write(f"Template version published: {version}")
            cycle = create_close_cycle("2026-03", date(2026, 3, 10), version, users["admin"])
            self.stdout.write(f"CloseCycle created: {cycle}")
            self.stdout.write(self.style.SUCCESS("Demo seed completed successfully!"))

    def _create_countries(self):
        for code, name in COUNTRIES:
            Country.objects.get_or_create(code=code, defaults={"name": name})
        self.stdout.write(f"Countries: {Country.objects.count()} available")

    def _create_users(self):
        users = {}
        DEMO_PASSWORD = "demo1234"

        # Admin (superuser)
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@example.com",
                "first_name": "Admin",
                "last_name": "User",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            admin.set_password(DEMO_PASSWORD)
            admin.save()
        UserGlobalRole.objects.get_or_create(user=admin, defaults={"role": GlobalRole.ADMIN})
        users["admin"] = admin

        # HQ Project Owner
        hq_owner, created = User.objects.get_or_create(
            username="hq_owner",
            defaults={
                "email": "hq_owner@example.com",
                "first_name": "HQ",
                "last_name": "Owner",
            },
        )
        if created:
            hq_owner.set_password(DEMO_PASSWORD)
            hq_owner.save()
        UserGlobalRole.objects.get_or_create(user=hq_owner, defaults={"role": GlobalRole.HQ_PROJECT_OWNER})
        users["hq_owner"] = hq_owner

        # Country CFO – Italy
        it = Country.objects.get(code="IT")
        cfo_it, created = User.objects.get_or_create(
            username="cfo_it",
            defaults={
                "email": "cfo_it@example.com",
                "first_name": "Maria",
                "last_name": "Rossi",
            },
        )
        if created:
            cfo_it.set_password(DEMO_PASSWORD)
            cfo_it.save()
        Membership.objects.get_or_create(
            user=cfo_it, country=it, role=CountryRole.COUNTRY_CFO
        )
        users["cfo_it"] = cfo_it

        # Process Owner – Italy
        po_it, created = User.objects.get_or_create(
            username="process_owner_it",
            defaults={
                "email": "po_it@example.com",
                "first_name": "Luca",
                "last_name": "Bianchi",
            },
        )
        if created:
            po_it.set_password(DEMO_PASSWORD)
            po_it.save()
        Membership.objects.get_or_create(
            user=po_it, country=it, role=CountryRole.PROCESS_OWNER
        )
        users["po_it"] = po_it

        self.stdout.write(f"Users created/verified: {list(users.keys())}")
        return users

    def _create_template(self, admin_user):
        family, _ = TemplateFamily.objects.get_or_create(name="Standard OTC/SIAD Close Template")

        # Check if v1 already exists
        version, created = TemplateVersion.objects.get_or_create(
            family=family,
            version_number=1,
            defaults={
                "status": TemplateVersionStatus.DRAFT,
                "created_by": admin_user,
            },
        )
        if not created and version.status == TemplateVersionStatus.PUBLISHED:
            self.stdout.write("Template v1 already published, skipping rebuild.")
            return version

        # -- Macro AR --
        ar, _ = MacroTemplate.objects.get_or_create(
            version=version, name="AR",
            defaults={"order": 10}
        )
        # Processes under AR
        bulk, _ = ProcessTemplate.objects.get_or_create(
            version=version, macro=ar, name="Vendite Bulk",
            defaults={"description": "Gestione vendite bulk mensili", "order": 10, "is_gating": True}
        )
        package, _ = ProcessTemplate.objects.get_or_create(
            version=version, macro=ar, name="Vendite Package",
            defaults={"description": "Gestione vendite package mensili", "order": 20}
        )
        # Tasks – Vendite Bulk
        TaskTemplate.objects.get_or_create(
            version=version, process=bulk, name="Estrazione dati vendite",
            defaults={"description": "Estrai i dati dal sistema ERP", "order": 10, "due_offset_days": -7}
        )
        TaskTemplate.objects.get_or_create(
            version=version, process=bulk, name="Riconciliazione vendite bulk",
            defaults={"description": "Riconcilia totali con contabilità", "order": 20, "due_offset_days": -3,
                      "requires_evidence": True}
        )
        # Tasks – Vendite Package
        TaskTemplate.objects.get_or_create(
            version=version, process=package, name="Analisi contratti package",
            defaults={"order": 10, "due_offset_days": -5}
        )
        TaskTemplate.objects.get_or_create(
            version=version, process=package, name="Chiusura fatturazione package",
            defaults={"order": 20, "due_offset_days": -1, "requires_evidence": True}
        )

        # -- Macro AP --
        ap, _ = MacroTemplate.objects.get_or_create(
            version=version, name="AP",
            defaults={"order": 20}
        )
        # Process under AP
        ricevimenti, _ = ProcessTemplate.objects.get_or_create(
            version=version, macro=ap, name="Ricevimenti",
            defaults={"description": "Contabilizzazione ricevimenti merce/servizi", "order": 10}
        )
        ordini, _ = ProcessTemplate.objects.get_or_create(
            version=version, macro=ap, name="Chiusura ordini di acquisto",
            defaults={"description": "Chiusura ordini di acquisto aperti", "order": 20}
        )
        # Tasks – Ricevimenti
        TaskTemplate.objects.get_or_create(
            version=version, process=ricevimenti, name="Verifica DDT in sospeso",
            defaults={"order": 10, "due_offset_days": -7}
        )
        TaskTemplate.objects.get_or_create(
            version=version, process=ricevimenti, name="Contabilizzazione ricevimenti",
            defaults={"order": 20, "due_offset_days": -3, "requires_evidence": True}
        )
        # Tasks – Ordini
        TaskTemplate.objects.get_or_create(
            version=version, process=ordini, name="Lista ordini aperti",
            defaults={"order": 10, "due_offset_days": -5}
        )
        TaskTemplate.objects.get_or_create(
            version=version, process=ordini, name="Chiusura/annullamento ordini obsoleti",
            defaults={"order": 20, "due_offset_days": 0, "requires_evidence": True}
        )

        # -- Dependency example: AR/Vendite Bulk must complete before AP/Ricevimenti --
        Dependency.objects.get_or_create(
            version=version,
            from_process=bulk,
            to_process=ricevimenti,
        )
        # Another: Vendite Package → Chiusura ordini di acquisto
        Dependency.objects.get_or_create(
            version=version,
            from_process=package,
            to_process=ordini,
        )

        # -- Assignments for Italy --
        it = Country.objects.get(code="IT")
        po_it = User.objects.get(username="process_owner_it")
        for pt in [bulk, package]:
            ProcessAssignment.objects.get_or_create(
                version=version, country=it, process_template=pt,
                defaults={"owner_user": po_it}
            )
        for pt in [ricevimenti, ordini]:
            ProcessAssignment.objects.get_or_create(
                version=version, country=it, process_template=pt,
                defaults={"owner_user": po_it}
            )

        self.stdout.write(f"Template structure built: {version}")
        return version
