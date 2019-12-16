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

import sys

import click

from ..util import format_json, format_python, close_kernel


def displayhook(value):
    if value is None:
        return
    __builtins__['_'] = None
    formatted = None
    if isinstance(value, dict) or isinstance(value, list):
        try:
            formatted = format_json(value)
        except Exception:
            pass
    if formatted is None:
        formatted = format_python(value)
    click.echo(formatted, nl=False)
    __builtins__['_'] = value


def cli():
    from ..main import pass_env
    from .client import ApiClient
    from .cache import Cache
    from .cache_synchronizer import CacheSynchronizer

    @click.command()
    @pass_env
    def cli(env):
        '''Start a Python REPL with a Kubernetes API client object.'''
        try:
            import readline
        except Exception:
            pass
        import code

        old_displayhook = sys.displayhook
        sys.displayhook = displayhook
        try:
            with ApiClient(env) as api:
                context = {
                    'api': api,
                    'Cache': Cache,
                    'CacheSynchronizer': CacheSynchronizer,
                }
                shell = code.InteractiveConsole(context)
                shell.interact()
        finally:
            sys.displayhook = old_displayhook
            close_kernel()

    return cli
