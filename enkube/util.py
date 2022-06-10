# Copyright 2018 SpiderOak, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
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
    curio_running, from_coroutine, _isasyncgenfunction, finalize)
from curio.monitor import Monitor


def load_yaml(stream, Loader=yaml.SafeLoader, object_pairs_hook=OrderedDict, load_doc=False):
    class OrderedLoader(Loader):
        pass
    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    if load_doc:
        return list(yaml.load_all(stream, OrderedLoader))
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

        if 'CURIOMONITOR' in os.environ:
            m = Monitor(k)
            k._call_at_shutdown(m.close)

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
            if from_coroutine() or curio_running():
                return asyncfunc(*args, **kwargs)
            else:
                return _gen(*args, **kwargs)

    else:
        @wraps(asyncfunc)
        def wrapped(*args, **kwargs):
            if from_coroutine() or curio_running():
                return asyncfunc(*args, **kwargs)
            else:
                return get_kernel().run(asyncfunc(*args, **kwargs))

    wrapped._awaitable = True
    return wrapped


#class AsyncInstanceType(curio.meta.AsyncInstanceType):
#    __call__ = sync_wrap(curio.meta.AsyncInstanceType.__call__)
#
#
#class AsyncObject(metaclass=AsyncInstanceType):
#    pass


class SyncIterWrapper:
    _sentinel = object()

    def __init__(self, aiter):
        self._aiter = aiter

    @sync_wrap
    async def _anext(self):
        try:
            return await self._aiter.__anext__()
        except StopAsyncIteration:
            return self._sentinel

    def __next__(self):
        item = self._anext()
        if item is self._sentinel:
            raise StopIteration()
        return item

class SyncIter:
    def __iter__(self):
        return SyncIterWrapper(self.__aiter__())


class SyncContextManager:
    @sync_wrap
    async def __enter__(self):
        return await self.__aenter__()

    @sync_wrap
    async def __exit__(self, typ, val, tb):
        return await self.__aexit__(typ, val, tb)
