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

import sys
import unittest
from unittest.mock import patch
from functools import wraps

from curio.meta import iscoroutinefunction

from ..util import get_kernel, close_kernel


def async_test(func):
    async def run_test_async(args, kw):
        try:
            await func(*args, **kw)
        except:
            return sys.exc_info()

    @wraps(func)
    def async_test(*args, **kw):
        exc_info = None
        try:
            if iscoroutinefunction(func):
                exc_info = get_kernel().run(run_test_async, args, kw)
            else:
                func(*args, **kw)
        finally:
            close_kernel()
        if exc_info:
            raise exc_info[0].with_traceback(exc_info[1], exc_info[2])
    return async_test


class AsyncTestCaseType(type):
    def __new__(cls, name, bases, attrs):
        for k in attrs:
            v = attrs[k]
            if k.startswith('test_') and callable(v):
                attrs[k] = async_test(v)
        return type.__new__(cls, name, bases, attrs)


class AsyncTestCase(unittest.TestCase, metaclass=AsyncTestCaseType):
    pass


def apatch(*args, **kw):
    p = patch(*args, **kw)
    def wrapper(func):
        @wraps(func)
        async def wrapped(*args, **kw):
            with p as m:
                return await func(*args, m, **kw)
        return wrapped
    return wrapper


async def dummy_coro(*args, **kw):
    pass
