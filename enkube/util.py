import json
from pygments import highlight, lexers, formatters


def format_json(obj):
    return highlight(
        json.dumps(obj, sort_keys=True, indent=2),
        lexers.JsonLexer(),
        formatters.TerminalFormatter()
    )


def format_diff(diff):
    return highlight(diff, lexers.DiffLexer(), formatters.TerminalFormatter())


def flatten_kube_lists(items):
    for obj in items:
        if obj['kind'].endswith('List'):
            for obj in flatten_kube_lists(obj['items']):
                yield obj
        else:
            yield obj
