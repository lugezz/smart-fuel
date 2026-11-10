from rest_framework import serializers

from .models import (
    CANADIAN_PROVINCES,
    US_STATES,
    CityLocation,
    TruckStop,
    normalize_city,
)


class TruckStopCitySerializer(serializers.ModelSerializer):
    class Meta:
        model = CityLocation
        fields = (
            "id", "city", "state", "country", "status", "precision",
            "latitude", "longitude", "provider", "source_id", "attribution",
        )


class TruckStopSerializer(serializers.ModelSerializer):
    city_location = TruckStopCitySerializer(read_only=True)

    class Meta:
        model = TruckStop
        fields = (
            "id", "opis_id", "name", "address", "city", "state", "country",
            "rack_id", "retail_price", "latitude", "longitude", "city_location", "updated_at",
        )


class TruckStopFilterSerializer(serializers.Serializer):
    country = serializers.ChoiceField(choices=TruckStop.Country.values, required=False)
    state = serializers.ChoiceField(choices=sorted(US_STATES | CANADIAN_PROVINCES), required=False)
    city = serializers.CharField(required=False, max_length=100)

    def to_internal_value(self, data):
        data = data.copy()
        for field in ("country", "state"):
            if field in data and isinstance(data[field], str):
                data[field] = data[field].strip().upper()
        return super().to_internal_value(data)

    def validate_city(self, value):
        return normalize_city(value)

    def validate(self, attrs):
        country, state = attrs.get("country"), attrs.get("state")
        if country and state:
            regions = US_STATES if country == TruckStop.Country.USA else CANADIAN_PROVINCES
            if state not in regions:
                raise serializers.ValidationError({"state": "State/province does not belong to the selected country."})
        return attrs
