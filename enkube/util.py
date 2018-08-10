import json
import yaml
import pyaml
import asyncio
import functools
from collections import OrderedDict
from pprint import pformat
from pygments import highlight, lexers, formatters


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


def sync_wrap(coro_func):
    @functools.wraps(coro_func)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        try:
            return loop.run_until_complete(coro_func(*args, **kwargs))
        except StopAsyncIteration:
            raise StopIteration
    return wrapper


def sync_wrap_iter(async_gen_func):
    @functools.wraps(async_gen_func)
    def wrapped(*args, **kwargs):
        loop = asyncio.get_event_loop()
        it = async_gen_func(*args, **kwargs)
        while True:
            try:
                yield loop.run_until_complete(it.__anext__())
            except StopAsyncIteration:
                break
    return wrapped


def close_event_loop(loop=None):
    if loop is None:
        loop = asyncio.get_event_loop()
    if loop.is_closed():
        return
    # lifted with minor modifications from asyncio.runners.run()
    # https://github.com/python/cpython/blob/3.7/Lib/asyncio/runners.py
    try:
        to_cancel = asyncio.all_tasks(loop)
        if to_cancel:
            for task in to_cancel:
                task.cancel()
            loop.run_until_complete(asyncio.gather(
                *to_cancel, loop=loop, return_exceptions=True))
            for task in to_cancel:
                if task.cancelled():
                    continue
                if task.exception() is not None:
                    loop.call_exception_handler({
                        'message': (
                            'unhandled exception while closing event loop'),
                        'exception': task.exception(),
                        'task': task
                    })
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        loop.close()
