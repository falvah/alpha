from django.apps import AppConfig
from django.core.checks import Error, register


class CreditConfig(AppConfig):
    name = 'credit'

    def ready(self):
        @register()
        def check_scorecard(app_configs, **kwargs):
            from .scorecard.policy import load_card
            try:
                load_card()
            except (ValueError, OSError) as exc:
                return [Error(str(exc), id='credit.E001')]
            return []
