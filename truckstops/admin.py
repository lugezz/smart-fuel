from django.contrib import admin

from .models import TruckStop


@admin.register(TruckStop)
class TruckStopAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "country", "retail_price")
    list_filter = ("country", "state")
    search_fields = ("=opis_id", "name", "city", "address")
    readonly_fields = ("updated_at",)
