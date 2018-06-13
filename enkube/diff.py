import os
import sys
import re
import json
import pyaml
import subprocess
import threading
import tempfile
import shutil
from copy import deepcopy
from itertools import zip_longest
from collections import OrderedDict
from deepdiff import DeepDiff
from pygments import highlight, lexers, formatters
import click

from .render import pass_renderer, RenderError
from .ctl import kubectl_popen


EXCLUDE_PATHS = {
    "root['metadata']['annotations']['deployment.kubernetes.io/revision']",
    "root['metadata']['annotations']['kubectl.kubernetes.io/last-applied-configuration']",
    "root['metadata']['creationTimestamp']",
    "root['metadata']['generation']",
    "root['metadata']['resourceVersion']",
    "root['metadata']['selfLink']",
    "root['metadata']['uid']",
    "root['spec']['template']['metadata']['creationTimestamp']",
    "root['status']",
}
PATH_RE = re.compile(r"\[(?:'([^']+)'|(\d+))\]")


class Differ:
    def calculate_changes(self, env, rendered):
        try:
            self.nobjs = self.gather_objects(rendered)
        except RuntimeError as e:
            raise RenderError(e.args[0])

        self.oobjs = self.gather_objects_from_kubectl(
            env, self.object_refs(self.nobjs))

        self.changes = []
        seen = set()

        for ons, nns in zip_longest(self.oobjs, self.nobjs, fillvalue=()):
            if ons and ons not in seen and ons not in self.nobjs:
                self.changes.append(('delete_ns', (ons,)))
                seen.add(ons)
            if nns and nns not in seen and nns not in self.oobjs:
                self.changes.append(('add_ns', (nns,)))
                seen.add(nns)

            if ons and ons not in seen:
                self.diff_ns(ons)
                seen.add(ons)
            if nns and nns not in seen:
                self.diff_ns(nns)
                seen.add(nns)

        return self.changes

    def get_namespaces_from_kubectl(self, env):
        args = [
            'get', 'namespace', '-o',
            "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}"
        ]
        with kubectl_popen(env, args, stdout=subprocess.PIPE) as p:
            return set(ns.rstrip('\n') for ns in p.stdout)

    def filter_objects_by_namespace(self, namespaces, objs):
        for obj in objs:
            if obj['kind'] == 'Namespace':
                ons = obj['metadata']['name']
            else:
                ons = obj['metadata'].get('namespace', 'default')
            if ons in namespaces:
                yield obj

    def gather_objects_from_kubectl(self, env, refs):
        namespaces = self.get_namespaces_from_kubectl(env)
        req = {
            'apiVersion': 'v1',
            'kind': 'List',
            'items': list(self.filter_objects_by_namespace(namespaces, refs))
        }
        args = ['get', '-f', '-', '-o', 'json']
        with tempfile.TemporaryFile('w+', encoding='utf-8') as f:
            json.dump(req, f)
            f.seek(0)
            with kubectl_popen(
                env, args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE
            ) as p:
                def target():
                    try:
                        shutil.copyfileobj(f, p.stdin)
                    finally:
                        p.stdin.close()
                t = threading.Thread(target=target)
                t.start()
                objs = self.gather_objects([
                    json.load(p.stdout, object_pairs_hook=OrderedDict)
                ])
        if p.returncode != 0:
            sys.exit(p.returncode)
        return objs

    def object_refs(self, objs):
        for ns, objs in objs.items():
            for (k, n), obj in objs.items():
                ref = {
                    'apiVersion': obj['apiVersion'],
                    'kind': k,
                    'metadata': { 'name': n },
                }
                if ns:
                    ref['metadata']['namespace'] = ns
                yield ref

    def flatten_lists(self, objs):
        for obj in objs:
            if obj['kind'] == 'List':
                for obj in obj['items']:
                    yield obj
            else:
                yield obj

    def gather_objects(self, items):
        objs = OrderedDict()
        for obj in self.flatten_lists(items):
            ns = obj['metadata'].get('namespace', '')
            k = obj['kind']
            n = obj['metadata']['name']
            if ns not in objs:
                objs[ns] = OrderedDict()
            objs[ns][k,n] = obj
        return objs

    def diff_obj(self, ns, k, n):
        oobj = self.oobjs[ns][k,n]
        nobj = self.nobjs[ns][k,n]
        d = DeepDiff(oobj, nobj, view='tree', exclude_paths=EXCLUDE_PATHS)
        if d:
            self.changes.append(('change_obj', (ns, k, n, d)))

    def diff_ns(self, ns):
        seen = set()
        for o, n in zip_longest(self.oobjs[ns], self.nobjs[ns]):
            if (
                o and o not in seen and o not in self.nobjs[ns]
                and o[0] != 'Namespace'
            ):
                self.changes.append(('delete_obj', (ns, o[0], o[1])))
                seen.add(o)
            if (
                n and n not in seen and n not in self.oobjs[ns]
                and n[0] != 'Namespace'
            ):
                self.changes.append(('add_obj', (ns, n[0], n[1])))
                seen.add(n)

            if o and o not in seen:
                self.diff_obj(ns, o[0], o[1])
                seen.add(o)
            if n and n not in seen:
                self.diff_obj(ns, n[0], n[1])
                seen.add(n)


def remove_excluded_paths(obj):
    root = deepcopy(obj)
    for p in EXCLUDE_PATHS:
        path = [(int(i) if k is None else k) for k, i in PATH_RE.findall(p)]
        rpath = []
        last = root
        while path:
            rpath.insert(0, (last, path.pop(0)))
            try:
                last = last[rpath[0][-1]]
            except (KeyError, IndexError):
                break
        else:
            for i, (o, k) in enumerate(rpath):
                if not i or not o[k]:
                    del o[k]
    return root


def print_diff(o1, o2):
    files = []
    args = ['diff', '-u']
    try:
        for label, o in [('CLUSTER', o1), ('LOCAL', o2)]:
            o = remove_excluded_paths(o)
            with tempfile.NamedTemporaryFile('w+', delete=False) as f:
                files.append(f)
                pyaml.dump(o, f, safe=True)
            args.extend([
                '--label', '{}/{} {}'.format(
                    o['metadata'].get('namespace','<default>'),
                    o['metadata']['name'], label
                ),
                f.name
            ])
        d = subprocess.run(args, stdout=subprocess.PIPE).stdout
        formatted = highlight(
            d, lexers.DiffLexer(), formatters.TerminalFormatter())
        click.echo(formatted, nl=False)
    finally:
        for f in files:
            os.unlink(f.name)


class DiffError(click.ClickException):
    def show(self):
        click.secho('Error generating diff: {}'.format(self.args[0]), fg='red', err=True)


@click.command()
@pass_renderer
def cli(renderer):
    '''Show differences between rendered manifests and running state.'''
    stdout = click.get_text_stream('stdout')

    differ = Differ()
    changes = differ.calculate_changes(
        renderer.env, (o for _, o in renderer.render()))

    for action, args in changes:
        if action == 'add_ns':
            click.secho('Added namespace {} with {} objects'.format(
                args[0], len(differ.nobjs[args[0]])), fg='green')
        elif action == 'delete_ns':
            click.secho('Deleted namespace {} with {} objects'.format(
                args[0], len(differ.oobjs[args[0]])), fg='red')
        elif action == 'add_obj':
            ns, k, n = args
            click.secho('Added {} {}/{}'.format(k, ns, n), fg='green')
        elif action == 'delete_obj':
            ns, k, n = args
            click.secho('Deleted {} {}/{}'.format(k, ns, n), fg='red')
        elif action == 'change_obj':
            ns, k, n, d = args
            click.secho('Changed {} {}/{}'.format(k, ns, n), fg='yellow')
            print_diff(differ.oobjs[ns][k,n], differ.nobjs[ns][k,n])
