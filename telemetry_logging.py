"""Keep request failures and changes; omit successful polling from access logs."""
import logging
import re


class PollingFilter(logging.Filter):
    def filter(self, record):
        if record.levelno >= logging.WARNING:
            return True
        return not re.search(r'"GET /api/[^"\s]* HTTP/[\d.]+"\s+(?:200|304)\s', record.getMessage())


def configure():
    logging.getLogger('werkzeug').addFilter(PollingFilter())
