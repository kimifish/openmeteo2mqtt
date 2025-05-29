#  -*- coding: utf-8 -*-
# pyright: basic
# pyright: reportAttributeAccessIssue=false

import json
import logging
import sys
import time
from datetime import datetime

import openmeteo_requests
import pandas as pd
import requests_cache
import schedule
from openmeteo_sdk.Variable import Variable
from openmeteo_sdk.VariablesWithTime import VariablesWithTime
from openmeteo_sdk.WeatherApiResponse import WeatherApiResponse
from retry_requests import retry
from kimqtt import MQTT

from openmeteo2mqtt.config import cfg, APP_NAME


log = logging.getLogger(f"{APP_NAME}.{__name__}")


def _create_dict_with_variable_types():
    """ Creates inverted dict like {0: 'undefined', 1: 'apparent_temperature', 2: 'cape', 3: 'cloud_cover',...} """
    var_dict = dict()
    for var in Variable.__dict__.keys():
        if var.startswith('_'):
            continue
        var_dict[Variable.__dict__[var]] = var
    return var_dict


weather_vars = _create_dict_with_variable_types()


def subscribe_mqtt_topics(mqttc):
    pass


def process_current_data_to_JSON(current: VariablesWithTime):
    # Current values. The order of variables needs to be the same as requested.
    current_dict = dict()
    for _ in range(0, current.VariablesLength()):
        name = cfg.params.current[_]
        value = current.Variables(_)
        if value is not None:
            value = value.Value()
        if type(value) == float:
            value = round(value, 2)
        current_dict[name] = value
    current_dict['weather_condition'] = cfg.weather_codes[current_dict['weather_code']]
    current_dict['relative_humidity_2m'] = current_dict['relative_humidity_2m']/100
    return json.dumps(current_dict)


def process_location_data_to_JSON(response: WeatherApiResponse):
    current_dict = dict()
    current_dict['latitude'] = response.Latitude()
    current_dict['longitude'] = response.Longitude()
    current_dict['elevation'] = response.Elevation()
    tz_abbr = response.TimezoneAbbreviation()
    if tz_abbr is not None:
        current_dict['timezone'] = tz_abbr.decode('utf8')
    current_dict['time'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
    return json.dumps(current_dict)


# TODO: Make it create JSON
def process_hourly_forecast_data_to_JSON(hourly):
    data_dict = dict()
    # Process hourly data. The order of variables needs to be the same as requested.
    hourly_temperature_2m = hourly.Variables(0).ValuesAsNumpy()
    hourly_relative_humidity_2m = hourly.Variables(1).ValuesAsNumpy()
    hourly_apparent_temperature = hourly.Variables(2).ValuesAsNumpy()
    hourly_precipitation_probability = hourly.Variables(3).ValuesAsNumpy()
    hourly_rain = hourly.Variables(4).ValuesAsNumpy()
    hourly_snowfall = hourly.Variables(5).ValuesAsNumpy()
    hourly_weather_code = hourly.Variables(6).ValuesAsNumpy()
    hourly_surface_pressure = hourly.Variables(7).ValuesAsNumpy()
    hourly_wind_speed_10m = hourly.Variables(8).ValuesAsNumpy()

    hourly_data = dict(date=pd.date_range(start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
                                          end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                                          freq=pd.Timedelta(seconds=hourly.Interval()),
                                          inclusive="left"
                                          )
                       )
    hourly_data["temperature_2m"] = hourly_temperature_2m
    hourly_data["relative_humidity_2m"] = hourly_relative_humidity_2m
    hourly_data["apparent_temperature"] = hourly_apparent_temperature
    hourly_data["precipitation_probability"] = hourly_precipitation_probability
    hourly_data["rain"] = hourly_rain
    hourly_data["snowfall"] = hourly_snowfall
    hourly_data["weather_code"] = hourly_weather_code
    hourly_data["surface_pressure"] = hourly_surface_pressure
    hourly_data["wind_speed_10m"] = hourly_wind_speed_10m

    hourly_dataframe = pd.DataFrame(data=hourly_data)
    # print(hourly_dataframe)
    ser = hourly_dataframe['surface_pressure']
    # print(ser.get('2024-10-07 21:00:00+00:00'))
    # timestamp = datetime.utcnow()
    # timestamp = timestamp.replace(hour=21, minute=0, second=0, microsecond=0)
    # print(daily_dataframe.loc[timestamp])
    return data_dict

# TODO: Make it create JSON
def process_daily_forecast_data_to_JSON(daily):
    data_dict = dict()
    # Process daily data. The order of variables needs to be the same as requested.
    daily_precipitation_probability_max = daily.Variables(0).ValuesAsNumpy()

    daily_data = dict(date=pd.date_range(start=pd.to_datetime(daily.Time(), unit="s", utc=False),
                                         end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=False),
                                         freq=pd.Timedelta(seconds=daily.Interval()),
                                         inclusive="left"
                                         )
                      )
    daily_data["precipitation_probability_max"] = daily_precipitation_probability_max
    daily_dataframe = pd.DataFrame(data=daily_data)
    #print(daily_dataframe)
    return data_dict


def get_openmeteo_data():
    # Setup the Open-Meteo API client with cache and retry on error
    log.debug('Updating weather data...')
    cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
    retry_session = retry(cache_session, retries=99999, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)  # pyright: ignore
    responses = openmeteo.weather_api(cfg.url, params=cfg.data['params'])
    response = responses[0]

    location_json = process_location_data_to_JSON(response)
    current_json = process_current_data_to_JSON(response.Current())  # pyright: ignore
    hourly_json = process_hourly_forecast_data_to_JSON(response.Hourly())
    daily_json = process_daily_forecast_data_to_JSON(response.Daily())
    # log.debug(location_json)

    data_dict = dict()
    data_dict['location'] = location_json
    data_dict['current'] = current_json
    data_dict['hourly'] = hourly_json
    data_dict['daily'] = daily_json
    return data_dict


def post_weather(mqttc, data_dict):
    log.debug('Posting weather data...')
    for k in data_dict.keys():
        if len(data_dict[k]) > 0:  # If dictionary isn't empty
            mqttc.publish(topic=f'{cfg.mqtt.topic}/{k}', payload=data_dict[k])


def retrieve_and_post_weather(mqttc):
    post_weather(mqttc, get_openmeteo_data())
    log.debug('Done.')


def main():
    mqttc = MQTT(host=cfg.mqtt.server, port=cfg.mqtt.port)
    subscribe_mqtt_topics(mqttc)
    mqttc.connect()
    retrieve_and_post_weather(mqttc)
    schedule.every(cfg.update_interval_in_minutes).minutes.do(retrieve_and_post_weather, mqttc=mqttc)

    while not cfg.killer.kill_now:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    log.info('OpenMeteo2MQTT started.')
    main()
