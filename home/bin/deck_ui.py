"""Shared look for the deck's Textual menus: a blank line between list items.

Import and call install() before building any OptionList:
    import deck_ui; deck_ui.install()
"""
from textual.widgets import OptionList
from textual.widgets.option_list import Option


def spaced(options):
    """Put an empty, unselectable row between two selectable items."""
    out, prev = [], False
    for o in options:
        selectable = isinstance(o, Option) and not o.disabled
        if selectable and prev:
            out.append(Option("", disabled=True))
        out.append(o)
        prev = selectable
    return out


def install():
    if getattr(OptionList, "_deck_spaced", False):
        return
    init, add = OptionList.__init__, OptionList.add_options

    def __init__(self, *content, **kwargs):
        init(self, *spaced(content), **kwargs)

    def add_options(self, items):
        return add(self, spaced(list(items)))

    OptionList.__init__ = __init__
    OptionList.add_options = add_options
    OptionList._deck_spaced = True
