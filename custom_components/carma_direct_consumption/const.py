"""Constants for the CARMA Direct Consumption integration."""

from datetime import timedelta

DOMAIN = "carma_direct_consumption"

BASE_URL = "http://www.carmasmartmetering.com/DirectConsumptionDev/"

CONF_ADDRESS = "service_address"
CONF_ACCOUNT = "account_number"
CONF_METERS = "meters"
CONF_UNITS = "units"
CONF_HISTORY_MONTHS = "history_months"

ROLE_COLD = "cold_water"
ROLE_HOT = "hot_water"
ROLE_ELECTRICITY = "electricity"
ROLE_OTHER = "other"
ROLE_SKIP = "skip"

ROLE_LABELS = {
    ROLE_COLD: "Cold water",
    ROLE_HOT: "Hot water",
    ROLE_ELECTRICITY: "Electricity",
    ROLE_OTHER: "Other",
    ROLE_SKIP: "Don't import",
}

DEFAULT_HISTORY_MONTHS = 24
REFRESH_MONTHS = 2
UPDATE_INTERVAL = timedelta(
    hours=12
)  # Note: the portal only updates readings once per day.
RETRY_INTERVAL = timedelta(minutes=30)  # Retry interval after a failed refresh.

SERVICE_REIMPORT = "reimport_history"
# Re-import the newest days on each refresh because the portal may revise readings.
OVERLAP_DAYS = 3
