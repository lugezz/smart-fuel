from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination

from .models import TruckStop
from .serializers import TruckStopFilterSerializer, TruckStopSerializer


class TruckStopPagination(PageNumberPagination):
    page_size = 50


class TruckStopListView(ListAPIView):
    serializer_class = TruckStopSerializer
    pagination_class = TruckStopPagination

    def get_queryset(self):
        filters = TruckStopFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        queryset = TruckStop.objects.select_related("city_location")
        for field in ("country", "state"):
            if field in filters.validated_data:
                queryset = queryset.filter(**{field: filters.validated_data[field]})
        city = filters.validated_data.get("city")
        if city:
            queryset = queryset.filter(city__icontains=city)
        return queryset.order_by("opis_id", "pk")


class TruckStopDetailView(RetrieveAPIView):
    serializer_class = TruckStopSerializer
    queryset = TruckStop.objects.select_related("city_location")
