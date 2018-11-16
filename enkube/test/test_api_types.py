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

import unittest
from unittest.mock import patch, MagicMock, sentinel

from enkube.api import types


class TestKindType(unittest.TestCase):
    def test_new_defaults(self):
        class MyKind(types.Kind):
            pass
        self.assertEqual(MyKind.kind, 'MyKind')
        self.assertEqual(MyKind._singular, 'mykind')
        self.assertEqual(MyKind._plural, 'mykinds')
        self.assertEqual(MyKind._shortNames, [])

    def test_new(self):
        class MyKind(types.Kind):
            kind = 'FooBar'
            _singular = 'barfoo'
            _plural = 'foobaren'
            _shortNames = ['fb']
        self.assertEqual(MyKind.kind, 'FooBar')
        self.assertEqual(MyKind._singular, 'barfoo')
        self.assertEqual(MyKind._plural, 'foobaren')
        self.assertEqual(MyKind._shortNames, ['fb'])


class MyNamespacedKind(types.Kind):
    apiVersion = 'testing.enkube.local/v1'


class MyClusterKind(types.Kind):
    _namespaced = False
    apiVersion = 'testing.enkube.local/v1'


class TestKind(unittest.TestCase):
    def test_selflink(self):
        o = MyNamespacedKind(metadata={'selfLink': 'foobar'})
        self.assertEqual(o._selfLink(), 'foobar')

    def test_selflink_with_verb(self):
        o = MyNamespacedKind(metadata={
            'selfLink': '/apis/testing.enkube.local/v1/namespaces/foo/mynamespacedkinds/bar'
        })
        self.assertEqual(
            o._selfLink(verb='watch'),
            '/apis/testing.enkube.local/v1/watch/namespaces/foo/mynamespacedkinds/bar'
        )

    def test_selflink_makelink_namespaced(self):
        o = MyNamespacedKind(metadata={'namespace': 'foo', 'name': 'bar'})
        self.assertEqual(
            o._selfLink(), '/apis/testing.enkube.local/v1/namespaces/foo/mynamespacedkinds/bar')

    def test_selflink_makelink_cluster(self):
        o = MyClusterKind(metadata={'name': 'bar'})
        self.assertEqual(
            o._selfLink(), '/apis/testing.enkube.local/v1/myclusterkinds/bar')

    def test_makelink_namespaced(self):
        self.assertEqual(
            MyNamespacedKind._makeLink('bar', 'foo'),
            '/apis/testing.enkube.local/v1/namespaces/foo/mynamespacedkinds/bar'
        )

    def test_makelink_namespaced_list(self):
        self.assertEqual(
            MyNamespacedKind._makeLink(namespace='foo'),
            '/apis/testing.enkube.local/v1/namespaces/foo/mynamespacedkinds'
        )

    def test_makelink_namespaced_list_all_namespaces(self):
        self.assertEqual(
            MyNamespacedKind._makeLink(),
            '/apis/testing.enkube.local/v1/mynamespacedkinds'
        )

    def test_makelink_namespaced_list_all_namespaces_with_name(self):
        self.assertEqual(
            MyNamespacedKind._makeLink('foo'),
            '/apis/testing.enkube.local/v1/mynamespacedkinds?fieldSelector=metadata.name=foo'
        )

    def test_makelink_namespaced_with_verb(self):
        self.assertEqual(
            MyNamespacedKind._makeLink('bar', 'foo', verb='watch'),
            '/apis/testing.enkube.local/v1/watch/namespaces/foo/mynamespacedkinds/bar'
        )

    def test_makelink_namespaced_list_with_verb(self):
        self.assertEqual(
            MyNamespacedKind._makeLink(namespace='foo', verb='watch'),
            '/apis/testing.enkube.local/v1/watch/namespaces/foo/mynamespacedkinds'
        )

    def test_makelink_namespaced_list_all_namespaces_with_verb(self):
        self.assertEqual(
            MyNamespacedKind._makeLink(verb='watch'),
            '/apis/testing.enkube.local/v1/watch/mynamespacedkinds'
        )

    def test_makelink_cluster(self):
        self.assertEqual(
            MyClusterKind._makeLink('bar'),
            '/apis/testing.enkube.local/v1/myclusterkinds/bar'
        )

    def test_makelink_cluster_list(self):
        self.assertEqual(
            MyClusterKind._makeLink(),
            '/apis/testing.enkube.local/v1/myclusterkinds'
        )

    def test_makelink_cluster_list_with_verb(self):
        self.assertEqual(
            MyClusterKind._makeLink(verb='watch'),
            '/apis/testing.enkube.local/v1/watch/myclusterkinds'
        )

    def test_makelink_cluster_with_namespace_raises_valueerror(self):
        with self.assertRaises(ValueError) as err:
            MyClusterKind._makeLink(namespace='foo'),
        self.assertEqual(
            err.exception.args[0], 'cannot specify namespace for cluster-scoped resource')

    def test_makelink_core_api(self):
        class CoreKind(types.Kind):
            _namespaced = False
            apiVersion = 'v1'
        self.assertEqual(CoreKind._makeLink('foo'), '/api/v1/corekinds/foo')

    def test_makelink_cluster_list_with_query(self):
        self.assertEqual(
            MyClusterKind._makeLink(foo='bar'),
            '/apis/testing.enkube.local/v1/myclusterkinds?foo=bar'
        )


if __name__ == '__main__':
    unittest.main()
