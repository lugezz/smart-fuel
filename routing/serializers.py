from rest_framework import serializers

from truckstops.models import US_STATES, CityLocation


def available_cities():
    return CityLocation.objects.filter(
        country="US", state__in=US_STATES, status=CityLocation.Status.RESOLVED,
        latitude__isnull=False, longitude__isnull=False,
    )


class CitySerializer(serializers.ModelSerializer):
    class Meta:
        model = CityLocation
        fields = ("id", "city", "state", "country", "precision")


class CityFilterSerializer(serializers.Serializer):
    search = serializers.CharField(required=False, max_length=100, allow_blank=True)
    state = serializers.ChoiceField(choices=sorted(US_STATES), required=False)

    def to_internal_value(self, data):
        data = data.copy()
        if "state" in data and isinstance(data["state"], str):
            data["state"] = data["state"].strip().upper()
        return super().to_internal_value(data)


class RouteRequestSerializer(serializers.Serializer):
    available_cities_queryset = available_cities()

    start_city_id = serializers.PrimaryKeyRelatedField(
        queryset=available_cities_queryset, pk_field=serializers.IntegerField(min_value=1),
    )
    finish_city_id = serializers.PrimaryKeyRelatedField(
        queryset=available_cities_queryset, pk_field=serializers.IntegerField(min_value=1),
    )

    def validate(self, attrs):
        start, finish = attrs["start_city_id"], attrs["finish_city_id"]
        if start.pk == finish.pk:
            raise serializers.ValidationError("Start and finish must be different cities.")
        if (start.latitude, start.longitude) == (finish.latitude, finish.longitude):
            raise serializers.ValidationError("Start and finish must have different coordinates.")
        return attrs
