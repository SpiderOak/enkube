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
import sys
import subprocess
from collections import OrderedDict
import click

from .util import format_json, load_yaml
from .main import pass_env


class GPGError(RuntimeError):
    def __init__(self, message):
        self.message = message


class GPGProcessError(GPGError):
    def __init__(self, returncode, message):
        self.message = message
        self.returncode = returncode


class GPG:
    def __init__(self, env):
        self.env = env

    def _communicate(self, args, s, outfile):
        try:
            s.fileno()
        except Exception:
            infile = subprocess.PIPE
            if outfile is None:
                outfile = subprocess.PIPE
        else:
            infile = s
            s = None

        a = ['gpg']
        a.extend(args)

        p = subprocess.Popen(
            a, stdin=infile, stdout=outfile, stderr=subprocess.PIPE)
        out, err = p.communicate(s)
        if p.returncode:
            raise GPGProcessError(p.returncode, err.decode('utf-8').strip())
        return out

    def encrypt(self, s, outfile=None):
        recipient = self.env.gpgsecret_keyid()
        if not recipient:
            raise GPGError('recipient keyid not specified in environment')
        return self._communicate(['-ea', '-r', recipient], s, outfile)

    def decrypt(self, s, outfile=None):
        return self._communicate(['-d'], s, outfile)

    def _endecrypt_obj(self, op, obj):
        if isinstance(obj, str):
            if obj.startswith('-----BEGIN PGP MESSAGE-----'):
                return obj
            return op(obj.encode('utf-8')).decode('ascii')
        elif isinstance(obj, bytes):
            return op(obj).decode('ascii')
        elif isinstance(obj, dict):
            return OrderedDict((k, self._endecrypt_obj(op, v)) for k, v in obj.items())
        elif isinstance(obj, list):
            return [self._endecrypt_obj(op, i) for i in obj]
        return obj

    def encrypt_object(self, obj):
        return self._endecrypt_obj(self.encrypt, obj)

    def decrypt_object(self, obj):
        return self._endecrypt_obj(self.decrypt, obj)


@click.command()
@click.argument('action', type=click.Choice(['encrypt', 'decrypt']))
@click.option('--json', '-j', 'isjson', is_flag=True)
@pass_env
def cli(env, action, isjson):
    '''En/decrypt secrets.'''
    gpg = GPG(env)

    if isjson:
        op = getattr(gpg, '{}_object'.format(action))
        obj = load_yaml(sys.stdin)
    else:
        op = getattr(gpg, action)
        obj = click.get_binary_stream('stdin')

    try:
        obj = op(obj)
    except GPGError as err:
        click.secho(err.message, fg='red', err=True)
        sys.exit(getattr(err, 'returncode', 1))

    if isjson:
        formatted = format_json(obj, sort_keys=False)
        click.echo(formatted, nl=False)
