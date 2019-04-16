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

import pkg_resources

import click


class PluginLoader:
    @property
    def _entrypoints(self):
        if '_entrypoints' not in self.__dict__:
            self.__dict__['_entrypoints'] = dict(
                (ep.name, ep) for ep in
                pkg_resources.iter_entry_points(self.entrypoint_type)
            )
        return self.__dict__['_entrypoints']

    def list(self):
        return self._entrypoints.keys()

    def load(self, name):
        if '_plugins' not in self.__dict__:
            self.__dict__['_plugins'] = {}
        if name not in self._plugins:
            try:
                self._plugins[name] = self._entrypoints[name].load()
            except KeyError:
                return None
        return self._plugins[name]

    def load_all(self):
        return [self.load(name) for name in self.list()]


class CommandPluginLoader(click.MultiCommand, PluginLoader):
    entrypoint_type = 'enkube.commands'

    def list_commands(self, ctx):
        return self.list()

    def get_command(self, ctx, name):
        plugin = self.load(name)
        if plugin:
            return plugin()


class RenderPluginLoader(PluginLoader):
    entrypoint_type = 'enkube.renderers'
