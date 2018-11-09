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

import os
import subprocess
import click

from .main import pass_env


def kubectl_popen(env, args, **kwargs):
    envvars = env.get_kubectl_environ()
    k = {'env': envvars, 'universal_newlines': True}
    k.update(kwargs)
    return subprocess.Popen([env.get_kubectl_path()] + args, **k)


@click.command(
    context_settings={'ignore_unknown_options': True},
    add_help_option=False
)
@click.argument('args', nargs=-1, type=click.UNPROCESSED)
@pass_env
def cli(env, args):
    '''Wrap kubectl, setting KUBECONFIG according to selected environment.'''
    kubectl_popen(env, list(args)).wait()
