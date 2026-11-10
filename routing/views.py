from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from truckstops.models import normalize_city

from .serializers import (
    CityFilterSerializer,
    CitySerializer,
    RouteRequestSerializer,
    available_cities,
)
from .services import ATTRIBUTION, get_route


class CityPagination(PageNumberPagination):
    page_size = 50


class CityListView(ListAPIView):
    serializer_class = CitySerializer
    pagination_class = CityPagination

    def get_queryset(self):
        filters = CityFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        queryset = available_cities()
        search = filters.validated_data.get("search")
        state = filters.validated_data.get("state")
        if search:
            queryset = queryset.filter(city__icontains=normalize_city(search))
        if state:
            queryset = queryset.filter(state=state)
        return queryset.order_by("city", "state", "pk")


class CityDetailView(RetrieveAPIView):
    serializer_class = CitySerializer
    queryset = available_cities()


@csrf_exempt
def api_not_found(request):
    return JsonResponse({"detail": "Not found."}, status=404)


class RouteView(APIView):
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        start = serializer.validated_data["start_city_id"]
        finish = serializer.validated_data["finish_city_id"]
        result = get_route(start, finish)
        return Response({
            "start": CitySerializer(start).data,
            "finish": CitySerializer(finish).data,
            "route": result.feature,
            "provider": "openrouteservice",
            "attribution": ATTRIBUTION,
            "location_attribution": list(dict.fromkeys([start.attribution, finish.attribution])),
            "cached": result.cached,
        })
