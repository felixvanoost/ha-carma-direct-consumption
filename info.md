# CARMA Smart Metering

This integration imports daily electricity and water usage from the [CARMA Direct Consumption](http://www.carmasmartmetering.com) portal. It includes historical readings for use in the Home Assistant Energy dashboard.

## Installation

1. Open **HACS** in Home Assistant and select **Integrations**.
2. Open the menu and select **Custom repositories**.
3. Add `https://github.com/felixvanoost/ha-carma-smart-metering` with the category **Integration**.
4. Find **CARMA Smart Metering** in HACS, download it, then restart Home Assistant.

## Setup

After restarting, go to **Settings → Devices & services → Add integration**, select **CARMA Smart Metering**, then enter your CARMA service address and account number. These must match the values displayed in the CARMA billing portal. Once connected, confirm each meter's role during setup.
