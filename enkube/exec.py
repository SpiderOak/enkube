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
from urllib.parse import quote
import click

from .util import flatten_kube_lists, format_json, close_kernel
from .ctl import kubectl_popen
from .api import Api
from .main import pass_env


@click.command(
    context_settings={'ignore_unknown_options': True},
    add_help_option=False
)
@click.option('-l', 'labels', multiple=True)
@click.option('-n', 'namespace')
@click.argument('args', nargs=-1, type=click.UNPROCESSED)
@pass_env
def cli(env, namespace, labels, args):
    '''Convenience wrapper for kubectl exec.'''
    try:
        with Api(env) as api:
            for pod in api.list(
                'v1', 'Pod', namespace, labelSelector=','.join(labels)
            ):
                if pod['status']['phase'] == 'Running':
                    podname = pod['metadata']['name']
                    break
            else:
                click.secho('No running pods found', fg='red')
                sys.exit(1)
    finally:
        close_kernel()

    click.secho(f'Found pod {podname}', fg='cyan')
    args = ['-n', namespace, 'exec', podname] + list(args)
    kubectl_popen(env, args).wait()
