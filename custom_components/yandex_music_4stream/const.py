"""Constants for Yandex Music 4STREAM integration."""

DOMAIN = "yandex_music_4stream"
POLL_INTERVAL = 5  # seconds
PROXY_PORT = 8479
MAX_SKIP_ON_ERROR = 3  # max tracks to skip on consecutive URL failures
CONSECUTIVE_POLL_ERRORS_THRESHOLD = 5  # mark entity unavailable after N poll failures

CONF_YANDEX_TOKEN = "yandex_token"
CONF_DEVICES = "devices"
CONF_DEVICE_HOST = "host"
CONF_DEVICE_NAME = "name"
