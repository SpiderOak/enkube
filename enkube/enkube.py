'''Manage Kubernetes manifests.
'''
import os
import sys
import argparse
import json
import _jsonnet
import pyaml
import collections
import pkg_resources

DESCRIPTION = __doc__.splitlines()[0]
SEARCH_EXTS = ['.jsonnet']


def parse_args(args=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('files', nargs='*')
    parser.add_argument('--search', '-J', action='append')

    opts = parser.parse_args(args)

    if not opts.files:
        opts.files = ['manifests']

    if not opts.search:
        opts.search = []
    opts.search.append(os.getcwd())

    return opts


def find_files(paths, explicit=False):
    for p in paths:
        if explicit and not os.path.isdir(p):
            yield open(p)
        else:
            for ext in SEARCH_EXTS:
                if p.endswith(ext) and os.path.isfile(p):
                    yield open(p)
            if os.path.isdir(p):
                for f in find_files(
                    [os.path.join(p, n) for n in sorted(os.listdir(p))]
                ):
                    yield f


def search_callback(opts):
    def callback(dirname, rel):
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

        for d in [dirname] + opts.search:
            path = os.path.join(d, rel)
            try:
                with open(path) as f:
                    return path, f.read()
            except FileNotFoundError:
                continue
        raise RuntimeError('file not found')
    return callback


def render_jsonnet(opts, name, s):
    s = _jsonnet.evaluate_snippet(
        name, s, import_callback=search_callback(opts))
    return json.loads(s, object_pairs_hook=collections.OrderedDict)


def dump(obj):
    pyaml.dump(obj, sys.stdout, safe=True)


def main(args=None):
    opts = parse_args(args)

    for f in find_files(opts.files, True):
        with f:
            s = f.read()
        try:
            obj = render_jsonnet(opts, f.name, s)
        except RuntimeError as e:
            print(e.args[0], file=sys.stderr)
            sys.exit(1)

        print('---\n# File: {0.name}'.format(f))
        dump(obj)

    sys.exit(0)


if __name__ == '__main__':
    main()
