"""Support for Apple push notification service."""
import logging
import os

from homeassistant.helpers.event import track_state_change
from homeassistant.config import load_yaml_config_file
from homeassistant.components.notify import (
    ATTR_TARGET, ATTR_DATA, BaseNotificationService)

DOMAIN = "apns"
APNS_DEVICES = "apns.yaml"
DEVICE_TRACKER_DOMAIN = "device_tracker"


def get_service(hass, config):
    """Return push service"""
    name = config.get("name")
    if name is None:
        logging.error("Name must be specified.")
        return None

    cert_file = config.get('cert_file')
    if cert_file is None:
        logging.error("Certificate must be specified.")
        return None

    sandbox = bool(config.get('sandbox', False))

    service = ApnsNotificationService(hass, name, sandbox, cert_file)
    hass.services.register(DOMAIN, name, service.register)
    return service


class ApnsDevice(object):
    """
    Stores information about a device that is
    registered for push notifications.
    """

    def __init__(self, push_id, name, tracking_device_id=None):
        """Initialize Apns Device."""
        self.device_push_id = push_id
        self.device_name = name
        self.tracking_id = tracking_device_id

    @property
    def push_id(self):
        """The apns id for the device."""
        return self.device_push_id

    @property
    def name(self):
        """The friendly name for the device."""
        return self.device_name

    @property
    def tracking_device_id(self):
        """
        The id of a device that is tracked by the device
        tracking component.
        """
        return self.tracking_id

    @property
    def full_tracking_device_id(self):
        """
        The full id of a device that is tracked by the device
        tracking component.
        """
        return DEVICE_TRACKER_DOMAIN + '.' + self.tracking_id

    def __eq__(self, other):
        """Return the comparision."""
        if isinstance(other, self.__class__):
            return self.push_id == other.push_id and self.name == other.name
        return NotImplemented

    def __ne__(self, other):
        """Return the comparision."""
        return not self.__eq__(other)


class ApnsNotificationService(BaseNotificationService):
    """Implement the notification service for the AWS SNS service."""

    def __init__(self, hass, app_name, sandbox, cert_file):
        """Initialize APNS application."""
        self.hass = hass
        self.app_name = app_name
        self.sandbox = sandbox
        self.certificate = cert_file
        self.yaml_path = hass.config.path(app_name + '_' + APNS_DEVICES)
        self.devices = {}
        self.device_states = {}
        if os.path.isfile(self.yaml_path):
            self.devices = {
                str(key): ApnsDevice(
                    str(key),
                    value.get('name'),
                    value.get('tracking_device_id')
                )
                for (key, value) in
                load_yaml_config_file(self.yaml_path).items()
            }

        def state_changed_listener(entity_id, from_s, to_s):
            """
            Track device state change if a device
            has a tracking id specified.
            """
            self.device_states[entity_id] = str(to_s.state)
            return

        tracking_ids = [
            device.full_tracking_device_id
            for (key, device) in self.devices.items()
            if device.tracking_device_id is not None
        ]
        track_state_change(hass, tracking_ids, state_changed_listener)

    @staticmethod
    def write_device(out, device):
        """Write a single device to file."""
        attributes = []
        if device.name is not None:
            attributes.append(
                'name: {}'.format(device.name))
        if device.tracking_device_id is not None:
            attributes.append(
                'tracking_device_id: {}'.format(device.tracking_device_id))

        out.write(device.push_id)
        out.write(": {")
        if len(attributes) > 0:
            separator = ", "
            out.write(separator.join(attributes))

        out.write("}\n")

    def write_devices(self):
        """Write all known devices to file."""
        with open(self.yaml_path, 'w+') as out:
            for _, device in self.devices.items():
                ApnsNotificationService.write_device(out, device)

    def register(self, call):
        """Register a device to receive push messages."""

        push_id = call.data.get("push_id")
        if push_id is None:
            return False

        device_name = call.data.get("name")
        current_device = self.devices.get(push_id)
        current_tracking_id = None if current_device is None \
            else current_device.tracking_device_id

        device = ApnsDevice(
            push_id,
            device_name,
            current_tracking_id)

        if current_device is None:
            self.devices[push_id] = device
            with open(self.yaml_path, 'a') as out:
                self.write_device(out, device)
            return

        if device != current_device:
            self.devices[push_id] = device
            self.write_devices()

        return True

    def send_message(self, message="", **kwargs):
        """Send push message to registered devices."""
        from apns3 import APNs, Payload

        apns = APNs(
            use_sandbox=self.sandbox,
            cert_file=self.certificate,
            key_file=self.certificate)

        device_state = kwargs.get(ATTR_TARGET)
        message_data = kwargs.get(ATTR_DATA)

        if message_data is None:
            message_data = {}

        payload = Payload(
            message,
            message_data.get("badge"),
            message_data.get("sound"),
            message_data.get("category"),
            message_data.get("custom", {}),
            message_data.get("content_available", False))

        for push_id, device in self.devices.items():
            if device_state is None:
                apns.gateway_server.send_notification(push_id, payload)
            elif device.tracking_device_id is not None:
                state = self.device_states.get(device.full_tracking_device_id)
                if state == str(device_state):
                    apns.gateway_server.send_notification(push_id, payload)

        return True
