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
import re
import json
import pyaml
import _jsonnet
import pkg_resources
import inspect
from collections import OrderedDict
from collections.abc import Mapping
from functools import update_wrapper
import requests
import click

from .environment import Environment
from .plugins import RenderPluginLoader
from .main import pass_env


SEARCH_EXTS = ['.jsonnet']
URL_RX = re.compile(r'https?://', re.I)


def _plugin_callbacks(plugin):
    for attr in dir(plugin):
        if attr.startswith('_'):
            continue
        cb = getattr(plugin, attr)
        if not callable(cb):
            continue
        if cb.__annotations__.get('return') != 'cb':
            continue
        argspec = inspect.getfullargspec(cb)
        yield attr, cb, argspec


def _plugin_cb_import(prefix, plugin, dirname):
    callback_strings = ['local dirname=' + json.dumps(dirname)]
    for attr, cb, argspec in _plugin_callbacks(plugin):
        args = ', '.join(argspec.args[2:])
        annotated_args = ', '.join(
            f'std.manifestJsonEx({arg}, " ")' if argspec.annotations.get(arg) == 'json' else arg
            for arg in argspec.args[2:]
        )
        callback_strings.append(
            f'{attr}({args}):: std.native("{prefix}.{attr}")(dirname, {annotated_args})')
    return '{' + ', '.join(callback_strings) + '}'


class BaseRenderer:
    verify_namespace = True
    env = Environment()
    plugin_loader = RenderPluginLoader()

    @property
    def native_callbacks(self):
        callbacks = {}
        for name in self.plugin_loader.list():
            plugin = self.plugin_loader.load(name)(self.env)
            for attr, cb, argspec in _plugin_callbacks(plugin):
                args = tuple(argspec.args[1:])
                callbacks[f'{name}.{attr}'] = (args, cb)
        return callbacks

    def render_jsonnet(self, name, s, object_pairs_hook=OrderedDict):
        s = _jsonnet.evaluate_snippet(
            name, s,
            import_callback=self.import_callback,
            native_callbacks=self.native_callbacks,
            ext_vars={
                'VERIFY_NAMESPACES': '1' if self.verify_namespace else '0'
            }
        )
        return json.loads(s, object_pairs_hook=object_pairs_hook)

    def import_callback(self, dirname, rel):
        return self._import_callback(dirname, rel)

    def _import_callback(self, dirname, rel):
        if rel.startswith('enkube/'):
            n = rel.split('/', 1)[1]
            if n in self.plugin_loader._entrypoints:
                plugin = self.plugin_loader.load(n)(self.env)
                return rel, _plugin_cb_import(n, plugin, dirname)

            if not n.endswith('.libsonnet'):
                n += '.libsonnet'
            try:
                res = pkg_resources.resource_string(
                    __name__, os.path.join('libsonnet', n)).decode('utf-8')
                return rel, res
            except Exception:
                pass

        if URL_RX.match(rel):
            try:
                res = requests.get(rel)
                return rel, res.text
            except Exception:
                raise RuntimeError('error retrieving URL')

        raise RuntimeError('file not found')


class Renderer(BaseRenderer):
    def __init__(self, env, files=(), exclude=(), verify_namespace=True):
        self.env = env
        self.files = list(files)
        self.exclude = list(exclude)
        self.verify_namespace = verify_namespace

        if not self.files:
            self.files = [click.Path(exists=True)('manifests')]

    def render(self, object_pairs_hook=OrderedDict):
        for f in self.find_files(self.files, True):
            with f:
                s = f.read()
            obj = self.render_jsonnet(
                f.name, s, object_pairs_hook=object_pairs_hook)
            yield f.name, obj

    def render_to_stream(self, stream):
        for fname, obj in self.render():
            click.secho('---\n# File: {}'.format(fname), file=stream, fg='blue')
            pyaml.dump(obj, stream, safe=True)

    def find_files(self, paths, explicit=False):
        for p in paths:
            if p in self.exclude:
                continue
            if explicit and not os.path.isdir(p):
                yield open(p)
            else:
                for ext in SEARCH_EXTS:
                    if p.endswith(ext) and os.path.isfile(p):
                        yield open(p)
                if os.path.isdir(p):
                    for f in self.find_files(
                        [os.path.join(p, n) for n in sorted(os.listdir(p))]
                    ):
                        yield f

    def _import_callback(self, dirname, rel):
        if rel == 'enkube/env':
            return rel, self.env.to_json()

        try:
            return super(Renderer, self)._import_callback(dirname, rel)
        except RuntimeError as err:
            if err.args[0] != 'file not found':
                raise

        for d in self.env.search_dirs(
            [dirname], [os.path.join(dirname, 'defaults')]
        ):
            path = os.path.join(d, rel)
            try:
                with open(path) as f:
                    return path, f.read()
            except FileNotFoundError:
                continue

        raise RuntimeError('file not found')


class RenderError(click.ClickException):
    def show(self):
        click.secho('Render error: {}'.format(self.args[0]), fg='red', err=True)


def pass_renderer(callback):
    @click.argument('files', nargs=-1, type=click.Path(exists=True))
    @click.option('--exclude', multiple=True, type=click.Path())
    @click.option('--verify-namespace/--no-verify-namespace', default=True)
    @pass_env
    def inner(env, files, exclude, verify_namespace, *args, **kwargs):
        env.renderer = Renderer(env, files, exclude, verify_namespace)
        return callback(env.renderer, *args, **kwargs)
    return update_wrapper(inner, callback)


def cli():
    @click.command()
    @pass_renderer
    def cli(renderer):
        '''Render Kubernetes manifests.'''
        stdout = click.get_text_stream('stdout')
        try:
            renderer.render_to_stream(stdout)
        except RuntimeError as e:
            raise RenderError(e.args[0])

    return cli
