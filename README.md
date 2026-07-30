# OpenMeteo2MQTT

Populates MQTT topics with Open-Meteo weather data.

This project was initially developed for personal use, but feel free to use and adapt it as you wish.

## License

This project is licensed under the MIT License.

## Features

*   Fetches current weather data, hourly forecasts for the next 24 hours, and daily forecasts from the [Open-Meteo API](https://open-meteo.com/).
*   Publishes weather data to specified MQTT topics.
*   Periodically updates weather data at a configurable interval.
*   Highly configurable via a YAML file (`config.yaml`).
*   Supports caching of API requests to reduce load and improve speed.
*   Uses bounded HTTP and MQTT timeouts, retries, and backoff so temporary network failures do not stop the service.
*   Publishes retained availability and per-update status topics for stale-data monitoring.
*   Provides human-readable weather condition descriptions based on weather codes.
*   **NEW:** Supports SOCKS5 proxy for accessing Open-Meteo API (useful for bypassing regional restrictions).

## Prerequisites

*   Python 3.10 or higher.
*   An MQTT broker.

## Installation

1.  **Clone the repository (if you haven't already):**
    ```bash
    git clone <repository_url>
    cd openmeteo2mqtt
    ```

2.  **Install using `uv` (recommended, based on [`pyproject.toml`](pyproject.toml:1)):**
    If you don't have `uv`, you can install it following the instructions [here](https://github.com/astral-sh/uv).
    ```bash
    uv pip install .
    ```
    Alternatively, for development:
    ```bash
    uv pip install -e .[dev]
    ```

3.  **Or, install using `pip`:**
    ```bash
    pip install .
    ```
    For development:
    ```bash
    pip install -e .[dev]
    ```

This will also install all necessary dependencies listed in [`pyproject.toml`](pyproject.toml:1).

## Configuration

The application is configured using a YAML file. By default, it looks for `config.yaml` in the following locations (in order of preference):
1.  Path specified by the `-c` or `--config` command-line argument.
2.  `$XDG_CONFIG_HOME/openmeteo2mqtt/config.yaml` (e.g., `~/.config/openmeteo2mqtt/config.yaml`)
3.  `~/.config/openmeteo2mqtt/config.yaml` (if `$XDG_CONFIG_HOME` is not set)

Create a `config.yaml` file. You can start by copying `config.example.yaml` from the repository or use the example below:

```yaml
update_interval_in_minutes: 15 # How often to fetch new current weather data

forecast:
  update_interval_in_minutes: 60 # Optional. Defaults to max(update_interval_in_minutes, 60)

mqtt:
  server: "mqtt.lan" # Your MQTT broker address (can be a list for failover)
  # server:
  #   - "mqtt.primary.lan"
  #   - "mqtt.secondary.lan"
  port: 1883 # Your MQTT broker port
  topic: "weather" # Base MQTT topic for weather data
  connect_timeout_seconds: 10
  publish_timeout_seconds: 10
  reconnect_attempts: 3
  retry_delay_seconds: 5
  qos: 1 # Wait for broker acknowledgement
  retain: true # Keep the last successful values for reconnecting consumers

http:
  connect_timeout_seconds: 10
  read_timeout_seconds: 30
  retries: 4
  backoff_factor: 1

retry:
  initial_delay_seconds: 5
  max_delay_seconds: 180
  multiplier: 2
  jitter_seconds: 5

# Open-Meteo API URL (usually no need to change)
url: "https://api.open-meteo.com/v1/forecast"

# Parameters for the Open-Meteo API
params:
  latitude: 55.75 # Your latitude
  longitude: 37.62 # Your longitude
  timezone: "Europe/Moscow" # Your timezone
  forecast_days: 3 # Number of days for daily forecast
  # Hourly forecast is always limited to the next 24 hours via forecast_hours=24

  # Weather variables to fetch for current weather
  current:
    - "temperature_2m"
    - "relative_humidity_2m"
    - "apparent_temperature"
    - "precipitation"
    - "rain"
    - "showers"
    - "snowfall"
    - "weather_code"
    - "cloud_cover"
    - "surface_pressure"
    - "wind_speed_10m"
    - "wind_direction_10m"

  # Weather variables to fetch for hourly forecast
  hourly:
    - "temperature_2m"
    - "relative_humidity_2m"
    - "apparent_temperature"
    - "precipitation_probability"
    - "rain"
    - "snowfall"
    - "weather_code"
    - "cloud_cover"
    - "surface_pressure"
    - "wind_speed_10m"
    - "wind_direction_10m"

  # Weather variables to fetch for daily forecast
  daily:
    - "precipitation_probability_max"
    # Add more daily variables as needed from Open-Meteo documentation

# Mapping of Open-Meteo weather codes to human-readable strings (in Russian by default)
weather_codes:
  0: "ясно"
  1: "малооблачно"
  2: "облачно"
  3: "сплошная облачность"
  45: "туман"
  48: "сильный туман"
  51: "небольшая морось"
  53: "морось"
  55: "сильная морось"
  56: "лёгкая ледяная морось"
  57: "ледяная морось"
  61: "лёгкий дождь"
  63: "дождь"
  65: "сильный дождь"
  66: "лёгкий ледяной дождь"
  67: "ледяной дождь"
  71: "лёгкий снег"
  73: "снег"
  75: "сильный снег"
  77: "снежные зёрна"
  80: "лёгкий ливень"
  81: "ливень"
  82: "сильный ливень"
  85: "снегопад"
  86: "сильный снегопад"
  95: "гроза"
  96: "град"
  99: "сильный град с грозой"
```
For a full list of weather codes, refer to your `config.yaml` or the Open-Meteo documentation.

## SOCKS5 Proxy Support

If you encounter issues accessing the Open-Meteo API from your location (due to regional restrictions), you can configure the application to use a SOCKS5 proxy:

```yaml
# Optional SOCKS5 proxy settings for accessing Open-Meteo API
proxy:
  url: "your-proxy-server.com"
  port: 1080
```

When proxy settings are configured, all API requests to Open-Meteo will be routed through the specified SOCKS5 proxy. If proxy settings are not provided or are empty, the application will connect directly to the API.

## Usage

Once installed and configured, you can run the application using the following command:

```bash
openmeteo2mqtt
```

You can also specify a custom configuration file:

```bash
openmeteo2mqtt --config /path/to/your/custom_config.yaml
```

The application will then start fetching weather data and publishing it to your MQTT broker.

## MQTT Topics

Data is published to subtopics under the base topic defined in your `config.yaml` (e.g., `weather/`).
The following subtopics are used:

*   `{base_topic}/location`: JSON string with location data (latitude, longitude, elevation, timezone).
    Example: `weather/location`
    ```json
    {
      "latitude": 55.75,
      "longitude": 37.62,
      "elevation": 156.0,
      "timezone": "MSK",
      "time": "2024-05-29T18:30:00.123456"
    }
    ```
*   `{base_topic}/current`: JSON string with current weather conditions.
    Example: `weather/current`
    ```json
    {
      "temperature_2m": 15.5,
      "relative_humidity_2m": 0.65,
      "apparent_temperature": 14.8,
      "precipitation": 0.0,
      "rain": 0.0,
      "showers": 0.0,
      "snowfall": 0.0,
      "weather_code": 2,
      "weather_condition": "облачно",
      "cloud_cover": 75,
      "surface_pressure": 1012.5,
      "wind_speed_10m": 10.8,
      "wind_direction_10m": 270
    }
    ```
*   `{base_topic}/hourly`: JSON string with hourly forecast data for the next 24 hours.
    Example: `weather/hourly`
*   `{base_topic}/daily`: JSON string with daily forecast data.
    Example: `weather/daily`
    Both forecast topics now publish structured JSON in the form:
    ```json
    {
      "updated_at": "2026-04-15T12:00:00",
      "entries": [
        {
          "time": "2026-04-15T13:00:00",
          "temperature_2m": 12.3,
          "weather_code": 3,
          "weather_condition": "сплошная облачность"
        }
      ]
    }
    ```
*   `{base_topic}/availability`: retained `online`/`offline` service availability. The MQTT last will publishes `offline` after an ungraceful disconnect.
*   `{base_topic}/status/current` and `{base_topic}/status/forecast`: retained JSON status. A failed update is reported as `state: error` without terminating the service. Current weather and forecast retry independently forever, using exponential backoff capped at three minutes, and return to their normal intervals after a successful update.

## Open-Meteo Limits

Open-Meteo free tier currently lists these limits on the pricing page:

*   600 calls per minute
*   5,000 calls per hour
*   10,000 calls per day
*   300,000 calls per month

Open-Meteo also states that requests with more than 10 variables count as multiple API calls. With the example configuration in this README:

*   current request: 12 variables, so about `1.2` API calls per request
*   forecast request: 11 hourly + 1 daily variable, so about `1.2` API calls per request
*   current updates every 15 minutes: about `115.2` API calls per day and `3456` per 30-day month
*   forecast updates every 60 minutes: about `28.8` API calls per day and `864` per 30-day month
*   total: about `144` API calls per day and `4320` per 30-day month

That is far below the free-tier ceilings, so the application now refreshes forecasts hourly by default. This also matches Open-Meteo's own recommendation to update forecast data every hour.

## Dependencies

Key dependencies include:
*   [`openmeteo-requests`](https://pypi.org/project/openmeteo-requests/): For interacting with the Open-Meteo API.
*   [`openmeteo-sdk`](https://pypi.org/project/openmeteo-sdk/): SDK for Open-Meteo.
*   [`pandas`](https://pandas.pydata.org/): For data manipulation.
*   [`requests-cache`](https://pypi.org/project/requests-cache/): For caching API requests.
*   [`retry-requests`](https://pypi.org/project/retry-requests/): For retrying failed requests.
*   [`rich`](https://pypi.org/project/rich/): For rich terminal output and logging.
*   [`kimiconfig`](https://pypi.org/project/kimiconfig/): For configuration management.
*   [`kimqtt`](https://pypi.org/project/kimqtt/): For MQTT communication.
*   [`kimiutils`](https://pypi.org/project/kimiutils/): Utility functions.

For a full list, please see the [`pyproject.toml`](pyproject.toml:1) file.

## Contributing

This project was made for personal needs, but contributions, bug reports, and feature requests are welcome! Please feel free to open an issue or submit a pull request.

## Troubleshooting

*   Ensure your MQTT broker is running and accessible.
*   Check your `config.yaml` for correct latitude, longitude, and MQTT settings.
*   Enable debug logging for more detailed output. The logging level can be set in `config.yaml` under a `logging.level` key (e.g., `logging:\n  level: DEBUG`). Refer to [`src/openmeteo2mqtt/config.py`](src/openmeteo2mqtt/config.py:1) for how logging is configured.
