# Changelog

## 0.2.0

- Shows each meter's assigned role as its translated device name (e.g. 'Cold water') and translates its sensor names ('Last daily usage' and 'Meter reading'). The meter serial number is now correctly stored as the device serial number instead of appearing in the device name.
- Bases sensor unique IDs and long-term statistic IDs on the meter serial number.

## 0.1.0

Initial release.

- Signs in to the CARMA Direct Consumption [portal](http://www.carmasmartmetering.com/DirectConsumptionDev/login.aspx) and reads the daily consumption for every meter on the account.
- Imports consumption history into Home Assistant as long-term statistics (`carma_direct_consumption:meter_<number>`) for the Energy dashboard.
- Applies per-meter roles - currently `electricity`, `cold water`, `hot water`, `other`, or `skip`. The roles can be changed later without losing history.
- Creates two sensors per meter - last daily usage and the meter reading.
- Defines a `carma_direct_consumption.reimport_history` service to rebuild the imported history.
- Paces requests and retries after portal server errors.
