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
import subprocess

from .kubediff import DiffCommand
from .kubectl import CtlCommand

DESCRIPTION = __doc__
SEARCH_EXTS = ['.jsonnet']


def main(args=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--env', '-e')
    parser.add_argument('--search', '-J', action='append')
    sp = parser.add_subparsers()

    plugins = {}
    for ep in pkg_resources.iter_entry_points('enkube.commands'):
        plugin = ep.load()
        plugins[ep.name] = plugin(sp)

    opts, args = parser.parse_known_args(args)
    opts.args = args
    opts.plugins = plugins

    if args and not getattr(opts.command, 'handles_extra_args', False):
        parser.error('unrecognized arguments: {}'.format(' '.join(args)))

    if not opts.search:
        opts.search = []

    cwd = os.getcwd()
    opts.search.append(cwd)

    if opts.env:
        opts.search.append(os.path.join(cwd, 'envs', opts.env))

    opts.command.main(opts)

    sys.exit(0)


class RenderCommand:
    '''Render Kubernetes manifests.
    '''
    _cmd = 'render'

    def __init__(self, sp):
        self._parser = sp.add_parser(self._cmd, help=self.__doc__)
        self._parser.set_defaults(command=self)
        self._parser.add_argument('files', nargs='*')

    def main(self, opts):
        self.opts = opts
        self.render(sys.stdout)

    def render(self, stream):
        if not self.opts.files:
            self.opts.files = ['manifests']

        for f in self.find_files(self.opts.files, True):
            with f:
                s = f.read()
            try:
                obj = self.render_jsonnet(f.name, s)
            except RuntimeError as e:
                print(e.args[0], file=sys.stderr)
                sys.exit(1)

            print('---\n# File: {0.name}'.format(f), file=stream)
            pyaml.dump(obj, stream, safe=True)

    def render_jsonnet(self, name, s):
        s = _jsonnet.evaluate_snippet(
            name, s,
            import_callback=self.search_callback
        )
        return json.loads(s, object_pairs_hook=collections.OrderedDict)

    def find_files(self, paths, explicit=False):
        for p in paths:
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

    def search_callback(self, dirname, rel):
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

        for d in [dirname] + self.opts.search:
            path = os.path.join(d, rel)
            try:
                with open(path) as f:
                    return path, f.read()
            except FileNotFoundError:
                continue
        raise RuntimeError('file not found')


class ApplyCommand(RenderCommand):
    '''Render and apply Kubernetes manifests.
    '''
    _cmd = 'apply'

    def __init__(self, sp):
        super(ApplyCommand, self).__init__(sp)
        self._parser.add_argument(
            '--dry-run', action='store_true', help="don't actually apply to server")

    def main(self, opts):
        self.opts = opts
        ctl = self.opts.plugins['ctl']
        args = ['apply', '-f', '-']
        if opts.dry_run:
            args.append('--dry-run=true')
        p = ctl.popen(self.opts, args, stdin=subprocess.PIPE)
        self.render(p.stdin)
        p.stdin.close()
        p.wait()


if __name__ == '__main__':
    main()
