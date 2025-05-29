import logging
import socket
import time

import paho.mqtt.client
from kimiUtils.utils import Singleton


class MQTT(metaclass=Singleton):

    def __init__(self, connect_on_init=False):
        self.callback_dict = {}
        self.client = self.get_mqtt_client()
        # self.on_connect = self._on_connect
        # self.on_disconnect = self._on_disconnect
        # self.on_subscribe = self._on_subscribe
        if connect_on_init:
            self.connect()

    def _dummy(self, *args):
        log.info(f'Dummy callback function called with {args}.')

    def get_mqtt_client(self):
        try:
            mqttc = paho.mqtt.client.Client(paho.mqtt.client.CallbackAPIVersion.VERSION2)
        except AttributeError:
            mqttc = paho.mqtt.client.Client()

        mqttc.on_message = self._on_message
        mqttc.on_connect = self._on_connect
        mqttc.on_subscribe = self._on_subscribe
        mqttc.on_disconnect = self._on_disconnect
        return mqttc

    def connect(self, server=config.mqtt_server, port=config.mqtt_port):
        connected = False
        while not self.client.is_connected() and not killer.kill_now:
            try:
                # mqttc.connect_async(host=config.mqtt_server, port=config.mqtt_port)
                log.debug(f'Connecting to {server}:{port}')
                self.client.connect(host=server, port=port)
                self.client.loop_start()
                for _ in range(0,10):
                    time.sleep(1)
                    if self.client.is_connected() or killer.kill_now:
                        break
            except socket.error:
                log.error('Can\'t connect. Retrying in 5 sec..')
                time.sleep(5)

    def publish(self, *args, **kwargs):
        self.client.publish(*args, **kwargs)

    def subscribe(self, topic, callback):
        """ It is possible to subscribe before and after connection. """
        self.callback_dict[topic] = callback
        if self.client.is_connected():
            self.client.subscribe(topic)
            log.debug(f'Topic subscribed: {topic} → {callback.__qualname__}')

    def _subscribe_all_topics(self):
        for topic in self.callback_dict.keys():
            self.client.subscribe(topic)
            log.debug(f'Topic subscribed: {topic} → {self.callback_dict[topic].__qualname__}')

    def _on_message(self, client, userdata, msg):
        log.debug(f'MQTT msg: {msg.topic} - {msg.payload}')
        if msg.topic in self.callback_dict:
            self.callback_dict[msg.topic](msg)
        else:
            self._dummy(msg)

    def _on_connect(self, *args):
        log.info(f'MQTT connected.')
        self._subscribe_all_topics()

    def _on_disconnect(self, *args):
        log.info(f'MQTT disconnected.')

    def _on_subscribe(self, *args):
        pass
