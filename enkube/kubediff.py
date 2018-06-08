'''Diff Kubernetes manifests.
'''
import argparse
import json
import subprocess
import threading
import tempfile
import shutil
from itertools import zip_longest
from collections import OrderedDict
from deepdiff import DeepDiff

from .enkube import RenderCommand


DESCRIPTION = __doc__


class DiffCommand(RenderCommand):
    '''Show differences between rendered manifests and running state.
    '''
    _cmd = 'diff'

    def main(self, opts):
        self.opts = opts

        try:
            self.nobjs = self.gather_objects(o for _, o in self.render())
        except RuntimeError as e:
            print(e.args[0], file=sys.stderr)
            sys.exit(1)

        self.oobjs = self.gather_objects_from_kubectl(
            self.object_refs(self.nobjs))

        self.changes = []
        seen = set()

        for ons, nns in zip_longest(self.oobjs, self.nobjs, fillvalue=()):
            if ons not in seen and ons not in self.nobjs:
                self.changes.append(('delete_ns', (ons,)))
                seen.add(ons)
            if nns not in seen and nns not in self.oobjs:
                self.changes.append(('add_ns', (nns,)))
                seen.add(nns)

            if ons not in seen:
                self.diff_ns(ons)
                seen.add(ons)
            if nns not in seen:
                self.diff_ns(nns)
                seen.add(nns)

        for action, args in self.changes:
            if action == 'add_ns':
                print('Added namespace {} with {} objects'.format(
                    args[0], len(self.nobjs[args[0]])))
            elif action == 'delete_ns':
                print('Deleted namespace {} with {} objects'.format(
                    args[0], len(self.oobjs[args[0]])))
            elif action == 'add_obj':
                ns, k, n = args
                print('Added {} {}/{}'.format(k, ns, n))
            elif action == 'delete_obj':
                ns, k, n = args
                print('Deleted {} {}/{}'.format(k, ns, n))
            elif action == 'change_obj':
                ns, k, n, d = args
                print('Changed {} {}/{}'.format(k, ns, n))
                print(d)

    def gather_objects_from_kubectl(self, refs):
        req = {'apiVersion': 'v1', 'kind': 'List', 'items': list(refs)}
        ctl = self.opts.plugins['ctl']
        args = ['get', '-f', '-', '-o', 'json']
        with tempfile.TemporaryFile('w+', encoding='utf-8') as f:
            json.dump(req, f)
            f.seek(0)
            with ctl.popen(
                self.opts, args,
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
                return self.gather_objects([
                    json.load(p.stdout, object_pairs_hook=OrderedDict)
                ])

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
        d = DeepDiff(oobj, nobj)
        if d:
            self.changes.append(('change_obj', (ns, k, n, d)))

    def diff_ns(self, ns):
        seen = set()
        for o, n in zip_longest(self.oobjs[ns], self.nobjs[ns]):
            if o and o not in seen and o not in self.nobjs[ns]:
                self.changes.append(('delete_obj', (ns, o[0], o[1])))
                seen.add(o)
            if n and n not in seen and n not in self.oobjs[ns]:
                self.changes.append(('add_obj', (ns, n[0], n[1])))
                seen.add(n)

            if o and o not in seen:
                self.diff_obj(ns, o[0], o[1])
                seen.add(o)
            if n and n not in seen:
                self.diff_obj(ns, n[0], n[1])
                seen.add(n)
