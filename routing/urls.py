from django.urls import path, re_path

from .views import CityDetailView, CityListView, RouteView, api_not_found

urlpatterns = [
    path("routes/", RouteView.as_view(), name="route"),
    path("cities/", CityListView.as_view(), name="city-list"),
    path("cities/<int:pk>/", CityDetailView.as_view(), name="city-detail"),
    re_path(r"^.*$", api_not_found, name="api-not-found"),
]
