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

import logging
import signal
from itertools import chain

import click
import curio

from .util import close_kernel, sync_wrap
from .plugins import PluginLoader
from .api.types import CustomResourceDefinition
from .api.client import ApiClient, ApiError
from .api.watcher import Watcher
from .api.cache import Cache
from .main import pass_env

LOG = logging.getLogger(__name__)


async def watch_signals(g):
    '''Wait for termination signals then cancel taskgroup.'''
    async with curio.SignalQueue(signal.SIGTERM, signal.SIGINT) as q:
        sig = await q.get()
        try:
            signame = signal.Signals(sig).name
        except ValueError:
            signame = f'signal {sig}'
        LOG.info(f'caught {signame}')
        await g.cancel_remaining()


@sync_wrap
async def main(api, crds, tasks, kw):
    LOG.info('starting controller')

    crds = dict((c._selfLink(), c) for c in crds)
    watcher = Watcher(api)
    cache = Cache(watcher)

    def crd_deleted_event_filter(event, obj):
        return event == 'DELETED' and obj._selfLink() in crds

    async def recreate_crd(cache, event, old, new):
        assert event == 'DELETED'
        path = old._selfLink()
        if path in crds:
            try:
                await api.create(crds[path])
            except ApiError as err:
                if err.resp.status_code != 409:
                    raise

    cache.subscribe(crd_deleted_event_filter, recreate_crd)

    try:
        if crds:
            await CustomResourceDefinition._watch(watcher)
            for crd in crds.values():
                await recreate_crd(cache, 'DELETED', crd, None)

        async with curio.TaskGroup() as g:
            await curio.spawn(watch_signals, g, daemon=True)
            await g.spawn(cache.run)

            for t in tasks:
                await g.spawn(t, api, watcher, cache, kw)

            async for t in g:
                LOG.debug(f'task {t.name} finished')

    finally:
        await watcher.cancel()

    LOG.info('controller exiting')


class CRDLoader(PluginLoader):
    entrypoint_type = 'enkube.controller.crds'


class TaskLoader(PluginLoader):
    entrypoint_type = 'enkube.controller.tasks'


class ParamLoader(PluginLoader):
    entrypoint_type = 'enkube.controller.params'


def cli():
    crds = list(chain.from_iterable(CRDLoader().load_all()))
    tasks = list(chain.from_iterable(TaskLoader().load_all()))
    params = list(chain.from_iterable(ParamLoader().load_all()))

    @click.command()
    @pass_env
    def cli(env, **kw):
        '''Run a Kubernetes controller.'''
        try:
            with ApiClient(env) as api:
                kw['api'] = api
                main(api, crds, tasks, kw)
        finally:
            close_kernel()

    cli.params.extend(params)

    return cli

cli = cli()
