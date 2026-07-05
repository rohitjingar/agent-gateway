"""Optional structured (JSON) logging, toggled by GATEWAY_LOG_FORMAT=json.

JSON logs are what log aggregators (ELK, Loki, Datadog) want. Text mode is left
untouched so local dev and pytest keep their familiar output.
"""

from __future__ import annotations

import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def configure_logging(settings) -> None:
    if settings.log_format != "json":
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
