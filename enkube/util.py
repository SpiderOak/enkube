import json
import yaml
import pyaml
from collections import OrderedDict
from pprint import pformat
from pygments import highlight, lexers, formatters


def load_yaml(stream, Loader=yaml.SafeLoader, object_pairs_hook=OrderedDict):
    class OrderedLoader(Loader):
        pass
    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
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
