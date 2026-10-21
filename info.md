# CARMA Direct Consumption

This integration imports daily electricity and water usage from the CARMA Direct Consumption [portal](http://www.carmasmartmetering.com/DirectConsumptionDev/login.aspx). CARMA provides sub-metering services for buildings in Canada.

It creates two sensors for each meter:

- `Last daily usage` — the latest available daily total
- `Meter reading` — the cumulative meter reading

Historical daily usage is imported as Home Assistant long-term statistics. You can assign a role to each meter, currently `electricity`, `cold water`, `hot water`, or `other`.

## Installation

1. Open **HACS** in Home Assistant and select **Integrations**.
2. Open the menu and select **Custom repositories**.
3. Add `https://github.com/felixvanoost/ha-carma-direct-consumption` with the category **Integration**.
4. Find **CARMA Direct Consumption** in HACS, download it, then restart Home Assistant.

## Setup

After restarting, go to **Settings → Devices & services → Add integration**, select **CARMA Direct Consumption**, then enter your CARMA service address and account number. These must match the values displayed in the CARMA billing portal. Follow the setup instructions to assign a measurement role for each meter.

History is imported in the background and may take a few minutes. CARMA publishes daily usage about a day late and may revise recent readings, so the integration re-fetches the latest 3 days of data during each refresh.

> **Security:** The CARMA portal supports only HTTP. Login credentials and meter readings are sent in plaintext and can be read by others on the network.

Requires Home Assistant 2025.4 or newer.
