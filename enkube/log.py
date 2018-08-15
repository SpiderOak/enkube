import logging

import click


class ColorLogFormatter(logging.Formatter):
    colors = [
        (40, 'red'),
        (30, 'yellow'),
        (20, 'blue'),
    ]
    def format(self, record):
        style = {}
        for threshold, color in self.colors:
            if record.levelno >= threshold:
                style['fg'] = color
                break
        s = super(ColorLogFormatter, self).format(record)
        return click.style(s, **style)


class ClickLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            click.echo(msg)
        except Exception:
            self.handleError(record)


def init_logging(level):
    logging._acquireLock()
    try:
        handler = ClickLogHandler()
        handler.formatter = ColorLogFormatter(
            '%(asctime)s %(levelname)s %(message)s')
        logging.root.addHandler(handler)
        logging.root.setLevel(level)
    finally:
        logging._releaseLock()
