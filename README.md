![Test Workflow](https://github.com/felixvanoost/ha-carma-direct-consumption/actions/workflows/test.yml/badge.svg)
![Validate Workflow](https://github.com/felixvanoost/ha-carma-direct-consumption/actions/workflows/validate.yml/badge.svg)

# CARMA Direct Consumption

> **Note:** This integration is not affiliated with or endorsed by CARMA Corp.

An integration for Home Assistant that reads daily electricity and water consumption readings from the CARMA Direct Consumption [portal](http://www.carmasmartmetering.com/DirectConsumptionDev/login.aspx). CARMA provides sub-metering services for buildings in Canada.

## Features

- Reads every meter on your account (electricity, cold water, hot water, etc.) and creates two sensors per meter - `Last daily usage` and `Meter reading`.
- Imports historical readings as long-term statistics into Home Assistant.
- Allows you to label the measurement that each meter corresponds to.

## Installation

If you do not yet have HACS installed, begin with the [Getting started with HACS](https://hacs.xyz/docs/use/) instructions. Once installed, click on this badge from the machine Home Assistant is running on to add this repository to HACS:

[![Open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=felixvanoost&repository=ha-carma-direct-consumption&category=integration)

You can also add the repository manually:

1. In Home Assistant, open the HACS panel on the left sidebar, then select **Integrations**.
2. From the drop-down menu in the top right corner (three dots), select **Custom repositories**.
3. Enter the repository URL (`https://github.com/felixvanoost/ha-carma-direct-consumption`) and type (`integration`), then click on **Add**.
4. Find **CARMA Direct Consumption** in the HACS panel then select **Download** from the drop-down menu on the right.
5. Restart Home Assistant to apply the changes.

## Configuration

Once installed, add the CARMA Direct Consumption integration to Home Assistant and configure the meter roles:

1. In Home Assistant, open **Settings → Devices & services → Add integration**, then select **CARMA Direct Consumption**.
2. Enter your service address and account number exactly as they are displayed on the CARMA billing portal.
3. When prompted, select the type of measurement that each meter on your account corresponds to. If you're unsure, check the numbers against a recent CARMA bill.

Once configured, the reading history will be imported in the background and may take a few minutes.

## Usage

Past daily usage is saved as Home Assistant [long-term statistics](https://www.home-assistant.io/docs/configuration/long-term-statistics/). You can use this data in the Energy dashboard:

1. In Home Assistant, open the Energy dashboard via **Settings → Dashboards → Energy**.
2. From the Energy dashboard, click on **Edit** in the top right corner.
3. Under **Grid connections**, select the electricity meter(s) to use as the data source. If CARMA measures your water usage, you can also add water meter(s) under the 'Water Consumption' tab. The Energy dashboard supports an arbitrary number of meters, so you can monitor hot and cold water usage separately or track electricity usage for individually metered zones in a building.

The CARMA portal publishes daily readings and reports them about a day late. It may also revise recent readings, so the last 3 days of data is re-imported with every refresh.

> **Security:** The CARMA portal supports only HTTP. Login credentials and meter readings are sent in plaintext and can be read by others on the network.

## Troubleshooting

To enable debug logging, open **Tools → Actions** and paste the following YAML code:

```yaml
logger:
  logs:
    custom_components.carma_direct_consumption: debug
```

You can check the logs in **Settings → System → Logs** to see why an import failed:

```bash
Meter 00000000: read 148 day(s) from 5 month(s) (Jun 2026 to Oct 2026); stopped because: ...
```

To re-import meter data from scratch, run **Developer tools → Actions → CARMA Direct Consumption: Re-import history**.

## Contributing

Bug reports and pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.
