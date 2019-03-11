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
from enkube.api.client import ApiError
from enkube import controller


class FooKind(Kind):
    apiVersion = 'enkube.local/v1'

class BarKind(Kind):
    apiVersion = 'enkube.local/v1'


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
        cache.add_kind.assert_called_once_with(CustomResourceDefinition)

    def test_init_skips_crd_handler_when_none_defined(self):
        cache = MagicMock()
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, cache)
        cache.subscribe.assert_not_called()
        cache.add_kind.assert_not_called()

    def test_init_adds_kinds_to_cache(self):
        class FooController(controller.Controller):
            kinds = [
                'v1/Kind1',
                ('v1/Kind2', {'kind2_arg': 'foo'}),
                FooKind,
                (BarKind, {'bar_arg': 'bar'}),
            ]
        cache = MagicMock()
        c = FooController(sentinel.mgr, sentinel.env, sentinel.api, cache)
        cache.add_kind.assert_has_calls([
            (('v1/Kind1',),),
            (('v1/Kind2',), {'kind2_arg': 'foo'}),
            ((FooKind,),),
            ((BarKind,), {'bar_arg': 'bar'}),
        ], any_order=True)

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
            self.assertTrue(c._crd_filter('DELETED', crd))
            self.assertFalse(c._crd_filter('ADDED', crd))
            self.assertFalse(c._crd_filter('MODIFIED', crd))
        self.assertFalse(c._crd_filter('DELETED', MagicMock(**{'_selfLink.return_value': 3})))

    async def test_crd_event(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, MagicMock())
        c.ensure_object = MagicMock(side_effect=dummy_coro)
        crd = MagicMock()
        c.crds = {crd._selfLink.return_value: crd}
        await c._crd_event(c.cache, 'DELETED', crd, None)
        c.ensure_object.assert_called_once_with(crd)

    async def test_ensure_object(self):
        api = MagicMock(**{'create.side_effect': dummy_coro})
        c = controller.Controller(sentinel.mgr, sentinel.env, api, MagicMock())
        await c.ensure_object(sentinel.obj)
        api.create.assert_called_once_with(sentinel.obj)

    async def test_ensure_object_ignores_conflict(self):
        async def conflict_coro(*args, **kw):
            raise ApiError(MagicMock(status_code=409))
        api = MagicMock(**{'create.side_effect': conflict_coro})
        c = controller.Controller(sentinel.mgr, sentinel.env, api, MagicMock())
        await c.ensure_object(sentinel.obj)
        api.create.assert_called_once_with(sentinel.obj)

    async def test_ensure_object_raises_non_conflict_errors(self):
        async def conflict_coro(*args, **kw):
            raise ApiError(MagicMock(status_code=500))
        api = MagicMock(**{'create.side_effect': conflict_coro})
        c = controller.Controller(sentinel.mgr, sentinel.env, api, MagicMock())
        with self.assertRaises(ApiError):
            await c.ensure_object(sentinel.obj)
        api.create.assert_called_once_with(sentinel.obj)

    async def test_ensure_crds(self):
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, MagicMock())
        c.ensure_object = MagicMock(side_effect=dummy_coro)
        crds = [MagicMock(), MagicMock()]
        c.crds = dict((crd._selfLink.return_value, crd) for crd in crds)
        await c.ensure_crds()
        c.ensure_object.assert_has_calls([call(crd) for crd in crds])

    async def test_spawn_and_close(self):
        expected_args = [((sentinel.arg1, sentinel.arg2), {})]
        call_args = []
        async def my_coro(*args, **kw):
            call_args.append((args, kw))
            await curio.Event().wait()
        c = controller.Controller(sentinel.mgr, sentinel.env, sentinel.api, MagicMock())
        t = await c.spawn(my_coro, *expected_args[0][0], **expected_args[0][1])
        self.assertEqual(t.name.rsplit('.', 1)[-1], 'my_coro')
        while not call_args:
            await curio.sleep(0)
        await c.close()
        self.assertEqual(call_args, expected_args)


def mock_controller_cls(run_coro=dummy_coro, close_coro=dummy_coro):
    return MagicMock(side_effect=lambda mgr, env, api, cache, **kw: MagicMock(**dict({
        'mgr': mgr, 'env': env, 'api': api, 'cache': cache,
        'run.side_effect': run_coro,
        'close.side_effect': close_coro,
        'ensure_crds.side_effect': dummy_coro,
    }, **kw)))


def mock_cache(run_coro=dummy_coro, resync_coro=dummy_coro):
    return MagicMock(**{
        '_controller_run_task': None,
        'run.side_effect': run_coro,
        'resync.side_effect': resync_coro,
    })


def mock_api_client_factory(env):
    return MagicMock(**{
        'env': env,
        'close.side_effect': dummy_coro,
    })


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
        self.api = mock_api_client_factory(sentinel.env)
        self.cache = mock_cache()
        self.mgr.envs[sentinel.env] = (self.api, self.cache, 1)
        self.mgr._run_controller_method = MagicMock(side_effect=self.mgr._run_controller_method)

    async def test_spawn_controller(self):
        cls = mock_controller_cls()
        kw = {'foo': 1, 'bar': 2}
        c = await self.mgr.spawn_controller(cls, sentinel.env, **kw)
        self.assertIs(c.env, sentinel.env)
        cls.assert_called_once_with(self.mgr, sentinel.env, self.api, self.cache, **kw)
        self.assertEqual(self.mgr.envs[sentinel.env][2], 2)
        c.ensure_crds.assert_not_called()

    async def test_spawn_controller_creates_api_and_cache(self):
        new_api = mock_api_client_factory(sentinel.new_env)
        self.mgr.api_client_factory = af = MagicMock(return_value=new_api)
        self.mgr.cache_factory = cf = MagicMock()
        cls = mock_controller_cls()
        await self.mgr.spawn_controller(cls, sentinel.new_env)
        cls.assert_called_once_with(self.mgr, sentinel.new_env, new_api, cf.return_value)
        af.assert_called_once_with(sentinel.new_env)
        cf.assert_called_once_with(new_api)
        api, cache, refs = self.mgr.envs[sentinel.new_env]
        self.assertIs(api, new_api)
        self.assertIs(cache, cf.return_value)
        self.assertEqual(refs, 1)

    async def test_stop_controller(self):
        ct = self.cache._controller_run_task = MagicMock(**{'cancel.side_effect': dummy_coro})
        c1 = mock_controller_cls()(self.mgr, sentinel.env, self.api, self.cache)
        c2 = mock_controller_cls()(self.mgr, sentinel.env, self.api, self.cache)
        self.mgr.controllers.add(c1)
        self.mgr.controllers.add(c2)
        self.mgr.envs[sentinel.env] = (self.api, self.cache, 2)
        await self.mgr.stop_controller(c1)
        c1.close.assert_called_once_with()
        c2.close.assert_not_called()
        self.assertEqual(self.mgr.envs[sentinel.env], (self.api, self.cache, 1))
        self.assertEqual(self.mgr.controllers, {c2})
        ct.cancel.assert_not_called()
        self.api.close.assert_not_called()
        self.mgr.log.info.assert_has_calls([
            call('stopping MagicMock'),
        ])

        await self.mgr.stop_controller(c2)
        c2.close.assert_called_once_with()
        self.assertNotIn(sentinel.env, self.mgr.envs)
        self.assertEqual(self.mgr.controllers, set())
        ct.cancel.assert_called_once_with()
        self.api.close.assert_called_once_with()
        self.mgr.log.info.assert_has_calls([
            call('stopping MagicMock'),
            call('stopping MagicMock'),
            call('shutting down cache and api for env'),
        ])

    async def test_stop_controller_before_run(self):
        c = mock_controller_cls()(self.mgr, sentinel.env, self.api, self.cache)
        self.mgr.controllers.add(c)
        await self.mgr.stop_controller(c)
        self.assertEqual(self.mgr.envs, {})
        self.assertEqual(self.mgr.controllers, set())
        self.api.close.assert_called_once_with()

    async def test_stop_controller_raises_runtimeerror_if_controller_not_running(self):
        c = mock_controller_cls()(self.mgr, sentinel.env, self.api, self.cache)
        with self.assertRaises(RuntimeError) as err:
            await self.mgr.stop_controller(c)
        self.assertEqual(err.exception.args[0], 'controller has not been spawned by this manager')
        c.close.assert_not_called()

    async def test_stop_controller_logs_exceptions(self):
        class FooError(Exception):
            pass
        async def close_coro():
            raise FooError()
        c = mock_controller_cls(close_coro=close_coro)(self.mgr, sentinel.env, self.api, self.cache)
        self.mgr.controllers.add(c)
        await self.mgr.stop_controller(c)
        self.mgr.log.exception.assert_called_once_with(
            'unhandled error in controller close method')

    async def test_run(self):
        self.mgr._run_cache = MagicMock(side_effect=dummy_coro)
        c1 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env)
        c2 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env)
        await self.mgr.run()
        self.mgr._run_cache.assert_called_once_with(self.cache)
        self.assertEqual(self.cache._controller_run_task.name, 'dummy_coro')
        c1.ensure_crds.assert_called_once_with()
        c2.ensure_crds.assert_called_once_with()
        c1.run.assert_called_once_with()
        c2.run.assert_called_once_with()
        c1.close.assert_called_once_with()
        c2.close.assert_called_once_with()
        self.mgr._run_controller_method.assert_has_calls([
            call(c1, 'run'), call(c2, 'run')], any_order=True)
        self.mgr.log.info.assert_has_calls([
            call('controller manager starting'),
            call('running MagicMock'),
            call('MagicMock.run finished'),
            call('running MagicMock'),
            call('MagicMock.run finished'),
            call('stopping MagicMock'),
            call('stopping MagicMock'),
            call('controller manager finished'),
        ])
        self.mgr.log.exception.assert_not_called()

    async def test_run_watch_signals(self):
        self.mgr._watch_signals = MagicMock(side_effect=dummy_coro)
        await self.mgr.run(watch_signals=True)
        self.mgr._watch_signals.assert_called_once_with()

    async def test_run_while_running_raises_runtimeerror(self):
        exceptions = []
        async def run_coro():
            try:
                await self.mgr.run()
            except Exception as err:
                exceptions.append(err)
        await self.mgr.spawn_controller(mock_controller_cls(run_coro=run_coro), sentinel.env)
        await self.mgr.run()
        err, = exceptions
        self.assertTrue(isinstance(err, RuntimeError))
        self.assertEqual(err.args[0], 'controller manager is already running')

    async def test_cancel_run(self):
        q = curio.Queue()
        async def blocking_coro():
            await q.put('start')
            try:
                await q.join()
            except curio.CancelledError:
                await q.put('cancelled')
                raise
            await q.put('finished')
        self.cache.run.side_effect = blocking_coro
        c1 = await self.mgr.spawn_controller(mock_controller_cls(blocking_coro), sentinel.env)
        c2 = await self.mgr.spawn_controller(mock_controller_cls(blocking_coro), sentinel.env)
        t = await curio.spawn(self.mgr.run)
        for _ in range(3):
            self.assertEqual(await q.get(), 'start')
        await t.cancel()
        for _ in range(3):
            self.assertEqual(await q.get(), 'cancelled')
        c1.close.assert_called_once_with()
        c2.close.assert_called_once_with()

    async def test_spawn_controller_while_running(self):
        self.mgr._run_cache = MagicMock(side_effect=dummy_coro)
        api2 = mock_api_client_factory(sentinel.env2)
        api3 = mock_api_client_factory(sentinel.env3)
        cache2 = mock_cache()
        cache3 = mock_cache()
        self.mgr.api_client_factory = MagicMock(return_value=api3)
        self.mgr.cache_factory = MagicMock(return_value=cache3)
        self.mgr.envs[sentinel.env2] = (api2, cache2, 1)

        async def run_coro():
            self.c3 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env)
            self.c4 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env3)
        c1 = await self.mgr.spawn_controller(mock_controller_cls(run_coro=run_coro), sentinel.env)
        c2 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env2)

        c1.ensure_crds.assert_not_called()
        c2.ensure_crds.assert_not_called()

        await self.mgr.run()

        c1.ensure_crds.assert_called_once_with()
        c2.ensure_crds.assert_called_once_with()
        self.c3.ensure_crds.assert_called_once_with()
        self.c4.ensure_crds.assert_called_once_with()

        self.mgr._run_controller_method.assert_has_calls([
            call(c1, 'run'), call(c2, 'run'), call(self.c3, 'run'), call(self.c4, 'run')
        ], any_order=True)
        c1.run.assert_called_once_with()
        c2.run.assert_called_once_with()
        self.c3.run.assert_called_once_with()
        self.c4.run.assert_called_once_with()

        c1.close.assert_called_once_with()
        c2.close.assert_called_once_with()
        self.c3.close.assert_called_once_with()
        self.c4.close.assert_called_once_with()

        self.assertEqual(self.cache._controller_run_task.name, 'dummy_coro')
        self.assertEqual(cache2._controller_run_task.name, 'dummy_coro')
        self.assertIs(cache3._controller_run_task, None)

        self.mgr._run_cache.assert_has_calls([
            call(self.cache),
            call(cache2),
            call(cache3),
        ])

        self.cache.resync.assert_called_once_with()
        cache2.resync.assert_not_called()
        cache3.resync.assert_not_called()

    async def test_run_cache(self):
        c = MagicMock(**{'run.side_effect': dummy_coro})
        await self.mgr._run_cache(c)
        c.run.assert_called_once_with()

    async def test_run_cache_logs_exceptions(self):
        class FooError(Exception):
            pass
        async def run_coro():
            raise FooError()
        c = MagicMock(**{'run.side_effect': run_coro})
        await self.mgr._run_cache(c)
        c.run.assert_called_once_with()
        self.mgr.log.exception.assert_called_once_with(
            'unhandled error in cache run method')

    async def test_run_cache_propagates_cancellederror(self):
        async def run_coro():
            raise curio.TaskCancelled()
        c = MagicMock(**{'run.side_effect': run_coro})
        with self.assertRaises(curio.CancelledError):
            await self.mgr._run_cache(c)
        self.mgr.log.exception.assert_not_called()

    async def test_run_controller_method(self):
        c = mock_controller_cls()(self.mgr, sentinel.env, sentinel.api, sentinel.cache)
        await self.mgr._run_controller_method(c, 'run')
        c.run.assert_called_once_with()
        self.mgr.log.debug.assert_not_called()
        self.mgr.log.info.assert_has_calls([
            call('running MagicMock'),
            call('MagicMock.run finished'),
        ])
        self.mgr.log.exception.assert_not_called()

    async def test_run_controller_method_passed_name(self):
        c = MagicMock(**{'foo.side_effect': dummy_coro})
        await self.mgr._run_controller_method(c, 'foo')
        c.foo.assert_called_once_with()
        self.mgr.log.debug.assert_not_called()
        self.mgr.log.info.assert_called_once_with('MagicMock.foo finished')
        self.mgr.log.exception.assert_not_called()

    async def test_run_controller_method_does_nothing_if_method_doesnt_exist(self):
        await self.mgr._run_controller_method(
            controller.Controller(self.mgr, sentinel.env, sentinel.api, sentinel.cache), 'run')
        self.mgr.log.debug.assert_not_called()
        self.mgr.log.info.assert_not_called()
        self.mgr.log.exception.assert_not_called()

    async def test_run_controller_logs_exceptions(self):
        class FooError(Exception):
            pass
        async def run_coro():
            raise FooError()
        c = mock_controller_cls(run_coro=run_coro)(
            self.mgr, sentinel.env, sentinel.api, sentinel.cache)
        await self.mgr._run_controller_method(c, 'run')
        self.mgr.log.info.assert_called_once_with('running MagicMock')
        self.mgr.log.exception.assert_called_once_with(
            'unhandled error in controller run method')

    async def test_run_controller_propagates_cancellederror(self):
        async def run_coro():
            raise curio.TaskCancelled()
        c = mock_controller_cls(run_coro=run_coro)(
            self.mgr, sentinel.env, sentinel.api, sentinel.cache)
        with self.assertRaises(curio.CancelledError):
            await self.mgr._run_controller_method(c, 'run')
        self.mgr.log.info.assert_has_calls([
            call('running MagicMock'),
            call('MagicMock.run cancelled'),
        ])
        self.mgr.log.exception.assert_not_called()

    async def test_context_manager(self):
        self.mgr._watch_signals = dummy_coro
        self.mgr.run = MagicMock(side_effect=dummy_coro)
        async with self.mgr as m:
            pass
        m.run.assert_called_once_with(watch_signals=True)

    async def test_context_manager_passes_exception(self):
        self.mgr._watch_signals = dummy_coro
        class FooError(Exception):
            pass
        self.mgr.run = MagicMock(side_effect=dummy_coro)
        with self.assertRaises(FooError):
            async with self.mgr as m:
                raise FooError()
        m.run.assert_not_called()

    @apatch('curio.SignalQueue')
    async def test_watch_signals(self, sq):
        self.mgr._taskgroup = MagicMock(**{'cancel_remaining.side_effect': dummy_coro})
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
                self.mgr._taskgroup.cancel_remaining.assert_called_once_with()
                self.mgr.log.info.assert_called_once_with(f'caught {signal.Signals(sig).name}')
                self.assertTrue(sq.return_value._exited)
                sq.reset_mock()
                self.mgr._taskgroup.reset_mock()
                self.mgr.log.reset_mock()

    @apatch('curio.SignalQueue')
    @apatch('signal.Signals')
    async def test_watch_signals_signame_missing(self, sq, s):
        s.side_effect = ValueError
        self.mgr._taskgroup = MagicMock(**{'cancel_remaining.side_effect': dummy_coro})
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
