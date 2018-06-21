import os
import sys
import pyaml
import subprocess
import tempfile
from collections import OrderedDict, deque
from deepdiff import DeepDiff
import click

from .util import format_diff, flatten_kube_lists
from .render import pass_renderer
from .api import Api


def interleave(*iters):
    q = deque((i, iter(i)) for i in iters)
    while q:
        i, ii = q.popleft()
        try:
            yield i, next(ii)
        except StopIteration:
            continue
        q.append((i, ii))


def gather_objects(items):
    namespaces = OrderedDict()
    for obj in flatten_kube_lists(items):
        ns = obj['metadata'].get('namespace')
        k = obj['kind']
        n = obj['metadata']['name']
        if ns not in namespaces:
            namespaces[ns] = OrderedDict()
        if k == 'Namespace' and n not in namespaces:
            namespaces[n] = OrderedDict()
        namespaces[ns][k,n] = obj
    return namespaces


def calculate_changes(items1, items2):
    seen = set()
    for i, ns in interleave(items1, items2):
        if ns in seen:
            continue
        seen.add(ns)
        if i is items1 and ns not in items2:
            yield ('delete_ns', (ns,))
        elif i is items2 and ns not in items1:
            yield ('add_ns', (ns,))
        else:
            for d in diff_ns(ns, items1[ns], items2[ns]):
                yield d


def diff_ns(ns, objs1, objs2):
    seen = set()
    for i, (k, n) in interleave(objs1, objs2):
        if (k, n) in seen:
            continue
        seen.add((k, n))
        if i is objs1 and (k, n) not in objs2:
            yield ('delete_obj', (ns, k, n))
        elif i is objs2 and (k, n) not in objs1:
            yield ('add_obj', (ns, k, n))
        else:
            for d in diff_obj(ns, k, n, objs1[k,n], objs2[k,n]):
                yield d


def diff_obj(ns, k, n, obj1, obj2):
    d = DeepDiff(obj1, obj2, view='tree')
    if d:
        yield ('change_obj', (ns, k, n, d))


def print_diff(o1, o2):
    files = []
    args = ['diff', '-u']
    try:
        for label, o in [('CLUSTER', o1), ('LOCAL', o2)]:
            with tempfile.NamedTemporaryFile('w+', delete=False) as f:
                files.append(f)
                pyaml.dump(o, f, safe=True)
            args.extend([
                '--label', '{}/{} {}'.format(
                    o['metadata'].get('namespace'),
                    o['metadata']['name'], label
                ),
                f.name
            ])
        d = subprocess.run(args, stdout=subprocess.PIPE).stdout
        formatted = format_diff(d)
        click.echo(formatted, nl=False)
    finally:
        for f in files:
            os.unlink(f.name)


def print_name(action, args):
    if action.endswith('_ns'):
        ns, = args
        line = 'Namespace {}'.format(ns)
    elif action.endswith('_obj'):
        ns, k, n = args[:3]
        line = '{} {}/{}'.format(k, ns, n)
    if action.startswith('add_'):
        click.secho(line, fg='green')
    elif action.startswith('delete_'):
        click.secho(line, fg='red')
    else:
        click.secho(line, fg='yellow')


def print_change(action, args, local, cluster):
    if action == 'add_ns':
        ns, = args
        click.secho('Added namespace {} with {} objects'.format(
            ns, len(local[ns])), fg='green')
    elif action == 'delete_ns':
        ns, = args
        click.secho('Deleted namespace {} with {} objects'.format(
            ns, len(cluster[ns])), fg='red')
    elif action == 'add_obj':
        ns, k, n = args
        click.secho('Added {} {}/{}'.format(k, ns, n), fg='green')
    elif action == 'delete_obj':
        ns, k, n = args
        click.secho('Deleted {} {}/{}'.format(k, ns, n), fg='red')
    elif action == 'change_obj':
        ns, k, n, d = args
        click.secho('Changed {} {}/{}'.format(k, ns, n), fg='yellow')
        print_diff(cluster[ns][k,n], local[ns][k,n])


@click.command()
@click.option(
    '--last-applied/--no-last-applied', default=True,
    help='Compare using last-applied-configuration annotation.'
)
@click.option('--quiet', '-q', is_flag=True)
@click.option(
    '--list', '-l', 'list_', is_flag=True,
    help='Only list names of changed objects'
)
@pass_renderer
def cli(renderer, last_applied, quiet, list_):
    '''Show differences between rendered manifests and running state.

    By default, compare to last applied configuration. Note that in this mode,
    differences introduced imperatively by eg. `kubectl scale` will be omitted.
    '''
    stdout = click.get_text_stream('stdout')

    rendered = [o for _, o in renderer.render(object_pairs_hook=dict)]
    local = gather_objects(rendered)
    with Api(renderer.env) as api:
        cluster = gather_objects(api.get_refs(rendered, last_applied))

    found_changes = False
    for action, args in calculate_changes(cluster, local):
        found_changes = True
        if quiet:
            break
        if list_:
            print_name(action, args)
        else:
            print_change(action, args, local, cluster)

    sys.exit(int(found_changes))
