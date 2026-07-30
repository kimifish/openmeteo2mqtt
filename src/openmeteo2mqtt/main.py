#  -*- coding: utf-8 -*-
# pyright: basic
# pyright: reportAttributeAccessIssue=false

import copy
import json
import logging
import math
import time
from datetime import datetime

import openmeteo_requests
import pandas as pd
import requests_cache
import schedule
from openmeteo_sdk.VariablesWithTime import VariablesWithTime
from openmeteo_sdk.WeatherApiResponse import WeatherApiResponse
from retry_requests import retry
from kimqtt import MQTT

from openmeteo2mqtt.config import cfg, APP_NAME


log = logging.getLogger(f"{APP_NAME}.{__name__}")

FREE_TIER_LIMITS = {
    'per_minute': 600,
    'per_hour': 5000,
    'per_day': 10000,
    'per_month': 300000,
}
FORECAST_HOURS = 24
DEFAULT_FORECAST_UPDATE_INTERVAL_IN_MINUTES = 60
HTTP_STATUS_TO_RETRY = (429, 500, 502, 503, 504)

def subscribe_mqtt_topics(mqttc):
    pass


def _build_request_params(include_current=False, include_hourly=False, include_daily=False):
    params = copy.deepcopy(cfg.data['params'])

    if not include_current:
        params.pop('current', None)
    if not include_hourly:
        params.pop('hourly', None)
        params.pop('forecast_hours', None)
        params.pop('past_hours', None)
        params.pop('start_hour', None)
        params.pop('end_hour', None)
    else:
        params['forecast_hours'] = FORECAST_HOURS

    if not include_daily:
        params.pop('daily', None)
        params.pop('forecast_days', None)
        params.pop('past_days', None)
        params.pop('start_date', None)
        params.pop('end_date', None)

    return params


def _create_openmeteo_client(expire_after_seconds):
    cache_session = requests_cache.CachedSession('.cache', expire_after=expire_after_seconds)

    if hasattr(cfg, 'proxy') and hasattr(cfg.proxy, 'url') and hasattr(cfg.proxy, 'port'):
        if cfg.proxy.url and cfg.proxy.port:
            log.debug(f'Using SOCKS5 proxy: {cfg.proxy.url}:{cfg.proxy.port}')
            cache_session.proxies = {
                'http': f'socks5://{cfg.proxy.url}:{cfg.proxy.port}',
                'https': f'socks5://{cfg.proxy.url}:{cfg.proxy.port}'
            }

    retry_session = retry(
        cache_session,
        retries=int(cfg.http.retries),
        backoff_factor=cfg.http.backoff_factor,
        status_to_retry=HTTP_STATUS_TO_RETRY,
        # A server-provided Retry-After must not block shutdown for an arbitrary time.
        respect_retry_after_header=False,
    )
    client = openmeteo_requests.Client(session=retry_session)  # pyright: ignore
    return client, retry_session


def _normalize_value(value, variable_name=None):
    if value is None:
        return None

    if hasattr(value, 'item'):
        value = value.item()

    if isinstance(value, float):
        if math.isnan(value):
            return None
        value = round(value, 2)

    if variable_name == 'relative_humidity_2m' and value is not None:
        value = round(value / 100, 4)

    return value


def _get_weather_condition(weather_code):
    if hasattr(cfg, 'weather_codes') and cfg.weather_codes:
        return cfg.data['weather_codes'].get(weather_code, 'unknown')
    return 'unknown'


def _build_time_index(variables_with_time, utc_offset_seconds):
    start = pd.to_datetime(variables_with_time.Time() + utc_offset_seconds, unit='s', utc=True)
    end = pd.to_datetime(variables_with_time.TimeEnd() + utc_offset_seconds, unit='s', utc=True)
    return pd.date_range(
        start=start,
        end=end,
        freq=pd.Timedelta(seconds=variables_with_time.Interval()),
        inclusive='left'
    ).tz_localize(None)


def _process_forecast_data_to_JSON(variables_with_time, variable_names, utc_offset_seconds, time_key, time_format):
    if variables_with_time is None or not variable_names:
        return ''

    time_index = _build_time_index(variables_with_time, utc_offset_seconds)
    rows = []

    for row_index, timestamp in enumerate(time_index):
        row = {time_key: timestamp.strftime(time_format)}
        for variable_index, variable_name in enumerate(variable_names):
            variable = variables_with_time.Variables(variable_index)
            values = variable.ValuesAsNumpy() if variable is not None else []
            value = values[row_index] if row_index < len(values) else None
            row[variable_name] = _normalize_value(value, variable_name)

        if 'weather_code' in row:
            row['weather_condition'] = _get_weather_condition(row['weather_code'])

        rows.append(row)

    return json.dumps({
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'entries': rows,
    })


def _estimate_api_call_cost(variable_names):
    if not variable_names:
        return 0.0
    return max(1.0, round(len(variable_names) / 10, 2))


def _estimate_usage(interval_minutes, variable_names):
    api_calls_per_request = _estimate_api_call_cost(variable_names)
    if interval_minutes <= 0 or api_calls_per_request == 0:
        return None

    requests_per_day = 1440 / interval_minutes
    requests_per_month = requests_per_day * 30

    return {
        'api_calls_per_request': api_calls_per_request,
        'requests_per_day': round(requests_per_day, 2),
        'requests_per_month': round(requests_per_month, 2),
        'api_calls_per_day': round(api_calls_per_request * requests_per_day, 2),
        'api_calls_per_month': round(api_calls_per_request * requests_per_month, 2),
    }


def _get_forecast_update_interval_in_minutes():
    configured_interval = getattr(getattr(cfg, 'forecast', object()), 'update_interval_in_minutes', None)
    if configured_interval:
        return configured_interval
    return max(cfg.update_interval_in_minutes, DEFAULT_FORECAST_UPDATE_INTERVAL_IN_MINUTES)


def _log_usage_budget():
    current_usage = _estimate_usage(cfg.update_interval_in_minutes, getattr(cfg.params, 'current', []))
    forecast_variables = list(getattr(cfg.params, 'hourly', [])) + list(getattr(cfg.params, 'daily', []))
    forecast_usage = _estimate_usage(_get_forecast_update_interval_in_minutes(), forecast_variables)

    if current_usage:
        log.info(
            'Current weather refresh every %s minutes: %.2f API calls/request, %.2f API calls/day, %.2f API calls/month',
            cfg.update_interval_in_minutes,
            current_usage['api_calls_per_request'],
            current_usage['api_calls_per_day'],
            current_usage['api_calls_per_month'],
        )

    if forecast_usage:
        log.info(
            'Forecast refresh every %s minutes: %.2f API calls/request, %.2f API calls/day, %.2f API calls/month. Free tier limits: %s/day, %s/month',
            _get_forecast_update_interval_in_minutes(),
            forecast_usage['api_calls_per_request'],
            forecast_usage['api_calls_per_day'],
            forecast_usage['api_calls_per_month'],
            FREE_TIER_LIMITS['per_day'],
            FREE_TIER_LIMITS['per_month'],
        )


def process_current_data_to_JSON(current: VariablesWithTime):
    # Current values. The order of variables needs to be the same as requested.
    current_dict = dict()
    for _ in range(0, current.VariablesLength()):
        name = cfg.params.current[_]
        value = current.Variables(_)
        if value is not None:
            value = value.Value()
        current_dict[name] = _normalize_value(value, name)
    # Add weather condition description if weather_codes are configured
    current_dict['weather_condition'] = _get_weather_condition(current_dict.get('weather_code'))
    return json.dumps(current_dict)


def process_location_data_to_JSON(response: WeatherApiResponse):
    current_dict = dict()
    current_dict['latitude'] = response.Latitude()
    current_dict['longitude'] = response.Longitude()
    current_dict['elevation'] = response.Elevation()
    tz_abbr = response.TimezoneAbbreviation()
    if tz_abbr is not None:
        current_dict['timezone'] = tz_abbr.decode('utf8')
    current_dict['time'] = datetime.now().isoformat(timespec='seconds')
    return json.dumps(current_dict)


def process_hourly_forecast_data_to_JSON(hourly, utc_offset_seconds):
    return _process_forecast_data_to_JSON(
        hourly,
        getattr(cfg.params, 'hourly', []),
        utc_offset_seconds,
        'time',
        '%Y-%m-%dT%H:%M:%S',
    )

def process_daily_forecast_data_to_JSON(daily, utc_offset_seconds):
    return _process_forecast_data_to_JSON(
        daily,
        getattr(cfg.params, 'daily', []),
        utc_offset_seconds,
        'date',
        '%Y-%m-%d',
    )


def _fetch_weather_response(params, expire_after_seconds):
    if cfg.killer.kill_now:
        return None

    openmeteo, session = _create_openmeteo_client(expire_after_seconds)
    try:
        responses = openmeteo.weather_api(
            cfg.url,
            params=params,
            timeout=(cfg.http.connect_timeout_seconds, cfg.http.read_timeout_seconds),
        )
    finally:
        session.close()

    if cfg.killer.kill_now:
        return None

    if not responses:
        raise RuntimeError('Open-Meteo returned an empty response')

    return responses[0]


def get_current_weather_data():
    log.debug('Updating current weather data...')
    response = _fetch_weather_response(
        _build_request_params(include_current=True),
        expire_after_seconds=min(cfg.update_interval_in_minutes, 5) * 60,
    )
    if response is None:
        return {}

    return {
        'location': process_location_data_to_JSON(response),
        'current': process_current_data_to_JSON(response.Current()),  # pyright: ignore
    }


def get_forecast_weather_data():
    log.debug('Updating forecast weather data...')
    include_hourly = bool(getattr(cfg.params, 'hourly', []))
    include_daily = bool(getattr(cfg.params, 'daily', []))
    if not include_hourly and not include_daily:
        return {}

    response = _fetch_weather_response(
        _build_request_params(include_hourly=include_hourly, include_daily=include_daily),
        expire_after_seconds=_get_forecast_update_interval_in_minutes() * 60,
    )
    if response is None:
        return {}

    utc_offset_seconds = response.UtcOffsetSeconds()
    data_dict = dict()
    if include_hourly:
        data_dict['hourly'] = process_hourly_forecast_data_to_JSON(response.Hourly(), utc_offset_seconds)
    if include_daily:
        data_dict['daily'] = process_daily_forecast_data_to_JSON(response.Daily(), utc_offset_seconds)
    return data_dict


def _interruptible_sleep(seconds):
    deadline = time.monotonic() + seconds
    while not cfg.killer.kill_now and time.monotonic() < deadline:
        time.sleep(max(0, min(0.1, deadline - time.monotonic())))


def _configure_mqtt_availability(mqttc):
    availability_topic = f'{cfg.mqtt.topic}/availability'
    client = mqttc.client
    client.will_set(availability_topic, 'offline', qos=cfg.mqtt.qos, retain=True)
    original_on_connect = client.on_connect

    def on_connect(*args, **kwargs):
        if original_on_connect is not None:
            original_on_connect(*args, **kwargs)
        message = client.publish(
            availability_topic,
            'online',
            qos=cfg.mqtt.qos,
            retain=True,
        )
        if int(message.rc) != 0:
            log.error('Failed to publish online availability: %s', message.rc)

    client.on_connect = on_connect
    return availability_topic


def _connect_mqtt(mqttc):
    client = getattr(mqttc, 'client', None)
    if client is None:
        log.error('MQTT client is not initialized')
        return False
    if client.is_connected():
        return True

    mqttc.shutdown = False
    if hasattr(client, 'connect_timeout'):
        client.connect_timeout = cfg.mqtt.connect_timeout_seconds

    hosts = mqttc.host if isinstance(mqttc.host, list) else [mqttc.host]
    attempts = int(cfg.mqtt.reconnect_attempts)
    for attempt in range(1, attempts + 1):
        for host in hosts:
            if cfg.killer.kill_now:
                return False
            try:
                log.info('Connecting to MQTT broker %s:%s (attempt %s/%s)', host, mqttc.port, attempt, attempts)
                result = client.connect(host=host, port=mqttc.port, keepalive=mqttc.keepalive)
                if int(result) != 0:
                    raise ConnectionError(f'MQTT connect returned {result}')
                mqttc.loop_start()

                deadline = time.monotonic() + cfg.mqtt.connect_timeout_seconds
                while not cfg.killer.kill_now and time.monotonic() < deadline:
                    if client.is_connected():
                        mqttc.connected = True
                        log.info('MQTT connected to %s:%s', host, mqttc.port)
                        return True
                    time.sleep(0.1)
                raise TimeoutError('MQTT CONNACK timed out')
            except (OSError, ConnectionError, TimeoutError, RuntimeError, ValueError) as error:
                log.warning('MQTT connection to %s:%s failed: %s', host, mqttc.port, error)
                try:
                    client.disconnect()
                    mqttc.loop_stop()
                except Exception:
                    log.debug('Failed to clean up an unsuccessful MQTT connection', exc_info=True)

        if attempt < attempts:
            _interruptible_sleep(cfg.mqtt.retry_delay_seconds)

    log.error('MQTT connection failed after %s attempts', attempts)
    return False


def _publish_message(mqttc, topic, payload, retain=None):
    if not _connect_mqtt(mqttc):
        return False

    retain = cfg.mqtt.retain if retain is None else retain
    try:
        message = mqttc.client.publish(
            topic,
            payload,
            qos=cfg.mqtt.qos,
            retain=retain,
        )
        if int(message.rc) != 0:
            log.error('MQTT publish to %s failed with code %s', topic, message.rc)
            return False
        if cfg.mqtt.qos > 0:
            message.wait_for_publish(timeout=cfg.mqtt.publish_timeout_seconds)
            if not message.is_published():
                log.error('MQTT publish acknowledgement timed out for %s', topic)
                return False
        return True
    except (OSError, RuntimeError, ValueError) as error:
        log.error('MQTT publish to %s failed: %s', topic, error)
        return False


def _publish_status(mqttc, update_name, state, error=None):
    status = {
        'state': state,
        'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
    }
    if error is not None:
        status['error'] = type(error).__name__
    return _publish_message(
        mqttc,
        f'{cfg.mqtt.topic}/status/{update_name}',
        json.dumps(status),
        retain=True,
    )


def _shutdown_mqtt(mqttc, availability_topic):
    mqttc.shutdown = True
    if mqttc.client.is_connected():
        _publish_message(mqttc, availability_topic, 'offline', retain=True)
    try:
        mqttc.client.disconnect()
    except Exception:
        log.warning('MQTT disconnect failed', exc_info=True)
    finally:
        mqttc.loop_stop()
        mqttc.connected = False


def post_weather(mqttc, data_dict):
    if cfg.killer.kill_now:
        return False
    log.debug('Posting weather data...')
    for key, payload in data_dict.items():
        if cfg.killer.kill_now:
            return False
        if payload and not _publish_message(mqttc, f'{cfg.mqtt.topic}/{key}', payload):
            return False
    return True


def _run_update(mqttc, update_name, get_data):
    if cfg.killer.kill_now:
        return False
    try:
        data = get_data()
        if not data:
            return True
        if not _publish_status(mqttc, update_name, 'updating'):
            return False
        if not post_weather(mqttc, data):
            publish_error = RuntimeError('MQTT weather publish failed')
            _publish_status(mqttc, update_name, 'error', publish_error)
            return False
        _publish_status(mqttc, update_name, 'ok')
        log.debug('%s update completed', update_name.capitalize())
        return True
    except Exception as error:
        log.exception('%s update failed; the service will retry on schedule', update_name.capitalize())
        _publish_status(mqttc, update_name, 'error', error)
        return False


def retrieve_and_post_weather(mqttc):
    return _run_update(mqttc, 'current', get_current_weather_data)


def retrieve_and_post_forecast(mqttc):
    return _run_update(mqttc, 'forecast', get_forecast_weather_data)


def main():
    _log_usage_budget()
    mqttc = MQTT(host=cfg.mqtt.server, port=cfg.mqtt.port)
    subscribe_mqtt_topics(mqttc)
    availability_topic = _configure_mqtt_availability(mqttc)
    schedule.clear()
    schedule.every(cfg.update_interval_in_minutes).minutes.do(retrieve_and_post_weather, mqttc=mqttc)
    if getattr(cfg.params, 'hourly', []) or getattr(cfg.params, 'daily', []):
        schedule.every(_get_forecast_update_interval_in_minutes()).minutes.do(retrieve_and_post_forecast, mqttc=mqttc)

    try:
        _connect_mqtt(mqttc)
        retrieve_and_post_weather(mqttc)
        retrieve_and_post_forecast(mqttc)

        while not cfg.killer.kill_now:
            try:
                schedule.run_pending()
            except Exception:
                log.exception('Unexpected scheduler error; continuing')
            _interruptible_sleep(1)
    finally:
        schedule.clear()
        _shutdown_mqtt(mqttc, availability_topic)
        log.info('OpenMeteo2MQTT stopped')


if __name__ == "__main__":
    log.info('OpenMeteo2MQTT started.')
    main()
