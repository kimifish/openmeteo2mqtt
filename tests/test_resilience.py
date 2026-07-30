import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openmeteo2mqtt import main


class FetchWeatherResponseTests(unittest.TestCase):
    def test_http_request_has_timeout_and_session_is_closed(self):
        response = object()
        client = Mock()
        client.weather_api.return_value = [response]
        session = Mock()

        with patch.object(main, '_create_openmeteo_client', return_value=(client, session)):
            result = main._fetch_weather_response({'current': ['temperature_2m']}, 60)

        self.assertIs(result, response)
        client.weather_api.assert_called_once_with(
            main.cfg.url,
            params={'current': ['temperature_2m']},
            timeout=(
                main.cfg.http.connect_timeout_seconds,
                main.cfg.http.read_timeout_seconds,
            ),
        )
        session.close.assert_called_once_with()

    def test_session_is_closed_when_http_request_fails(self):
        client = Mock()
        client.weather_api.side_effect = TimeoutError('network stalled')
        session = Mock()

        with patch.object(main, '_create_openmeteo_client', return_value=(client, session)):
            with self.assertRaises(TimeoutError):
                main._fetch_weather_response({}, 60)

        session.close.assert_called_once_with()


class UpdateTests(unittest.TestCase):
    def test_temporary_failure_does_not_escape_update(self):
        mqttc = Mock()
        error = TimeoutError('network stalled')

        with patch.object(main, '_publish_status', return_value=True) as publish_status:
            result = main._run_update(mqttc, 'current', Mock(side_effect=error))

        self.assertFalse(result)
        publish_status.assert_called_once_with(mqttc, 'current', 'error', error)

    def test_success_is_reported_only_after_weather_is_published(self):
        mqttc = Mock()

        with patch.object(main, 'post_weather', return_value=True), \
                patch.object(main, '_publish_status', return_value=True) as publish_status:
            result = main._run_update(mqttc, 'current', lambda: {'current': '{}'})

        self.assertTrue(result)
        self.assertEqual(
            publish_status.call_args_list,
            [
                unittest.mock.call(mqttc, 'current', 'updating'),
                unittest.mock.call(mqttc, 'current', 'ok'),
            ],
        )

    def test_publish_failure_changes_status_to_error(self):
        mqttc = Mock()

        with patch.object(main, 'post_weather', return_value=False), \
                patch.object(main, '_publish_status', return_value=True) as publish_status:
            result = main._run_update(mqttc, 'current', lambda: {'current': '{}'})

        self.assertFalse(result)
        self.assertEqual(publish_status.call_args_list[0], unittest.mock.call(mqttc, 'current', 'updating'))
        self.assertEqual(publish_status.call_args_list[1].args[:3], (mqttc, 'current', 'error'))

    def test_worker_backoff_grows_and_resets_after_success(self):
        waits = []

        def record_sleep(seconds):
            waits.append(seconds)
            if len(waits) == 3:
                main.cfg.killer.kill_now = True

        with patch.object(main.cfg.killer, 'kill_now', False), \
                patch.object(main.cfg.retry, 'initial_delay_seconds', 5), \
                patch.object(main.cfg.retry, 'max_delay_seconds', 180), \
                patch.object(main.cfg.retry, 'multiplier', 2), \
                patch.object(main, '_run_update', side_effect=[False, False, True]), \
                patch.object(main, '_interruptible_sleep', side_effect=record_sleep), \
                patch.object(main.random, 'uniform', return_value=0):
            main._run_update_worker(Mock(), 'current', Mock(), interval_minutes=15)

        self.assertEqual(waits, [5, 10, 900])

    def test_retry_delay_never_exceeds_configured_maximum(self):
        with patch.object(main.random, 'uniform', return_value=main.cfg.retry.jitter_seconds):
            delay = main._get_retry_delay(main.cfg.retry.max_delay_seconds)

        self.assertEqual(delay, main.cfg.retry.max_delay_seconds)

    def test_finished_fetch_is_not_published_after_shutdown(self):
        mqttc = Mock()

        def finish_during_shutdown():
            main.cfg.killer.kill_now = True
            return {'current': '{}'}

        with patch.object(main.cfg.killer, 'kill_now', False), \
                patch.object(main, 'post_weather') as post_weather:
            result = main._run_update(mqttc, 'current', finish_during_shutdown)

        self.assertFalse(result)
        post_weather.assert_not_called()


class MqttPublishTests(unittest.TestCase):
    def test_missing_qos_ack_is_treated_as_failure(self):
        message = SimpleNamespace(
            rc=0,
            wait_for_publish=Mock(),
            is_published=Mock(return_value=False),
        )
        client = Mock()
        client.publish.return_value = message
        mqttc = SimpleNamespace(client=client)

        with patch.object(main, '_connect_mqtt', return_value=True):
            result = main._publish_message(mqttc, 'weather/current', '{}')

        self.assertFalse(result)
        message.wait_for_publish.assert_called_once_with(
            timeout=main.cfg.mqtt.publish_timeout_seconds,
        )

    def test_availability_is_published_after_every_connect(self):
        original_on_connect = Mock()
        message = SimpleNamespace(rc=0)
        client = Mock(on_connect=original_on_connect)
        client.publish.return_value = message
        mqttc = SimpleNamespace(client=client)

        topic = main._configure_mqtt_availability(mqttc)
        client.on_connect('client', None, {}, 0)

        original_on_connect.assert_called_once_with('client', None, {}, 0)
        client.publish.assert_called_once_with(
            topic,
            'online',
            qos=main.cfg.mqtt.qos,
            retain=True,
        )

    def test_shutdown_stops_loop_when_client_is_disconnected(self):
        client = Mock()
        client.is_connected.return_value = False
        mqttc = SimpleNamespace(
            client=client,
            loop_stop=Mock(),
            shutdown=False,
            connected=True,
        )

        main._shutdown_mqtt(mqttc, 'weather/availability')

        self.assertTrue(mqttc.shutdown)
        self.assertFalse(mqttc.connected)
        client.disconnect.assert_called_once_with()
        mqttc.loop_stop.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
