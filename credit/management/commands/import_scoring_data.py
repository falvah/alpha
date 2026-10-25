from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction
from credit.models import Company
from credit.scoring_sources import store_source
from credit.scorecard.payload import parse_payload
from credit.scorecard import InputDataError


class Command(BaseCommand):
    help = 'Импортировать проверенные данные для автоматического скоринга компании по ИНН.'

    def add_arguments(self, parser):
        parser.add_argument('inn')
        parser.add_argument('path')
        parser.add_argument('--simulated', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            company = Company.objects.select_for_update().get(inn=options['inn'])
            with Path(options['path']).open('rb') as stream:
                payload = parse_payload(stream.read(65537))
            store_source(company, payload, simulated=options['simulated'])
        except (Company.DoesNotExist, Company.MultipleObjectsReturned, OSError, ValidationError, InputDataError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS('Данные сохранены. Следующая отправка заявки использует полную скоринговую карту.'))
