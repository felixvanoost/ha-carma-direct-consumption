![Test Workflow](https://github.com/felixvanoost/ha-carma-direct-consumption/actions/workflows/test.yml/badge.svg)
![Validate Workflow](https://github.com/felixvanoost/ha-carma-direct-consumption/actions/workflows/validate.yml/badge.svg)

# CARMA Direct Consumption

> **Note:** This integration is not affiliated with or endorsed by CARMA Corp.

A Home Assistant integration that reads daily electricity and water consumption from the CARMA Direct Consumption [portal](http://www.carmasmartmetering.com/DirectConsumptionDev/login.aspx). CARMA provides sub-metering services to residential and commercial buildings in Canada.

## Features

- Reads every meter on the account and creates `last_daily_usage` and `meter_reading` sensors for each imported meter.
- Imports historical readings as long-term statistics.
- Lets you assign a role to each meter (electricity, cold water, hot water, or other).
- Lets you reconfigure the integration to rediscover meters and update their roles.

## Installation

### Home Assistant Marketplace

[Add CARMA Direct Consumption to Home Assistant](https://my.home-assistant.io/redirect/config_flow_start/?domain=carma_direct_consumption)

In Home Assistant 2026.11 or later, open **Settings → Devices & services → Add integration**, search for **CARMA Direct Consumption**, and complete the setup.

### HACS

If HACS is not already installed, follow the [Getting started with HACS](https://hacs.xyz/docs/use/) instructions. From the machine that Home Assistant is running on, click on this badge to add the repository:

[![Open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=felixvanoost&repository=ha-carma-direct-consumption&category=integration)

You can also add the repository manually:

1. Open the HACS panel on the left sidebar, then select **Integrations**.
2. Open the three-dot menu in the top-right corner and select **Custom repositories**.
3. Enter the repository URL (`https://github.com/felixvanoost/ha-carma-direct-consumption`) and type (`integration`), then click on **Add**.
4. Find **CARMA Direct Consumption** in HACS, open the three-dot menu, and select **Download**.
5. Restart Home Assistant to apply the changes.

## Configuration

Once installed, add the CARMA Direct Consumption integration to Home Assistant and configure the meter roles:

1. Open **Settings → Devices & services → Add integration**, then select **CARMA Direct Consumption**.
2. Enter your service address and account number exactly as they are displayed on the CARMA billing portal.
3. When prompted, select the type of measurement that each meter on your account corresponds to. If you are unsure, check the numbers against a recent CARMA bill.

Once configured, the reading history is imported in the background and may take a few minutes.

To refresh the meters associated with the account:

1. Open **Settings → Devices & services**, then select **CARMA Direct Consumption**.
2. Open the three-dot menu, and choose **Reconfigure**.

Existing roles are preserved for meters that remain on the account. New meters can be assigned roles, and meters that are no longer reported can be removed from Home Assistant.

## Usage

Daily usage is stored as Home Assistant [long-term statistics](https://www.home-assistant.io/docs/configuration/long-term-statistics/). These can be used on the Energy dashboard:

1. Open the Energy dashboard via **Settings → Dashboards → Energy**.
2. From the Energy dashboard, click on **Edit** in the top right corner.
3. Under **Grid connections**, select the electricity meters to use as data sources. To track water, add the meters under **Water consumption**. You can monitor hot and cold water separately or track individually metered electricity zones.

The CARMA portal publishes daily readings with roughly a one-day delay. It may also revise recent readings, so the latest three days are re-imported with every refresh.

> **Security:** The CARMA portal supports HTTP only. Login credentials and meter readings are sent without encryption and can be visible to others on the network.

## Troubleshooting

To enable debug logging, open **Developer tools → Actions** and paste the following YAML code:

```yaml
logger:
  logs:
    custom_components.carma_direct_consumption: debug
```

You can check the logs in **Settings → System → Logs** to see why an import failed:

```bash
Meter 00000000: read 148 days from 5 months (Jun 2026 to Oct 2026); stop reason: Prev Month stopped moving.
```

If a meter role is not recognized, the integration logs a warning and skips the meter. Reconfigure the integration or update the meter roles in the options to assign a supported role.

To re-import meter data from scratch, open **Developer tools → Actions** and run **CARMA Direct Consumption: Re-import history**.

## Contributing

Bug reports and pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.
