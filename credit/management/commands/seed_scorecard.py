from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from credit.scorecard_workflow import CASES, create_seed_application, load_case


class Command(BaseCommand):
    help = "Добавить три контрольные заявки существующему пользователю; повторный запуск безопасен."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="entrepreneur")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.SANDBOX_MODE:
            raise CommandError("seed_scorecard запрещён вне тестового контура.")
        try:
            owner = get_user_model().objects.get(username=options["username"], is_staff=False, company__isnull=False)
        except get_user_model().DoesNotExist as exc:
            raise CommandError("Сначала создайте рабочие профили командой seed_workspace или укажите --username.") from exc
        for name in CASES:
            app = create_seed_application(owner, load_case(name), True, name)
            self.stdout.write(f"{name}: {app.reference} — {app.get_status_display()}; сумма предложения: {app.offer_amount or '—'}")
