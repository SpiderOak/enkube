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

from .log import init_logging
from .plugins import CommandPluginLoader
from .environment import Environment


pass_env = click.make_pass_decorator(Environment, ensure=True)


@click.command(cls=CommandPluginLoader)
@click.option('--env', '-e', envvar='ENKUBE_ENV')
@click.option('--search', '-J', multiple=True, type=click.Path(), envvar='ENKUBE_SEARCH')
@click.option('-v', '--verbose', count=True)
@click.pass_context
def cli(ctx, env, search, verbose):
    '''Manage Kubernetes manifests.'''
    init_logging(logging.WARNING - 10 * verbose)
    ctx.obj = Environment(env, search)


if __name__ == '__main__':
    cli()
