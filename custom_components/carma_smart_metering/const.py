"""Constants for the CARMA Smart Metering integration."""
from datetime import timedelta

DOMAIN = "carma_smart_metering"

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
WATER_ROLES = {ROLE_COLD, ROLE_HOT}

DEFAULT_HISTORY_MONTHS = 24
REFRESH_MONTHS = 2
UPDATE_INTERVAL = timedelta(hours=12)   # The portal only updates daily, so a faster interval is not useful.
RETRY_INTERVAL = timedelta(minutes=30)  # Retry interval after a failed refresh.

SERVICE_REIMPORT = "reimport_history"
OVERLAP_DAYS = 3  # Re-import the readings for the X newest days each time because the portal may revise them.
