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

import signal
import unittest
from unittest.mock import patch, MagicMock, sentinel, call

import curio

from .util import AsyncTestCase, apatch, dummy_coro

from enkube.api.types import *
from enkube.api.client import ApiClient, ApiError
from enkube.api.cache import Cache
from enkube import controller


class FooKind(Kind):
    apiVersion = 'enkube.local/v1'

class BarKind(Kind):
    apiVersion = 'enkube.local/v1'


class FakeCacheSynchronizer:
    def __init__(self, cache, api, kind, **kw):
        self.warm = MagicMock(wraps=curio.Event())

    async def run(self):
        pass


class TestControllerType(unittest.TestCase):
    def test_dictifies_crds(self):
        crd = MagicMock(**{'_selfLink.return_value': sentinel.selflink})
        class FooController(metaclass=controller.ControllerType):
            crds = [crd]
        self.assertEqual(FooController.crds, {sentinel.selflink: crd})

    def test_includes_crds_from_bases(self):
        all_crds = [
            MagicMock(**{'_selfLink.return_value': 0}),
            MagicMock(**{'_selfLink.return_value': 1}),
            MagicMock(**{'_selfLink.return_value': 2}),
        ]
        class BaseController1(metaclass=controller.ControllerType):
            crds = [all_crds[0]]
        class BaseController2(metaclass=controller.ControllerType):
            crds = [all_crds[1]]
        class Controller(BaseController1, BaseController2):
            crds = [all_crds[0], all_crds[2]]
        self.assertEqual(Controller.crds, {
            0: all_crds[0],
            1: all_crds[1],
            2: all_crds[2]
        })

    def test_kinds(self):
        class FooController(metaclass=controller.ControllerType):
            kinds = [
                'v1/Kind1',
                ('v1/Kind2', {'kind2_arg': 'foo'}),
                FooKind,
                FooKind,
                (BarKind, {'bar_arg': 'bar'}),
            ]
        self.assertEqual(FooController.kinds, {
            ('v1/Kind1', frozenset()),
            ('v1/Kind2', frozenset({('kind2_arg', 'foo')})),
            (FooKind, frozenset()),
            (BarKind, frozenset({('bar_arg', 'bar')})),
        })

    def test_includes_kinds_from_bases(self):
        class BaseController1(metaclass=controller.ControllerType):
            kinds = [
                'v1/Kind1',
            ]
        class BaseController2(metaclass=controller.ControllerType):
            kinds = [
                ('v1/Kind2', {'kind2_arg': 'foo'}),
            ]
        class Controller(BaseController1, BaseController2):
            kinds = [
                FooKind,
                (BarKind, {'bar_arg': 'bar'}),
            ]
        self.assertEqual(Controller.kinds, {
            ('v1/Kind1', frozenset()),
            ('v1/Kind2', frozenset({('kind2_arg', 'foo')})),
            (FooKind, frozenset()),
            (BarKind, frozenset({('bar_arg', 'bar')})),
        })


class TestController(AsyncTestCase):
    def test_init_subscribes_crd_handler(self):
        crd = MagicMock(**{'_selfLink.return_value': sentinel.selflink})
        class MyController(controller.Controller):
            crds = [crd]
        cache = MagicMock()
        c = MyController(sentinel.mgr, sentinel.env, sentinel.api, cache)
        cache.subscribe.assert_called_once_with(c._crd_filter, c._crd_event)

    def test_init_skips_crd_handler_when_none_defined(self):
        cache = MagicMock()
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, cache)
        cache.subscribe.assert_not_called()

    def test_crd_filter(self):
        all_crds = [
            MagicMock(**{'_selfLink.return_value': 0}),
            MagicMock(**{'_selfLink.return_value': 1}),
            MagicMock(**{'_selfLink.return_value': 2}),
        ]
        class MyController(controller.Controller):
            crds = all_crds
        c = MyController(sentinel.mgr, sentinel.env, sentinel.api, MagicMock())
        for crd in all_crds:
            self.assertTrue(c._crd_filter('DELETED', crd, None))
            self.assertFalse(c._crd_filter('ADDED', None, crd))
            self.assertFalse(c._crd_filter('MODIFIED', crd, crd))
        self.assertFalse(c._crd_filter('DELETED', MagicMock(**{'_selfLink.return_value': 3}), None))

    async def test_crd_event(self):
        api = MagicMock(**{'ensure_object.side_effect': dummy_coro})
        c = controller.Controller(sentinel.mgr, sentinel.env, api, MagicMock())
        crd = MagicMock()
        c.crds = {crd._selfLink.return_value: crd}
        await c._crd_event(c.cache, 'DELETED', crd, None)
        api.ensure_object.assert_called_once_with(crd)

    async def test_ensure_object(self):
        api = MagicMock(**{'ensure_object.side_effect': dummy_coro})
        c = controller.Controller(sentinel.mgr, sentinel.env, api, MagicMock())
        await c.ensure_object(sentinel.obj)
        api.ensure_object.assert_called_once_with(sentinel.obj)

    async def test_ensure_object_skips_cached_objects(self):
        api = MagicMock(**{'ensure_object.side_effect': dummy_coro})
        obj = MagicMock()
        cache = {obj._selfLink.return_value: obj}
        c = controller.Controller(sentinel.mgr, sentinel.env, api, cache)
        await c.ensure_object(obj)
        api.ensure_object.assert_not_called()

    async def test_ensure_objects(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, MagicMock())
        c.ensure_object = MagicMock(side_effect=dummy_coro)
        await c.ensure_objects([sentinel.obj1, sentinel.obj2])
        c.ensure_object.assert_has_calls([
            call(sentinel.obj1),
            call(sentinel.obj2),
        ])

    async def test_ensure_crds(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, MagicMock())
        c.ensure_object = MagicMock(side_effect=dummy_coro)
        crds = [MagicMock(), MagicMock()]
        c.crds = dict((crd._selfLink.return_value, crd) for crd in crds)
        await c.ensure_crds()
        c.ensure_object.assert_has_calls([call(crd) for crd in crds], any_order=True)

    async def test_spawn(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, sentinel.cache)
        c._taskgroup = MagicMock(**{'spawn.side_effect': dummy_coro})
        await c.spawn(sentinel.coro, sentinel.arg1, kw1=sentinel.kw1)
        c._taskgroup.spawn.assert_called_once_with(sentinel.coro, sentinel.arg1, kw1=sentinel.kw1)

    async def test_spawn_when_not_running_raises_runtimeerror(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, sentinel.cache)
        with self.assertRaises(RuntimeError):
            await c.spawn(sentinel.coro, sentinel.arg1, kw1=sentinel.kw1)

    async def test_close(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, sentinel.cache)
        tg = c._taskgroup = MagicMock(**{
            'cancel_remaining.side_effect': dummy_coro,
            'join.side_effect': dummy_coro,
        })
        await c.close()
        tg.cancel_remaining.assert_called_once_with()
        tg.join.assert_called_once_with()
        self.assertIsNone(c._taskgroup)

    async def test_start(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, sentinel.cache)
        c.kinds = {
            (FooKind, frozenset({'foo': 'bar'}.items())),
            (BarKind, frozenset({'baz': 'qux'}.items())),
        }
        syncs = []
        def make_sync(*args, **kw):
            syncs.append(MagicMock(wraps=FakeCacheSynchronizer(*args, **kw)))
            return syncs[-1]
        c.cache_synchronizer_factory = MagicMock(side_effect=make_sync)
        c.ensure_crds = MagicMock(side_effect=dummy_coro)
        c._warmup = MagicMock(side_effect=dummy_coro)
        await c.start()
        await c._taskgroup.join()
        c.ensure_crds.assert_called_once_with()
        c.cache_synchronizer_factory.assert_has_calls([
            call(sentinel.cache, sentinel.api, FooKind, foo='bar'),
            call(sentinel.cache, sentinel.api, BarKind, baz='qux'),
        ], any_order=True)
        for sync in syncs:
            sync.run.assert_called_once_with()
        c._warmup.assert_called_once_with(syncs)

    async def test_start_with_string_kinds(self):
        async def getkind_coro(*args, **kw):
            return FooKind
        api = MagicMock(**{'getKind.side_effect': getkind_coro})
        c = controller.Controller(sentinel.mgr, sentinel.env, api, sentinel.cache)
        c.kinds = {
            ('enkube.local/v1/FooKind', frozenset({'foo': 'bar'}.items())),
        }
        syncs = []
        def make_sync(*args, **kw):
            syncs.append(MagicMock(wraps=FakeCacheSynchronizer(*args, **kw)))
            return syncs[-1]
        c.cache_synchronizer_factory = MagicMock(side_effect=make_sync)
        c.ensure_crds = MagicMock(side_effect=dummy_coro)
        c._warmup = MagicMock(side_effect=dummy_coro)
        await c.start()
        await c._taskgroup.join()
        c.ensure_crds.assert_called_once_with()
        c.cache_synchronizer_factory.assert_has_calls([
            call(sentinel.cache, api, FooKind, foo='bar'),
        ], any_order=True)
        for sync in syncs:
            sync.run.assert_called_once_with()
        c._warmup.assert_called_once_with(syncs)

    async def test_start_when_started_raises_runtimeerror(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, sentinel.cache)
        c._taskgroup = MagicMock()
        with self.assertRaises(RuntimeError):
            await c.start()

    async def test_warmup(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, sentinel.cache)
        c.spawn = MagicMock(side_effect=dummy_coro)
        syncs = [
            FakeCacheSynchronizer(sentinel.cache, sentinel.api, FooKind),
            FakeCacheSynchronizer(sentinel.cache, sentinel.api, BarKind),
        ]
        async with curio.TaskGroup() as g:
            await g.spawn(c._warmup, syncs)
            for sync in syncs:
                await sync.warm.set()
        for sync in syncs:
            sync.warm.wait.assert_called_once_with()
        c.spawn.assert_called_once_with(c.run)


def mock_controller_cls(close_coro=dummy_coro):
    cls = MagicMock(instances=[])
    def create(mgr, env, api, cache, **kw):
        cls.instances.append(MagicMock(**{
            'mgr': mgr, 'env': env, 'api': api, 'cache': cache,
            'spec': controller.Controller,
            'start.side_effect': dummy_coro,
            'join.side_effect': dummy_coro,
            'run.side_effect': dummy_coro,
            'close.side_effect': close_coro,
        }))
        return cls.instances[-1]
    cls.side_effect = create
    return cls


def mock_api_cls():
    cls = MagicMock(instances=[])
    def create(*args, **kw):
        cls.instances.append(MagicMock(**{
            'spec': ApiClient,
            'close.side_effect': dummy_coro,
        }))
        return cls.instances[-1]
    cls.side_effect = create
    return cls


def mock_cache_cls():
    return MagicMock(return_value=MagicMock(spec=Cache))


class FakeSignalQueue(curio.Queue):
    _exited = False
    async def __aenter__(self):
        return self
    async def __aexit__(self, typ, val, tb):
        self._exited = True


class TestControllerManager(AsyncTestCase):
    def setUp(self):
        self.mgr = controller.ControllerManager()
        self.mgr.log = MagicMock()
        self.mgr.api_client_factory = mock_api_cls()
        self.mgr.cache_factory = mock_cache_cls()

    @property
    def api(self):
        return self.mgr.api_client_factory.instances[-1]

    @property
    def cache(self):
        return self.mgr.cache_factory.return_value

    async def test_spawn_controller(self):
        cls = mock_controller_cls()
        kw = {'foo': 1, 'bar': 2}
        c = await self.mgr.spawn_controller(cls, sentinel.env, **kw)
        self.assertIs(c, cls.instances[-1])
        cls.assert_called_once_with(self.mgr, sentinel.env, self.api, self.cache, **kw)
        self.assertEqual(self.mgr.controllers, {c})
        self.assertEqual(self.mgr.envs, {
            sentinel.env: (self.api, self.cache, 1),
        })
        self.mgr.api_client_factory.assert_called_once_with(sentinel.env)
        self.mgr.cache_factory.assert_called_once_with()
        c.start.assert_called_once_with()

    async def test_spawn_controller_existing_env(self):
        self.mgr.envs = {sentinel.env: (
            self.mgr.api_client_factory(sentinel.env), self.mgr.cache_factory(), 1)}
        self.mgr.api_client_factory.reset_mock()
        self.mgr.cache_factory.reset_mock()
        cls = mock_controller_cls()
        kw = {'foo': 1, 'bar': 2}
        c = await self.mgr.spawn_controller(cls, sentinel.env, **kw)
        self.assertIs(c, cls.instances[-1])
        cls.assert_called_once_with(self.mgr, sentinel.env, self.api, self.cache, **kw)
        self.assertEqual(self.mgr.controllers, {c})
        self.assertEqual(self.mgr.envs, {
            sentinel.env: (self.api, self.cache, 2),
        })
        self.mgr.api_client_factory.assert_not_called()
        self.mgr.cache_factory.assert_not_called()
        c.start.assert_called_once_with()

    async def test_stop_controller(self):
        self.mgr.envs = {sentinel.env: (
            self.mgr.api_client_factory(sentinel.env), self.mgr.cache_factory(), 2)}
        c1 = mock_controller_cls()(self.mgr, sentinel.env, self.api, self.cache)
        c2 = mock_controller_cls()(self.mgr, sentinel.env, self.api, self.cache)
        self.mgr.controllers.add(c1)
        self.mgr.controllers.add(c2)
        await self.mgr.stop_controller(c1)
        c1.close.assert_called_once_with()
        c2.close.assert_not_called()
        self.assertEqual(self.mgr.envs, {
            sentinel.env: (self.api, self.cache, 1),
        })
        self.assertEqual(self.mgr.controllers, {c2})
        self.api.close.assert_not_called()
        self.assertEqual(self.mgr.log.info.call_args_list, [
            call('stopping MagicMock'),
        ])
        self.mgr.log.info.reset_mock()

        await self.mgr.stop_controller(c2)
        c2.close.assert_called_once_with()
        self.assertEqual(self.mgr.envs, {})
        self.assertEqual(self.mgr.controllers, set())
        self.api.close.assert_called_once_with()
        self.assertEqual(self.mgr.log.info.call_args_list, [
            call('stopping MagicMock'),
            call('shutting down api for env'),
        ])

    async def test_stop_controller_raises_runtimeerror_if_controller_not_running(self):
        c = mock_controller_cls()(self.mgr, sentinel.env, sentinel.api, sentinel.cache)
        with self.assertRaises(RuntimeError) as err:
            await self.mgr.stop_controller(c)
        self.assertEqual(err.exception.args[0], 'controller has not been spawned by this manager')
        c.close.assert_not_called()

    async def test_stop_controller_logs_exceptions(self):
        self.mgr.envs = {sentinel.env: (
            self.mgr.api_client_factory(sentinel.env), self.mgr.cache_factory(), 1)}
        class FooError(Exception):
            pass
        async def close_coro():
            raise FooError()
        c = mock_controller_cls(close_coro=close_coro)(
            self.mgr, sentinel.env, self.api, self.cache)
        self.mgr.controllers.add(c)
        await self.mgr.stop_controller(c)
        self.mgr.log.exception.assert_called_once_with(
            'unhandled error in controller close method')
        self.assertEqual(self.mgr.envs, {})
        self.assertEqual(self.mgr.controllers, set())
        self.api.close.assert_called_once_with()

    async def test_stop_controller_propagates_cancellation(self):
        self.mgr.envs = {sentinel.env: (
            self.mgr.api_client_factory(sentinel.env), self.mgr.cache_factory(), 1)}
        c = mock_controller_cls(close_coro=curio.Event().wait)(
            self.mgr, sentinel.env, self.api, self.cache)
        self.mgr.controllers.add(c)
        async with curio.TaskGroup() as g:
            task = await g.spawn(self.mgr.stop_controller, c)
            while not c.close.called:
                await curio.sleep(0)
            await task.cancel()
        self.mgr.log.exception.assert_not_called()
        self.assertEqual(self.mgr.envs, {
            sentinel.env: (self.api, self.cache, 1)
        })
        self.assertEqual(self.mgr.controllers, {c})
        self.api.close.assert_not_called()

    async def test_join_controller(self):
        c = MagicMock(**{'join.side_effect': dummy_coro})
        self.mgr.stop_controller = MagicMock(side_effect=dummy_coro)
        await self.mgr._join_controller(c)
        c.join.assert_called_once_with()
        self.mgr.stop_controller.assert_called_once_with(c)

    async def test_join_controller_logs_exceptions(self):
        async def join_coro():
            raise RuntimeError()
        c = MagicMock(**{'join.side_effect': join_coro})
        self.mgr.stop_controller = MagicMock(side_effect=dummy_coro)
        await self.mgr._join_controller(c)
        c.join.assert_called_once_with()
        self.mgr.stop_controller.assert_called_once_with(c)
        self.mgr.log.exception.assert_called_once_with(
            'unhandled exception in controller join method')

    async def test_join_controller_propagates_cancellation(self):
        c = MagicMock(**{'join.side_effect': curio.Event().wait})
        self.mgr.stop_controller = MagicMock(side_effect=dummy_coro)
        async with curio.TaskGroup() as g:
            task = await g.spawn(self.mgr._join_controller, c)
            while not c.join.called:
                await curio.sleep(0)
            await task.cancel()
        self.mgr.stop_controller.assert_called_once_with(c)
        self.mgr.log.exception.assert_not_called()

    async def test_context_manager_exits_on_signal(self):
        stop = curio.Event()
        self.mgr._watch_signals = MagicMock(side_effect=stop.wait)
        self.mgr._join_controller = MagicMock(side_effect=lambda *args: curio.Event().wait())
        self.mgr.controllers.add(sentinel.controller)
        async with curio.TaskGroup() as g:
            await g.spawn(self.mgr.__aexit__, None, None, None)
            while not self.mgr._watch_signals.called:
                await curio.sleep(0)
            await stop.set()
        self.mgr._join_controller.assert_called_once_with(sentinel.controller)
        self.mgr._watch_signals.assert_called_once_with()

    async def test_context_manager_exits_when_all_controllers_finished(self):
        stop = curio.Event()
        async def join_coro(*args, **kw):
            await stop.wait()
            self.mgr.controllers.discard(sentinel.controller)
        self.mgr._watch_signals = MagicMock(side_effect=curio.Event().wait)
        self.mgr._join_controller = MagicMock(side_effect=join_coro)
        self.mgr.controllers.add(sentinel.controller)
        async with curio.TaskGroup() as g:
            await g.spawn(self.mgr.__aexit__, None, None, None)
            while not self.mgr._join_controller.called:
                await curio.sleep(0)
            await stop.set()
        self.mgr._join_controller.assert_called_once_with(sentinel.controller)
        self.mgr._watch_signals.assert_called_once_with()

    @apatch('curio.SignalQueue')
    async def test_watch_signals(self, sq):
        all_signals = [signal.SIGTERM, signal.SIGINT]
        for sig in all_signals:
            with self.subTest(sig=sig):
                sq.return_value = FakeSignalQueue()
                async with curio.TaskGroup() as g:
                    await g.spawn(self.mgr._watch_signals)
                    await sq.return_value.put(sig)
                    async for t in g:
                        t.result # propagate exceptions
                sq.assert_called_once_with(*all_signals)
                self.mgr.log.info.assert_called_once_with(f'caught {signal.Signals(sig).name}')
                self.assertTrue(sq.return_value._exited)
                sq.reset_mock()
                self.mgr.log.reset_mock()

    @apatch('curio.SignalQueue')
    @apatch('signal.Signals')
    async def test_watch_signals_signame_missing(self, sq, s):
        s.side_effect = ValueError
        sq.return_value = FakeSignalQueue()
        async with curio.TaskGroup() as g:
            await g.spawn(self.mgr._watch_signals)
            await sq.return_value.put(15)
            async for t in g:
                t.result # propagate exceptions
        sq.assert_called_once_with(signal.SIGTERM, signal.SIGINT)
        self.mgr.log.info.assert_called_once_with('caught signal 15')


if __name__ == '__main__':
    unittest.main()
