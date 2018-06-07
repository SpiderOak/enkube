'''Diff Kubernetes manifests.
'''
import argparse
import yaml
from itertools import zip_longest
from collections import OrderedDict
from deepdiff import DeepDiff


DESCRIPTION = __doc__


class DiffCommand:
    def __init__(self, sp):
        parser = sp.add_parser('diff', help=DESCRIPTION)
        parser.set_defaults(command=self)
        parser.add_argument('file1', type=argparse.FileType('r'))
        parser.add_argument('file2', type=argparse.FileType('r'))

    def main(self, opts):
        self.opts = opts

        self.oobjs = self.gather_objects(self.opts.file1)
        self.nobjs = self.gather_objects(self.opts.file2)

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

    def flatten_lists(self, objs):
        for obj in objs:
            if obj['kind'] == 'List':
                for obj in obj['items']:
                    yield obj
            else:
                yield obj

    def gather_objects(self, f):
        objs = OrderedDict()
        for obj in self.flatten_lists(yaml.load_all(f)):
            ns = obj['metadata'].get('namespace')
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
