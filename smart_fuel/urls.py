from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/truckstops/', include('truckstops.urls')),
    path('api/routing/', include('routing.urls')),
]
