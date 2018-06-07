'''Diff Kubernetes manifests.
'''
import argparse
import yaml
from itertools import zip_longest
from collections import OrderedDict
from deepdiff import DeepDiff


def init_parser(parser):
    parser.set_defaults(cmd=cmd_diff, finalize_opts=finalize_opts_diff)
    parser.add_argument('file1', type=argparse.FileType('r'))
    parser.add_argument('file2', type=argparse.FileType('r'))


def finalize_opts_diff(opts):
    pass


def flatten_lists(objs):
    for obj in objs:
        if obj['kind'] == 'List':
            for obj in obj['items']:
                yield obj
        else:
            yield obj


def gather_objects(f):
    objs = OrderedDict()
    for obj in flatten_lists(yaml.load_all(f)):
        ns = obj['metadata'].get('namespace')
        k = obj['kind']
        n = obj['metadata']['name']
        if ns not in objs:
            objs[ns] = OrderedDict()
        objs[ns][k,n] = obj
    return objs


def diff_obj(changes, ns, k, n, oobjs, nobjs):
    oobj = oobjs[ns][k,n]
    nobj = nobjs[ns][k,n]
    d = DeepDiff(oobj, nobj)
    if d:
        changes.append(('change_obj', (ns, k, n, d)))


def diff_ns(changes, oobjs, nobjs, ns):
    seen = set()
    for o, n in zip_longest(oobjs[ns], nobjs[ns]):
        if o and o not in seen and o not in nobjs[ns]:
            changes.append(('delete_obj', (ns, o[0], o[1])))
            seen.add(o)
        if n and n not in seen and n not in oobjs[ns]:
            changes.append(('add_obj', (ns, n[0], n[1])))
            seen.add(n)

        if o and o not in seen:
            diff_obj(changes, ns, o[0], o[1], oobjs, nobjs)
            seen.add(o)
        if n and n not in seen:
            diff_obj(changes, ns, n[0], n[1], oobjs, nobjs)
            seen.add(n)


def cmd_diff(opts):
    oobjs = gather_objects(opts.file1)
    nobjs = gather_objects(opts.file2)

    changes = []
    seen = set()

    for ons, nns in zip_longest(oobjs, nobjs, fillvalue=()):
        if ons not in seen and ons not in nobjs:
            changes.append(('delete_ns', (ons,)))
            seen.add(ons)
        if nns not in seen and nns not in oobjs:
            changes.append(('add_ns', (nns,)))
            seen.add(nns)

        if ons not in seen:
            diff_ns(changes, oobjs, nobjs, ons)
            seen.add(ons)
        if nns not in seen:
            diff_ns(changes, oobjs, nobjs, nns)
            seen.add(nns)

    for action, args in changes:
        if action == 'add_ns':
            print('Added namespace {} with {} objects'.format(args[0], len(nobjs[args[0]])))
        elif action == 'delete_ns':
            print('Deleted namespace {} with {} objects'.format(args[0], len(oobjs[args[0]])))
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
