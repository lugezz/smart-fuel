from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI "
    "MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT "
    "VA WA WV WI WY".split()
)
CANADIAN_PROVINCES = frozenset(
    "AB BC MB NB NL NS NT NU ON PE QC SK YT".split()
)


class TruckStop(models.Model):
    class Country(models.TextChoices):
        USA = "US", "United States"
        CANADA = "CA", "Canada"

    opis_id = models.PositiveIntegerField(unique=True, validators=[MinValueValidator(1)])
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    country = models.CharField(max_length=2, choices=Country.choices, db_index=True)
    rack_id = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    retail_price = models.DecimalField(
        max_digits=12,
        decimal_places=8,
        validators=[MinValueValidator(Decimal("0.00000001"))],
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(retail_price__gt=0),
                name="truckstop_positive_price",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(latitude__isnull=True, longitude__isnull=True)
                    | models.Q(
                        latitude__isnull=False,
                        longitude__isnull=False,
                        latitude__gte=-90,
                        latitude__lte=90,
                        longitude__gte=-180,
                        longitude__lte=180,
                    )
                ),
                name="truckstop_valid_coordinates",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state})"
