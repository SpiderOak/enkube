# Copyright 2018 SpiderOak, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
