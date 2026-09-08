"""Source-only package paths. Never imports application code or searches cwd."""
from pathlib import Path


def backend_root(anchor=None):
    package = Path(__file__ if anchor is None else anchor).resolve().parent
    if package.name == 'acceptance':
        root = package.parent / 'mezan' / 'backend'
    elif package.name == 'exit2c' and package.parent.name == 'packaging':
        root = package.parent.parent / 'backend'
    else:
        raise AssertionError('SOURCE_LAYOUT_UNSUPPORTED')
    if not root.is_dir():
        raise AssertionError('SOURCE_ROOT_MISSING')
    return root
