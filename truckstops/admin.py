from django.contrib import admin

from .models import CityLocation, TruckStop


@admin.register(TruckStop)
class TruckStopAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "country", "retail_price")
    list_filter = ("country", "state")
    search_fields = ("=opis_id", "name", "city", "address")
    readonly_fields = ("updated_at",)
    autocomplete_fields = ("city_location",)


@admin.register(CityLocation)
class CityLocationAdmin(admin.ModelAdmin):
    list_display = ("city", "state", "country", "status", "provider", "precision")
    list_filter = ("country", "state", "status")
    search_fields = ("city", "state")
    readonly_fields = ("precision", "imported_at")
