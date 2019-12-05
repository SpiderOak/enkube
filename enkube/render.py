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
import traceback
from junit_xml import TestSuite, TestCase
from timeit import default_timer as timer

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

    def render(self, object_pairs_hook=OrderedDict, raise_errors=True):
        for f in self.find_files(self.files, explicit=True):
            with f:
                s = f.read()
            try:
                obj = self.render_jsonnet(
                    f.name, s, object_pairs_hook=object_pairs_hook)
            except Exception:
                if raise_errors:
                    raise
            else:
                yield f.name, obj

    def render_to_stream(self, stream):
        for fname, obj in self.render():
            click.secho('---\n# File: {}'.format(fname), file=stream, fg='blue')
            pyaml.dump(obj, stream, safe=True)

    def render_to_directory(self, dirname):
        for fname, obj in self.render():
            path = os.path.splitext(
                os.path.join(dirname, self.get_rel_path(fname)))[0] + '.yaml'
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(f'---\n# File: {fname}\n')
                pyaml.dump(obj, f, safe=True)

    def get_rel_path(self, fname):
        for prefix in sorted(self.files, key=lambda f: len(f), reverse=True):
            if fname == prefix:
                return os.path.basename(fname)
            if fname.startswith(prefix) and fname[len(prefix)] == os.path.sep:
                return fname[len(prefix):].lstrip(os.path.sep)
        return fname.lstrip(os.path.sep)

    def find_files(self, paths, exts=SEARCH_EXTS, explicit=False):
        for p in paths:
            if p in self.exclude:
                continue
            if explicit and not os.path.isdir(p):
                yield open(p)
            else:
                for ext in exts:
                    if p.endswith(ext) and os.path.isfile(p):
                        yield open(p)
                if os.path.isdir(p):
                    children = [os.path.join(p, n) for n in sorted(os.listdir(p))]
                    for f in self.find_files(children, exts):
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


class JunitRenderer(Renderer):
    def __init__(self, env, files=(), exclude=(), verify_namespace=True, output_filename=None):
        super(JunitRenderer, self).__init__(env, files, exclude, verify_namespace)
        self.test_cases = []
        self.output_filename = output_filename

    def render_jsonnet(self, name, s, object_pairs_hook=OrderedDict):
        test_case = TestCase(name, file=name)
        self.test_cases.append(test_case)

        start = timer()
        try:
            return super(JunitRenderer, self).render_jsonnet(name, s, object_pairs_hook)
        except Exception:
            test_case.add_error_info(str(sys.exc_info()[1]), traceback.format_exc())
            raise
        finally:
            end = timer()
            test_case.elapsed_sec = end - start

    def render(self, object_pairs_hook=OrderedDict, raise_errors=False):
        if self.output_filename:
            f = open(self.output_filename, 'w')
        try:
            for ret in super(JunitRenderer, self).render(object_pairs_hook, raise_errors):
                yield ret
        finally:
            if self.output_filename:
                with f:
                    TestSuite.to_file(f, [
                        TestSuite(self.env.name or 'default', self.test_cases)
                    ], prettyprint=False)


class RenderError(click.ClickException):
    def show(self):
        click.secho('Render error: {}'.format(self.args[0]), fg='red', err=True)


def pass_renderer(callback):
    @click.argument('files', nargs=-1, type=click.Path(exists=True))
    @click.option('--exclude', multiple=True, type=click.Path())
    @click.option('--verify-namespace/--no-verify-namespace', default=True)
    @click.option('--junit-xml', type=click.Path())
    @pass_env
    def inner(env, files, exclude, verify_namespace, junit_xml, *args, **kwargs):
        if junit_xml:
            env.renderer = JunitRenderer(env, files, exclude, verify_namespace, junit_xml)
        else:
            env.renderer = Renderer(env, files, exclude, verify_namespace)
        return callback(env.renderer, *args, **kwargs)
    return update_wrapper(inner, callback)


def cli():
    @click.command()
    @click.option('--output-dir', type=click.Path())
    @pass_renderer
    def cli(renderer, output_dir):
        '''Render Kubernetes manifests.'''
        try:
            if output_dir:
                renderer.render_to_directory(output_dir)
            else:
                stdout = click.get_text_stream('stdout')
                renderer.render_to_stream(stdout)
        except RuntimeError as e:
            raise RenderError(e.args[0])

    return cli
