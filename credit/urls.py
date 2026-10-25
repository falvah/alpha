from django.urls import path
from . import views as v
urlpatterns = [
    path("", v.dashboard, name="dashboard"), path("login/", v.sign_in, name="login"), path("logout/", v.sign_out, name="logout"),
    path("products/", v.products, name="products"), path("applications/", v.applications, name="applications"),
    path("applications/new/", v.new_application, name="application_new"),
    path("bank/scorecard/", v.scorecard_workspace, name="scorecard_workspace"),
    path("applications/<uuid:pk>/scorecard/", v.application_scorecard, name="application_scorecard"),
    path("applications/<uuid:pk>/", v.application_detail, name="application_detail"),
    path("applications/<uuid:pk>/edit/<int:step>/", v.edit_application, name="application_edit"),
    path("applications/<uuid:pk>/actions/<str:action>/", v.application_action, name="application_action"),
    path("applications/<uuid:pk>/schedule/", v.payment_schedule, name="payment_schedule"),
    path("applications/<uuid:pk>/documents/<uuid:doc_id>/delete/", v.document_delete, name="document_delete"),
    path("documents/", v.documents, name="documents"), path("documents/<uuid:doc_id>/download/", v.document_download, name="document_download"),
    path("notifications/", v.notifications, name="notifications"), path("profile/", v.profile, name="profile"), path("help/", v.help_page, name="help"),
    path("bank/", v.staff_queue, name="staff_queue"), path("bank/audit/", v.audit, name="audit"), path("health/", v.health, name="health"),
    path("api/v1/products/", v.api_products), path("api/v1/applications/", v.api_applications),
    path("api/v1/applications/<uuid:pk>/", v.api_applications), path("api/v1/calculator/", v.api_calculate),
    path("api/v1/scorecard/", v.api_scorecard, name="api_scorecard"),
]
