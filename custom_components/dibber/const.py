"""Dibber constants."""

from datetime import timedelta
from logging import getLogger

DOMAIN = "dibber"
LOGGER = getLogger(__package__)
BASE_URL = "https://api.dibber.com"
USER_AGENT = "HomeAssistant-Dibber/0.1.0"
POLL_INTERVAL = timedelta(minutes=15)
DISCOVERY_INTERVAL = timedelta(hours=24)
CONF_CHILDREN = "children"
CONF_DEVICE_ID = "device_id"
CONF_ACCOUNT_ID = "account_id"
CONF_UNCERTAIN = "renewal_uncertain"
