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

import click

from .enkube import pass_env
from .util import format_yaml, close_kernel
from .api import Api


@click.command()
@pass_env
def cli(env):
    '''Dump all objects from Kubernetes server.'''
    try:
        with Api(env) as api:
            for obj in api.list():
                click.echo(format_yaml(obj))
    finally:
        close_kernel()
