import os
import json
import _jsonnet
import pyaml
import collections
import pkg_resources
from functools import update_wrapper
import click

from .enkube import pass_env


SEARCH_EXTS = ['.jsonnet']
NO_NAMESPACE_KINDS = [
    'Namespace',
    'ClusterRole',
    'ClusterRoleBinding',
]


class Renderer:
    def __init__(self, env, files=(), exclude=(), verify_namespace=True):
        self.env = env
        self.files = list(files)
        self.exclude = list(exclude)
        self.verify_namespace = verify_namespace

        if not self.files:
            self.files = [click.Path(exists=True)('manifests')]

    def render_to_stream(self, stream):
        for fname, obj in self.render():
            click.secho('---\n# File: {}'.format(fname), file=stream, fg='blue')
            pyaml.dump(obj, stream, safe=True)

    def render(self):
        for f in self.find_files(self.files, True):
            with f:
                s = f.read()
            obj = self.render_jsonnet(f.name, s)
            if self.verify_namespace:
                self.verify_object_namespace(obj)
            yield f.name, obj

    def verify_object_namespace(self, obj):
        objs = [obj]
        while objs:
            obj = objs.pop(0)
            if 'apiVersion' not in obj or obj['kind'] in NO_NAMESPACE_KINDS:
                continue
            if obj['kind'] == 'List':
                objs.extend(obj['items'])
                continue
            if not obj.get('metadata', {}).get('namespace'):
                raise RuntimeError('{} is missing namespace'.format(obj['kind']), obj)

    def render_jsonnet(self, name, s):
        s = _jsonnet.evaluate_snippet(
            name, s, import_callback=self.import_callback)
        return json.loads(s, object_pairs_hook=collections.OrderedDict)

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

    def import_callback(self, dirname, rel):
        if rel.startswith('enkube/'):
            if not rel.endswith('.libsonnet'):
                rel += '.libsonnet'
            try:
                res = pkg_resources.resource_string(
                    __name__, os.path.join('libsonnet', rel.split('/', 1)[1])
                ).decode('utf-8')
                return rel, res
            except Exception:
                pass

        for d in self.env.search_dirs([dirname]):
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


@click.command()
@pass_renderer
def cli(renderer):
    '''Render Kubernetes manifests.'''
    stdout = click.get_text_stream('stdout')
    try:
        renderer.render_to_stream(stdout)
    except RuntimeError as e:
        raise RenderError(e.args[0])
