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
        c = MyController(sentinel.mgr, sentinel.api, cache)
        cache.subscribe.assert_called_once_with(c._crd_filter, c._crd_event)
        cache.add_kind.assert_called_once_with(CustomResourceDefinition)

    def test_init_skips_crd_handler_when_none_defined(self):
        cache = MagicMock()
        c = controller.Controller(sentinel.mgr, sentinel.api, cache)
        self.assertFalse(cache.subscribe.called)
        self.assertFalse(cache.add_kind.called)

    def test_init_adds_kinds_to_cache(self):
        class FooController(controller.Controller):
            kinds = [
                'v1/Kind1',
                ('v1/Kind2', {'kind2_arg': 'foo'}),
                FooKind,
                (BarKind, {'bar_arg': 'bar'}),
            ]
        cache = MagicMock()
        c = FooController(sentinel.mgr, sentinel.api, cache)
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
        c = MyController(sentinel.mgr, sentinel.api, MagicMock())
        for crd in all_crds:
            self.assertTrue(c._crd_filter('DELETED', crd))
            self.assertFalse(c._crd_filter('ADDED', crd))
            self.assertFalse(c._crd_filter('MODIFIED', crd))
        self.assertFalse(c._crd_filter('DELETED', MagicMock(**{'_selfLink.return_value': 3})))

    async def test_crd_event(self):
        api = MagicMock(**{'create.side_effect': dummy_coro})
        c = controller.Controller(sentinel.mgr, api, MagicMock())
        crd = MagicMock()
        c.crds = {crd._selfLink.return_value: crd}
        await c._crd_event(c.cache, 'DELETED', crd, None)
        api.create.assert_called_once_with(crd)

    async def test_crd_event_ignores_conflict(self):
        async def conflict_coro(*args, **kw):
            raise ApiError(MagicMock(status_code=409))
        api = MagicMock(**{'create.side_effect': conflict_coro})
        c = controller.Controller(sentinel.mgr, api, MagicMock())
        crd = MagicMock()
        c.crds = {crd._selfLink.return_value: crd}
        await c._crd_event(c.cache, 'DELETED', crd, None)
        api.create.assert_called_once_with(crd)

    async def test_crd_event_raises_non_conflict_errors(self):
        async def conflict_coro(*args, **kw):
            raise ApiError(MagicMock(status_code=500))
        api = MagicMock(**{'create.side_effect': conflict_coro})
        c = controller.Controller(sentinel.mgr, api, MagicMock())
        crd = MagicMock()
        c.crds = {crd._selfLink.return_value: crd}
        with self.assertRaises(ApiError):
            await c._crd_event(c.cache, 'DELETED', crd, None)
        api.create.assert_called_once_with(crd)

    async def test_spawn_and_close(self):
        expected_args = [((sentinel.arg1, sentinel.arg2), {})]
        call_args = []
        async def my_coro(*args, **kw):
            call_args.append((args, kw))
            await curio.Event().wait()
        c = controller.Controller(sentinel.mgr, sentinel.api, MagicMock())
        t = await c.spawn(my_coro, *expected_args[0][0], **expected_args[0][1])
        self.assertEqual(t.name.rsplit('.', 1)[-1], 'my_coro')
        while not call_args:
            await curio.sleep(0)
        await c.close()
        self.assertEqual(call_args, expected_args)


def mock_controller_cls(run_coro=dummy_coro, close_coro=dummy_coro):
    return MagicMock(**{
        'return_value.run.side_effect': run_coro,
        'return_value.close.side_effect': close_coro,
    })


def mock_cache(run_coro=dummy_coro, resync_coro=dummy_coro):
    return MagicMock(**{
        'run.side_effect': run_coro,
        'resync.side_effect': resync_coro,
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
        self.api = MagicMock()
        self.cache = mock_cache()
        self.mgr.envs[sentinel.env] = (self.api, self.cache)
        self.mgr._run_controller_method = MagicMock(side_effect=self.mgr._run_controller_method)

    async def test_spawn_controller(self):
        cls = mock_controller_cls()
        c = await self.mgr.spawn_controller(cls, sentinel.env)
        self.assertIs(c, cls.return_value)
        cls.assert_called_once_with(self.mgr, self.api, self.cache)

    async def test_spawn_controller_creates_api_and_cache(self):
        self.mgr.api_client_factory = af = MagicMock()
        self.mgr.cache_factory = cf = MagicMock()
        cls = mock_controller_cls()
        await self.mgr.spawn_controller(cls, sentinel.new_env)
        cls.assert_called_once_with(self.mgr, af.return_value, cf.return_value)
        af.assert_called_once_with(sentinel.new_env)
        cf.assert_called_once_with(af.return_value)
        api, cache = self.mgr.envs[sentinel.new_env]
        self.assertIs(api, af.return_value)
        self.assertIs(cache, cf.return_value)

    async def test_run(self):
        c1 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env)
        c2 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env)
        await self.mgr.run()
        self.cache.run.assert_called_once_with()
        c1.run.assert_called_once_with()
        c2.run.assert_called_once_with()
        c1.close.assert_called_once_with()
        c2.close.assert_called_once_with()
        self.mgr._run_controller_method.assert_has_calls([call(c1), call(c2)])
        self.mgr.log.info.assert_has_calls([
            call('controller manager starting'),
            call('MagicMock.run finished'),
            call('MagicMock.run finished'),
            call('controller manager shutting down'),
            call('MagicMock.close finished'),
            call('MagicMock.close finished'),
        ])
        self.assertFalse(self.mgr.log.exception.called)

    async def test_run_watch_signals(self):
        self.mgr._watch_signals = MagicMock(side_effect=dummy_coro)
        await self.mgr.run(watch_signals=True)
        self.mgr._watch_signals.assert_called_once_with()

    async def test_run_logs_exceptions_when_closing_controllers(self):
        class FooError(Exception):
            pass
        async def close_coro():
            raise FooError()
        c1 = await self.mgr.spawn_controller(mock_controller_cls(close_coro=close_coro), sentinel.env)
        c2 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env)
        await self.mgr.run()
        c1.close.assert_called_once_with()
        c2.close.assert_called_once_with()
        self.mgr.log.info.assert_has_calls([
            call('controller manager starting'),
            call('MagicMock.run finished'),
            call('MagicMock.run finished'),
            call('controller manager shutting down'),
            call('MagicMock.close finished'),
        ])
        self.mgr.log.exception.assert_called_once_with(
            'unhandled error in controller close method')

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
        cache2 = mock_cache()
        cache3 = mock_cache()
        self.mgr.api_client_factory = MagicMock()
        self.mgr.cache_factory = MagicMock(return_value=cache3)
        self.mgr.envs[sentinel.env2] = (MagicMock(), cache2)

        async def run_coro():
            self.c3 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env)
            self.c4 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env3)
        c1 = await self.mgr.spawn_controller(mock_controller_cls(run_coro=run_coro), sentinel.env)
        c2 = await self.mgr.spawn_controller(mock_controller_cls(), sentinel.env2)
        await self.mgr.run()

        self.mgr._run_controller_method.assert_has_calls([
            call(c1), call(c2), call(self.c3), call(self.c4)])
        c1.run.assert_called_once_with()
        c2.run.assert_called_once_with()
        self.c3.run.assert_called_once_with()
        self.c4.run.assert_called_once_with()

        c1.close.assert_called_once_with()
        c2.close.assert_called_once_with()
        self.c3.close.assert_called_once_with()
        self.c4.close.assert_called_once_with()

        self.cache.run.assert_called_once_with()
        cache2.run.assert_called_once_with()
        cache3.run.assert_called_once_with()

        self.cache.resync.assert_called_once_with()
        self.assertFalse(cache2.resync.called)
        self.assertFalse(cache3.resync.called)

    async def test_run_controller_method(self):
        c = mock_controller_cls()(self.mgr, sentinel.api, sentinel.cache)
        await self.mgr._run_controller_method(c)
        c.run.assert_called_once_with()
        self.assertFalse(self.mgr.log.debug.called)
        self.mgr.log.info.assert_called_once_with('MagicMock.run finished')
        self.assertFalse(self.mgr.log.exception.called)

    async def test_run_controller_method_passed_name(self):
        c = MagicMock(**{'foo.side_effect': dummy_coro})
        await self.mgr._run_controller_method(c, 'foo')
        c.foo.assert_called_once_with()
        self.assertFalse(self.mgr.log.debug.called)
        self.mgr.log.info.assert_called_once_with('MagicMock.foo finished')
        self.assertFalse(self.mgr.log.exception.called)

    async def test_run_controller_method_does_nothing_if_method_doesnt_exist(self):
        await self.mgr._run_controller_method(
            controller.Controller(self.mgr, sentinel.api, sentinel.cache))
        self.assertFalse(self.mgr.log.debug.called)
        self.assertFalse(self.mgr.log.info.called)
        self.assertFalse(self.mgr.log.exception.called)

    async def test_run_controller_logs_exceptions(self):
        class FooError(Exception):
            pass
        async def run_coro():
            raise FooError()
        c = mock_controller_cls(run_coro=run_coro)(self.mgr, sentinel.api, sentinel.cache)
        await self.mgr._run_controller_method(c)
        self.assertFalse(self.mgr.log.info.called)
        self.mgr.log.exception.assert_called_once_with(
            'unhandled error in controller run method')

    async def test_run_controller_propagates_cancellederror(self):
        async def run_coro():
            raise curio.TaskCancelled()
        c = mock_controller_cls(run_coro=run_coro)(self.mgr, sentinel.api, sentinel.cache)
        with self.assertRaises(curio.CancelledError):
            await self.mgr._run_controller_method(c)
        self.mgr.log.info.assert_called_once_with('MagicMock.run cancelled')
        self.assertFalse(self.mgr.log.exception.called)

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
        self.assertFalse(m.run.called)

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
