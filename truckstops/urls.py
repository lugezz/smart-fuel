from django.urls import path

from .views import TruckStopDetailView, TruckStopListView

urlpatterns = [
    path("", TruckStopListView.as_view(), name="truckstop-list"),
    path("<int:pk>/", TruckStopDetailView.as_view(), name="truckstop-detail"),
]
