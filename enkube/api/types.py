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

from copy import deepcopy
from urllib.parse import urlencode

from ..util import sync_wrap


class ValidationError(Exception):
    pass


class ValidationTypeError(ValidationError, TypeError):
    pass


class ValidationValueError(ValidationError, ValueError):
    pass


def required(typ):
    if isinstance(typ, tuple):
        typ, flags = typ
        return typ, set(flags) | {'required'}
    return typ, {'required'}


def list_of(typ):
    if isinstance(typ, tuple):
        typ, flags = typ
        return typ, set(flags) | {'list'}
    return typ, {'list'}


class KubeDictType(type):
    _allowed_flags = {'required', 'list'}

    def __new__(cls, name, bases, attrs):
        __annotations__ = dict(
            (field, (typ, flags))
            for b in bases
            for field, (typ, flags) in getattr(b, '__annotations__', {}).items()
        )
        __annotations__.update(attrs.get('__annotations__', {}))
        _defaults = dict(i for b in bases for i in getattr(b, '_defaults', {}).items())
        for field, typ in __annotations__.items():
            if isinstance(typ, tuple):
                typ, flags = typ
                flags = set(flags)
            else:
                flags = set()

            if not isinstance(typ, type):
                raise TypeError(f'{field}: annotation must be a type')

            unknown_flags = flags - cls._allowed_flags
            if unknown_flags:
                raise ValueError(f'{field}: unknown annotation flags: {unknown_flags}')

            __annotations__[field] = typ, flags

            if field in attrs:
                default = _defaults[field] = attrs[field]
                if 'list' in flags:
                    if not isinstance(default, list):
                        raise TypeError(f'default value for field {field} is of incorrect type')
                    for i in default:
                        if not isinstance(i, typ):
                            raise TypeError(f'default value for field {field} is of incorrect type')
                elif not isinstance(default, typ):
                    raise TypeError(f'default value for field {field} is of incorrect type')

        attrs['__annotations__'] = __annotations__
        attrs['_defaults'] = _defaults

        if not bases:
            bases = (dict,)

        return type.__new__(cls, name, bases, attrs)


class KubeDict(metaclass=KubeDictType):
    def __init__(self, *args, **kw):
        cls = type(self)
        self.update(deepcopy(cls._defaults))
        self.update(dict(*args, **kw))
        for k, (typ, flags) in cls.__annotations__.items():
            if k not in self:
                continue
            v = self[k]
            if 'list' in flags:
                if not isinstance(v, list):
                    continue
                for i, o in enumerate(v):
                    if not isinstance(o, typ):
                        v[i] = typ(o)
            else:
                if not isinstance(v, typ):
                    self[k] = typ(v)

    def _validate_field_types(self):
        for attr, (typ, flags) in type(self).__annotations__.items():
            if 'required' in flags and attr not in self:
                raise ValidationValueError(f'{attr} is a required field')
            if attr not in self:
                continue

            val = self[attr]

            if 'list' in flags:
                if not isinstance(val, list):
                    raise ValidationTypeError(
                        f'expected {attr} to be an instance of type '
                        f'list, got {type(val).__name__}'
                    )

                for i, item in enumerate(val):
                    if not isinstance(item, typ):
                        raise ValidationTypeError(
                            f'expected {attr} to be a list of {typ.__name__} instances, '
                            f'but item {i} is an instance of {type(item).__name__}'
                        )
                    validate = getattr(item, '_validate_field_types', None)
                    if validate:
                        validate()

            elif not isinstance(val, typ):
                raise ValidationTypeError(
                    f'expected {attr} to be an instance of type '
                    f'{typ.__name__}, got {type(val).__name__}'
                )

            validate = getattr(val, '_validate_field_types', None)
            if validate:
                validate()

    def _validate(self):
        self._validate_field_types()

    def __getattribute__(self, key):
        if not key.startswith('_') and not (
            key == 'items' and self.get('kind', '').endswith('List')
        ) and key in self:
            return self[key]
        return super(KubeDict, self).__getattribute__(key)

    def __setattr__(self, key, value):
        if key in self.__annotations__:
            self[key] = value
        else:
            super(KubeDict, self).__setattr__(key, value)

    def __delattr__(self, key):
        if key in self.__annotations__:
            try:
                del self[key]
            except KeyError:
                pass
        super(KubeDict, self).__delattr__(key)


class APIResource(KubeDict):
    kind: required(str)
    name: required(str)
    namespaced: required(bool)
    shortNames: list_of(str)
    singularName: required(str)
    verbs: required(list_of(str))


class APIResourceList(KubeDict):
    apiVersion: required(str) = 'v1'
    kind: required(str) = 'APIResourceList'
    groupVersion: required(str)
    resources: required(list_of(APIResource))


class ObjectMeta(KubeDict):
    # this is not exhaustive
    name: required(str)
    namespace: str
    annotations: dict
    finalizers: list_of(str)
    labels: dict
    resourceVersion: str
    selfLink: str
    uid: str


class KindType(KubeDictType):
    instances = {}

    def __new__(cls, name, bases, attrs):
        if 'kind' not in attrs:
            attrs['kind'] = name
        if '_singular' not in attrs:
            attrs['_singular'] = attrs['kind'].lower()
        if '_plural' not in attrs:
            attrs['_plural'] = f"{attrs['_singular']}s"
        if '_shortNames' not in attrs:
            attrs['_shortNames'] = []
        inst = super(KindType, cls).__new__(cls, name, bases, attrs)
        if hasattr(inst, 'apiVersion') and hasattr(inst, 'kind'):
            cls.instances[inst.apiVersion, inst.kind] = inst
        return inst

    @classmethod
    def getKind(cls, apiVersion, kind):
        return cls.instances[apiVersion, kind]

    @classmethod
    def from_apiresource(cls, apiVersion, resource):
        try:
            return cls.getKind(apiVersion, resource['kind'])
        except KeyError:
            pass
        attrs = {
            'apiVersion': apiVersion,
            '_plural': resource['name'],
            '_singular': resource['singularName'],
            '_namespaced': resource['namespaced'],
            '_shortNames': resource.get('shortNames', []),
        }
        return cls.__new__(cls, resource['kind'], (Kind,), attrs)


class Kind(KubeDict, metaclass=KindType):
    _namespaced = True
    apiVersion: required(str)
    kind: required(str)
    metadata: required(ObjectMeta)

    def _validate_namespace(self):
        typ = type(self)
        ns = self.metadata.get('namespace')
        if typ._namespaced:
            if not ns:
                raise ValidationValueError(
                    f'{typ.__name__} objects must have a namespace')
        else:
            if ns:
                raise ValidationValueError(
                    f'namespace specified but {typ.__name__} objects are cluster-scoped')

    def _validate(self):
        super(Kind, self)._validate()
        self._validate_namespace()

    def _selfLink(self, verb=None):
        if 'selfLink' in self.metadata:
            l = self.metadata['selfLink']
            if not verb:
                return l
            return l.replace(f'/{self.apiVersion}/', f'/{self.apiVersion}/{verb}/', 1)

        self._validate_namespace()
        return self._makeLink(
            name=self.metadata['name'],
            namespace=self.metadata.get('namespace'),
            verb=verb
        )

    @classmethod
    def _makeLink(cls, name=None, namespace=None, verb=None, **kw):
        components = [
            '',
            'apis' if '/' in cls.apiVersion else 'api',
            cls.apiVersion,
        ]
        if verb:
            components.append(verb)
        if namespace:
            if cls._namespaced:
                components.extend(['namespaces', namespace])
            else:
                raise ValueError('cannot specify namespace for cluster-scoped resource')
        components.append(cls._plural)
        query = {}
        if name:
            if namespace or not cls._namespaced:
                components.append(name)
            else:
                query['fieldSelector'] = f'metadata.name={name}'
        query.update(kw)
        query = ('?' + urlencode(query, safe='=')) if query else ''
        return '/'.join(components) + query

    @classmethod
    @sync_wrap
    async def _watch(cls, watcher, name=None, namespace=None, **kw):
        path = cls._makeLink(name=name, namespace=namespace, verb='watch', **kw)
        return await watcher.watch(path)


class CustomResourceDefinitionNames(KubeDict):
    kind: required(str)
    singular: required(str)
    plural: required(str)
    shortNames: required(list_of(str)) = []


class CustomResourceDefinitionSpec(KubeDict):
    group: required(str)
    version: required(str)
    scope: required(str)
    names: required(CustomResourceDefinitionNames)
    subresources: dict


class CustomResourceDefinition(Kind):
    _namespaced = False
    apiVersion = 'apiextensions.k8s.io/v1beta1'
    spec: required(CustomResourceDefinitionSpec)

    @classmethod
    def _from_kind(cls, kind):
        g, v = kind.apiVersion.split('/', 1)
        crd = cls({
            'metadata': { 'name': f'{kind._plural}.{g}', },
            'spec': {
                'group': g,
                'version': v,
                'scope': 'Namespaced' if kind._namespaced else 'Cluster',
                'names': {
                    'kind': kind.kind,
                    'singular': kind._singular,
                    'plural': kind._plural,
                    'shortNames': kind._shortNames,
                },
                #'validation': {
                #    'openAPIV3Schema': { 'properties': { 'spec': { 'properties': {
                #    } } } }
                #},
            },
        })
        subresources = getattr(kind, '_subresources', None)
        if subresources:
            crd['subresources'] = subresources
        crd._validate()
        return crd
