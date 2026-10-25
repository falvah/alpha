"""Run the supplied full scorecard on ordinary submissions."""
from .scorecard import score_payload
from .scoring_sources import application_input


def calculate(app):
    app.scoring_input = application_input(app)
    app.scoring = score_payload(app.scoring_input)
    app.assigned_to = None
    app.offer_amount = app.offer_months = app.offer_rate = None


def apply(app, actor):
    from .scorecard_workflow import apply_result
    apply_result(app, actor)
