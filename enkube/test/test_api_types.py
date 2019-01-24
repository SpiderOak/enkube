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

from .util import AsyncTestCase, dummy_coro

from enkube.api import types


class TestKubeDictType(unittest.TestCase):
    def test_adds_flags_to_annotations(self):
        class FooDict(metaclass=types.KubeDictType):
            foo: str
        self.assertEqual(FooDict.__annotations__, {
            'foo': (str, set()),
        })

    def test_copies_annotations_from_bases(self):
        class FooDict(metaclass=types.KubeDictType):
            foo: str
        class BarDict(FooDict):
            bar: str
        class BazDict(BarDict):
            baz: str
        self.assertEqual(BazDict.__annotations__, {
            'foo': (str, set()),
            'bar': (str, set()),
            'baz': (str, set()),
        })

    def test_raises_typeerror_if_annotation_isnt_a_type(self):
        with self.assertRaises(TypeError):
            class FooDict(metaclass=types.KubeDictType):
                foo: 1

    def test_required_flag(self):
        class FooDict(metaclass=types.KubeDictType):
            foo: types.required(str)
        self.assertEqual(FooDict.__annotations__, {
            'foo': (str, {'required'}),
        })

    def test_list_of_flag(self):
        class FooDict(metaclass=types.KubeDictType):
            foo: types.list_of(str)
        self.assertEqual(FooDict.__annotations__, {
            'foo': (str, {'list'}),
        })

    def test_both_flags(self):
        class FooDict(metaclass=types.KubeDictType):
            foo: types.required(types.list_of(str))
        self.assertEqual(FooDict.__annotations__, {
            'foo': (str, {'list', 'required'}),
        })

    def test_unknown_flag_raises_valueerror(self):
        with self.assertRaises(ValueError):
            class FooDict(metaclass=types.KubeDictType):
                foo: (str, {'foo'})

    def test_defaults(self):
        class FooDict(metaclass=types.KubeDictType):
            foo: str = 'bar'
        self.assertEqual(FooDict._defaults, {'foo': 'bar'})

    def test_defaults_list(self):
        class FooDict(metaclass=types.KubeDictType):
            foo: types.list_of(str) = ['bar', 'baz']
        self.assertEqual(FooDict._defaults, {'foo': ['bar', 'baz']})

    def test_default_of_incorrect_type_raises_typeerror(self):
        with self.assertRaises(TypeError):
            class FooDict(metaclass=types.KubeDictType):
                foo: str = 1

    def test_default_of_incorrect_type_raises_typeerror_list(self):
        with self.assertRaises(TypeError):
            class FooDict(metaclass=types.KubeDictType):
                foo: types.list_of(str) = 'bar'

    def test_default_of_incorrect_type_raises_typeerror_list_items(self):
        with self.assertRaises(TypeError):
            class FooDict(metaclass=types.KubeDictType):
                foo: types.list_of(str) = ['bar', 1]

    def test_dict_is_default_base(self):
        class FooDict(metaclass=types.KubeDictType):
            pass
        self.assertTrue(issubclass(FooDict, dict))


class TestKubeDict(unittest.TestCase):
    def test_defaults(self):
        class FooDict(types.KubeDict):
            foo: str = 'bar'
        self.assertEqual(FooDict(), {'foo': 'bar'})

    def test_defaults_deepcopies(self):
        class FooDict(types.KubeDict):
            foo: types.list_of(dict) = [{'bar': 'baz'}]
        inst = FooDict()
        inst.foo[0]['bar'] = 'baaar'
        inst.foo.append({'qux': 'quux'})
        self.assertEqual(FooDict._defaults, {'foo': [{'bar': 'baz'}]})

    def test_args(self):
        class FooDict(types.KubeDict):
            foo: str = 'bar'
        self.assertEqual(FooDict({'baz': 'qux'}), {'foo': 'bar', 'baz': 'qux'})

    def test_args_override_defaults(self):
        class FooDict(types.KubeDict):
            foo: str = 'bar'
        self.assertEqual(FooDict({'foo': 'qux'}), {'foo': 'qux'})

    def test_kwargs(self):
        class FooDict(types.KubeDict):
            foo: str = 'bar'
        self.assertEqual(FooDict(baz='qux'), {'foo': 'bar', 'baz': 'qux'})

    def test_kwargs_override_defaults(self):
        class FooDict(types.KubeDict):
            foo: str = 'bar'
        self.assertEqual(FooDict(foo='qux'), {'foo': 'qux'})

    def test_coerces_types(self):
        class FooDict(types.KubeDict):
            foo: str
        self.assertEqual(FooDict(foo=1), {'foo': '1'})

    def test_coerces_types_list(self):
        class FooDict(types.KubeDict):
            foo: types.list_of(str)
        self.assertEqual(FooDict(foo=[1, 2]), {'foo': ['1', '2']})

    def test_coercion_doesnt_fail_when_missing(self):
        class FooDict(types.KubeDict):
            foo: str
        self.assertEqual(FooDict(), {})

    def test_coercion_doesnt_fail_when_not_a_list(self):
        class FooDict(types.KubeDict):
            foo: types.list_of(str)
        self.assertEqual(FooDict(foo=1), {'foo': 1})

    def test_validate_missing_required(self):
        class FooDict(types.KubeDict):
            foo: types.required(str)
        with self.assertRaises(types.ValidationValueError):
            FooDict()._validate_field_types()

    def test_validate_skips_unknown(self):
        class FooDict(types.KubeDict):
            pass
        FooDict(foo=1)._validate_field_types()

    def test_validate_incorrect_type(self):
        class FooDict(types.KubeDict):
            foo: str
        inst = FooDict()
        inst['foo'] = 1
        with self.assertRaises(types.ValidationTypeError):
            inst._validate_field_types()

    def test_validate_incorrect_type_recursive(self):
        class BarDict(types.KubeDict):
            bar: str
        class FooDict(types.KubeDict):
            foo: BarDict
        bar = BarDict()
        bar['bar'] = 1
        foo = FooDict()
        foo['foo'] = bar
        with self.assertRaises(types.ValidationTypeError):
            foo._validate_field_types()

    def test_validate_incorrect_type_list(self):
        class FooDict(types.KubeDict):
            foo: types.list_of(str)
        inst = FooDict()
        inst['foo'] = 1
        with self.assertRaises(types.ValidationTypeError):
            inst._validate_field_types()

    def test_validate_incorrect_type_list_items(self):
        class FooDict(types.KubeDict):
            foo: types.list_of(str)
        inst = FooDict()
        inst['foo'] = ['bar', 1]
        with self.assertRaises(types.ValidationTypeError):
            inst._validate_field_types()

    def test_validate_incorrect_type_list_recursive(self):
        class BarDict(types.KubeDict):
            bar: str
        class FooDict(types.KubeDict):
            foo: types.list_of(BarDict)
        bar = BarDict()
        bar['bar'] = 1
        foo = FooDict()
        foo['foo'] = [bar]
        with self.assertRaises(types.ValidationTypeError):
            foo._validate_field_types()

    def test_validate_calls_validate_field_types(self):
        class Called(Exception):
            pass
        class FooDict(types.KubeDict):
            def _validate_field_types(self):
                raise Called()
        foo = FooDict()
        with self.assertRaises(Called):
            foo._validate()

    def test_attribute_access(self):
        class FooDict(types.KubeDict):
            foo: str
        foo = FooDict(foo='bar')
        self.assertEqual(foo.foo, 'bar')

    def test_attribute_access_not_a_field(self):
        class FooDict(types.KubeDict):
            foo: str
        foo = FooDict(foo='bar')
        with self.assertRaises(AttributeError):
            foo.bar

    def test_attribute_access_list_items(self):
        class FooDictList(types.KubeDict):
            kind: str = 'FooDictList'
            items: types.list_of(str)
        foo = FooDictList(items=['foo', 'bar'])
        self.assertEqual(list(sorted(foo.items())), [
            ('items', ['foo', 'bar']),
            ('kind', 'FooDictList')
        ])
        self.assertEqual(foo['items'], ['foo', 'bar'])

    def test_set_attribute(self):
        class FooDict(types.KubeDict):
            foo: str
        foo = FooDict()
        foo.foo = 'bar'
        self.assertEqual(foo['foo'], 'bar')

    def test_set_attribute_not_a_field(self):
        class FooDict(types.KubeDict):
            foo: str
        foo = FooDict()
        foo.bar = 'bar'
        self.assertNotIn('bar', foo)
        self.assertEqual(foo.bar, 'bar')

    def test_delete_attribute(self):
        class FooDict(types.KubeDict):
            foo: str
        foo = FooDict(foo='bar')
        del foo.foo
        self.assertEqual(foo, {})

    def test_delete_attribute_not_set(self):
        class FooDict(types.KubeDict):
            foo: str
        foo = FooDict()
        with self.assertRaises(AttributeError):
            del foo.foo

    def test_delete_attribute_not_a_field(self):
        class FooDict(types.KubeDict):
            foo: str
        foo = FooDict()
        foo.bar = 'bar'
        self.assertEqual(foo.__dict__, {'bar': 'bar'})
        del foo.bar
        self.assertEqual(foo.__dict__, {})


class TestKindType(unittest.TestCase):
    def setUp(self):
        types.Kind.instances.clear()

    def test_new_defaults(self):
        class MyKind(types.Kind):
            apiVersion = 'v1'
        self.assertEqual(MyKind.kind, 'MyKind')
        self.assertEqual(MyKind._singular, 'mykind')
        self.assertEqual(MyKind._plural, 'mykinds')
        self.assertEqual(MyKind._shortNames, [])
        self.assertIs(types.Kind.getKind('v1', 'MyKind'), MyKind)

    def test_new(self):
        class MyKind(types.Kind):
            apiVersion = 'v1'
            kind = 'FooBar'
            _singular = 'barfoo'
            _plural = 'foobaren'
            _shortNames = ['fb']
        self.assertEqual(MyKind.kind, 'FooBar')
        self.assertEqual(MyKind._singular, 'barfoo')
        self.assertEqual(MyKind._plural, 'foobaren')
        self.assertEqual(MyKind._shortNames, ['fb'])
        self.assertIs(types.Kind.getKind('v1', 'FooBar'), MyKind)

    def test_from_apiresource(self):
        r = types.APIResource(
            kind='MyKind',
            name='mykinds',
            namespaced=True,
            singularName='',
            verbs=['get'],
        )
        k = types.Kind.from_apiresource('v1', r)
        self.assertEqual(k.kind, 'MyKind')
        self.assertEqual(k._singular, '')
        self.assertEqual(k._plural, 'mykinds')
        self.assertEqual(k._shortNames, [])
        self.assertEqual(k._namespaced, True)
        self.assertIs(types.Kind.getKind('v1', 'MyKind'), k)

    def test_from_apiresource_cluster(self):
        r = types.APIResource(
            kind='MyKind',
            name='mykinds',
            namespaced=False,
            singularName='',
            verbs=['get'],
        )
        k = types.Kind.from_apiresource('v1', r)
        self.assertEqual(k.kind, 'MyKind')
        self.assertEqual(k._singular, '')
        self.assertEqual(k._plural, 'mykinds')
        self.assertEqual(k._shortNames, [])
        self.assertEqual(k._namespaced, False)
        self.assertIs(types.Kind.getKind('v1', 'MyKind'), k)

    def test_from_apiresource_cached(self):
        r = types.APIResource(
            kind='MyKind',
            name='mykinds',
            namespaced=True,
            singularName='',
            verbs=['get'],
        )
        k1 = types.Kind.from_apiresource('v1', r)
        k2 = types.Kind.from_apiresource('v1', r)
        self.assertIs(k1, k2)


class MyNamespacedKind(types.Kind):
    apiVersion = 'testing.enkube.local/v1'


class MyClusterKind(types.Kind):
    _namespaced = False
    apiVersion = 'testing.enkube.local/v1'


class TestKind(AsyncTestCase):
    def test_selflink(self):
        o = MyNamespacedKind(metadata={'selfLink': 'foobar'})
        self.assertEqual(o._selfLink(), 'foobar')

    def test_selflink_makelink_namespaced(self):
        o = MyNamespacedKind(metadata={'namespace': 'foo', 'name': 'bar'})
        self.assertEqual(
            o._selfLink(), '/apis/testing.enkube.local/v1/namespaces/foo/mynamespacedkinds/bar')

    def test_selflink_makelink_namespaced_without_namespace_raises_validationerror(self):
        o = MyNamespacedKind(metadata={'name': 'bar'})
        with self.assertRaises(types.ValidationValueError) as err:
            o._selfLink()
        self.assertEqual(err.exception.args[0], 'MyNamespacedKind objects must have a namespace')

    def test_selflink_makelink_cluster(self):
        o = MyClusterKind(metadata={'name': 'bar'})
        self.assertEqual(
            o._selfLink(), '/apis/testing.enkube.local/v1/myclusterkinds/bar')

    def test_selflink_makelink_cluster_with_namespace_raises_validationerror(self):
        o = MyClusterKind(metadata={'namespace': 'foo', 'name': 'bar'})
        with self.assertRaises(types.ValidationValueError) as err:
            o._selfLink()
        self.assertEqual(
            err.exception.args[0],
            'namespace specified but MyClusterKind objects are cluster-scoped'
        )

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

    async def test_watch(self):
        async def watch_coro(path):
            return sentinel.watch
        watcher = MagicMock()
        watcher.watch.side_effect = watch_coro
        self.assertIs(await MyClusterKind._watch(watcher), sentinel.watch)
        watcher.watch.assert_called_once_with(
            '/apis/testing.enkube.local/v1/myclusterkinds?watch=true'
        )


class TestObjectList(unittest.TestCase):
    def test_subclass_for_kind(self):
        self.assertTrue(issubclass(MyClusterKind.List, types.ObjectList))
        self.assertEqual(MyClusterKind.List.__name__, 'MyClusterKindList')
        self.assertEqual(MyClusterKind.List.kind, 'MyClusterKindList')
        self.assertEqual(MyClusterKind.List.apiVersion, 'testing.enkube.local/v1')
        self.assertEqual(MyClusterKind.List.__annotations__, {
            'apiVersion': (str, {'required'}),
            'kind': (str, {'required'}),
            'metadata': (types.ListMeta, {'required'}),
            'items': (MyClusterKind, {'required', 'list'})
        })


class TestCustomResourceDefinition(unittest.TestCase):
    def test_from_kind_namespaced(self):
        self.assertEqual(types.CustomResourceDefinition._from_kind(MyNamespacedKind), {
            'apiVersion': 'apiextensions.k8s.io/v1beta1',
            'kind': 'CustomResourceDefinition',
            'metadata': {
                'name': 'mynamespacedkinds.testing.enkube.local'
            },
            'spec': {
                'group': 'testing.enkube.local',
                'names': {
                    'kind': 'MyNamespacedKind',
                    'plural': 'mynamespacedkinds',
                    'shortNames': [],
                    'singular': 'mynamespacedkind'
                },
            'scope': 'Namespaced',
            'version': 'v1'
            }
        })

    def test_from_kind_cluster(self):
        self.assertEqual(types.CustomResourceDefinition._from_kind(MyClusterKind), {
            'apiVersion': 'apiextensions.k8s.io/v1beta1',
            'kind': 'CustomResourceDefinition',
            'metadata': {
                'name': 'myclusterkinds.testing.enkube.local'
            },
            'spec': {
                'group': 'testing.enkube.local',
                'names': {
                    'kind': 'MyClusterKind',
                    'plural': 'myclusterkinds',
                    'shortNames': [],
                    'singular': 'myclusterkind'
                },
            'scope': 'Cluster',
            'version': 'v1'
            }
        })

    def test_from_kind_subresources(self):
        class MySubresourceKind(types.Kind):
            apiVersion = 'testing.enkube.local/v1'
            _subresources = {'foo': 'bar'}
        self.assertEqual(types.CustomResourceDefinition._from_kind(MySubresourceKind), {
            'apiVersion': 'apiextensions.k8s.io/v1beta1',
            'kind': 'CustomResourceDefinition',
            'metadata': {
                'name': 'mysubresourcekinds.testing.enkube.local'
            },
            'spec': {
                'group': 'testing.enkube.local',
                'names': {
                    'kind': 'MySubresourceKind',
                    'plural': 'mysubresourcekinds',
                    'shortNames': [],
                    'singular': 'mysubresourcekind'
                },
                'subresources': {'foo': 'bar'},
            'scope': 'Namespaced',
            'version': 'v1'
            }
        })


if __name__ == '__main__':
    unittest.main()
