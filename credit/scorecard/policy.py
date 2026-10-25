"""Load the supplied card unchanged; enable its documented manual-review band."""
from dataclasses import replace
from django.conf import settings
from credit.mlcard.config import CardConfig


def load_card():
    card = CardConfig.load_default()
    review_pd = getattr(settings, 'SCORECARD_REVIEW_PD', 0.20) if settings.configured else 0.20
    if review_pd is not None and not card.policy.max_pd < review_pd <= 1:
        raise ValueError('SCORECARD_REVIEW_PD must exceed max_pd and be at most 1.')
    return replace(card, policy=replace(card.policy, review_pd=review_pd))
