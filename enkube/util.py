import sys
import json
import yaml
import pyaml
import threading
from functools import wraps
from collections import OrderedDict
from pprint import pformat

from pygments import highlight, lexers, formatters

import curio
from curio.meta import (
    curio_running, _from_coroutine, _isasyncgenfunction, finalize)


def load_yaml(stream, Loader=yaml.SafeLoader, object_pairs_hook=OrderedDict):
    class OrderedLoader(Loader):
        pass
    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    return yaml.load(stream, OrderedLoader)


def format_json(obj, sort_keys=True):
    return highlight(
        json.dumps(obj, sort_keys=sort_keys, indent=2),
        lexers.JsonLexer(),
        formatters.TerminalFormatter()
    )


def format_yaml(obj, prefix='---\n'):
    return highlight(
        prefix + pyaml.dumps(obj, safe=True).decode('utf-8'),
        lexers.YamlLexer(),
        formatters.TerminalFormatter()
    )


def format_diff(diff):
    return highlight(diff, lexers.DiffLexer(), formatters.TerminalFormatter())


def format_python(obj):
    return highlight(
        pformat(obj),
        lexers.PythonLexer(),
        formatters.TerminalFormatter()
    )


def flatten_kube_lists(items):
    for obj in items:
        if obj.get('kind', '').endswith('List'):
            for obj in flatten_kube_lists(obj['items']):
                yield obj
        else:
            yield obj


_locals = threading.local()


def get_kernel():
    try:
        return _locals.curio_kernel
    except AttributeError:
        _locals.curio_kernel = k = curio.Kernel()
        return k


def set_kernel(kernel):
    _locals.curio_kernel = kernel


def close_kernel():
    try:
        k = _locals.curio_kernel
    except AttributeError:
        return
    k.run(shutdown=True)
    del _locals.curio_kernel


def sync_wrap(asyncfunc):
    if _isasyncgenfunction(asyncfunc):
        def _gen(*args, **kwargs):
            k = get_kernel()
            it = asyncfunc(*args, **kwargs)
            f = finalize(it)
            sentinal = object()
            async def _next():
                try:
                    return await it.__anext__()
                except StopAsyncIteration:
                    return sentinal
            k.run(f.__aenter__)
            try:
                while True:
                    item = k.run(_next)
                    if item is sentinal:
                        return
                    yield item
            finally:
                k.run(f.__aexit__, *sys.exc_info())

        @wraps(asyncfunc)
        def wrapped(*args, **kwargs):
            if _from_coroutine() or curio_running():
                return asyncfunc(*args, **kwargs)
            else:
                return _gen(*args, **kwargs)

    else:
        @wraps(asyncfunc)
        def wrapped(*args, **kwargs):
            if _from_coroutine() or curio_running():
                return asyncfunc(*args, **kwargs)
            else:
                return get_kernel().run(asyncfunc, *args, **kwargs)

    wrapped._awaitable = True
    return wrapped


class AsyncInstanceType(curio.meta.AsyncInstanceType):
    __call__ = sync_wrap(curio.meta.AsyncInstanceType.__call__)


class AsyncObject(metaclass=AsyncInstanceType):
    pass
