"""Small deterministic Tk harness for overlay behavior tests.

The production overlay imports Tk lazily inside ``log_overlay._run``.  This
module swaps that import for recording widgets, runs exactly one initial refresh,
then returns without entering a real event loop.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass
from unittest import mock

import log_overlay


class FakeEvent:
    def __init__(self, widget=None, x=0, y=0):
        self.widget = widget
        self.x = x
        self.y = y


class FakeWidget:
    _serial = 0

    def __init__(self, parent=None, kind="widget", **options):
        type(self)._serial += 1
        self.id = type(self)._serial
        self.parent = parent
        self.kind = kind
        self.options = dict(options)
        self.bindings = {}
        self.pack_calls = []
        self.grid_calls = []
        self.place_calls = []
        self.operations = []
        self.tag_configs = {}
        self.children = []
        self.packed = False
        self.placed = False
        if isinstance(parent, FakeWidget):
            parent.children.append(self)

    def configure(self, **options):
        self.options.update(options)

    config = configure

    def cget(self, key):
        return self.options[key]

    def pack(self, **options):
        self.packed = True
        self.pack_calls.append(dict(options))
        return self

    def pack_forget(self):
        self.packed = False
        self.pack_calls.append({"forget": True})
        return self

    def pack_propagate(self, _flag):
        return self

    def grid(self, **options):
        self.grid_calls.append(dict(options))
        return self

    def place(self, **options):
        self.placed = True
        self.place_calls.append(dict(options))
        return self

    def columnconfigure(self, *_args, **_kwargs):
        return self

    def bind(self, sequence, callback=None, add=None):
        self.bindings[(sequence, bool(add))] = callback
        return f"bind-{self.id}-{sequence}"

    def unbind(self, sequence):
        self.bindings.pop((sequence, False), None)
        self.bindings.pop((sequence, True), None)

    def winfo_reqwidth(self):
        text = str(self.options.get("text", ""))
        return max(len(text) * 8, 8)

    def winfo_width(self):
        return int(self.options.get("width", 300) or 300)

    def winfo_x(self):
        return 100

    def winfo_y(self):
        return 50

    def winfo_id(self):
        return 1000 + self.id

    def yview(self):
        return (0.0, 1.0)

    def see(self, _index):
        return self

    def set(self, *_args):
        self.operations.append(("set",) + tuple(_args))

    def tag_config(self, tag, **options):
        self.tag_configs[tag] = dict(options)

    def insert(self, index, text, *tags):
        self.operations.append(("insert", index, text, tuple(tags)))

    def delete(self, *indices):
        self.operations.append(("delete",) + tuple(indices))

    def create_line(self, *args, **kwargs):
        self.operations.append(("line", args, dict(kwargs)))

    def create_oval(self, *args, **kwargs):
        self.operations.append(("oval", args, dict(kwargs)))

    def create_rectangle(self, *args, **kwargs):
        self.operations.append(("rectangle", args, dict(kwargs)))

    def invoke(self):
        command = self.options.get("command")
        if command is not None:
            return command()
        return None

    def fire(self, sequence, event=None):
        callback = self.bindings.get((sequence, False)) or self.bindings.get((sequence, True))
        if callback is None:
            raise AssertionError(f"{self.kind} has no binding for {sequence}")
        return callback(FakeEvent(self) if event is None else event)


class FakeRoot(FakeWidget):
    def __init__(self, dpi=96):
        super().__init__(parent=None, kind="root")
        self.dpi = dpi
        self.geometries = []
        self.calls = []
        self.after_calls = []
        self.bindings = {}
        self.destroyed = False
        self.overridden = False
        self.attributes_seen = []
        self._x = 0
        self._y = 0
        self._after_id = 0

    def overrideredirect(self, flag):
        self.overridden = bool(flag)

    def attributes(self, name, value=None):
        self.attributes_seen.append((name, value))

    @property
    def tk(self):
        return self

    def call(self, *args):
        self.calls.append(args)
        return None

    def winfo_fpixels(self, spec):
        if spec != "1i":
            raise ValueError(spec)
        return self.dpi

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def geometry(self, value):
        self.geometries.append(value)
        try:
            if "x" in value:
                size, position = value.split("+", 1)
                _width, _height = size.split("x", 1)
            else:
                position = value.lstrip("+")
            x, y = position.split("+", 1)
            self._x, self._y = int(x), int(y)
        except Exception:
            pass

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def update_idletasks(self):
        return self

    def after(self, delay, callback=None):
        self._after_id += 1
        self.after_calls.append((delay, callback))
        return self._after_id

    def mainloop(self):
        return None

    def destroy(self):
        self.destroyed = True


class FakeFont:
    def __init__(self, **_options):
        self.options = _options

    def measure(self, text):
        return len(str(text)) * 8


class FakeTkModule(types.ModuleType):
    def __init__(self, dpi=96):
        super().__init__("tkinter")
        self.root = FakeRoot(dpi=dpi)
        self.created = [self.root]
        self.scale_calls = 0
        self.font = types.ModuleType("tkinter.font")
        self.font.Font = FakeFont

    def _make(self, parent, kind, **options):
        widget = FakeWidget(parent=parent, kind=kind, **options)
        self.created.append(widget)
        return widget

    def Tk(self):
        return self.root

    def Frame(self, parent, **options):
        return self._make(parent, "frame", **options)

    def Label(self, parent, **options):
        return self._make(parent, "label", **options)

    def Button(self, parent, **options):
        return self._make(parent, "button", **options)

    def Canvas(self, parent, **options):
        return self._make(parent, "canvas", **options)

    def Scrollbar(self, parent, **options):
        return self._make(parent, "scrollbar", **options)

    def Text(self, parent, **options):
        return self._make(parent, "text", **options)

    def Scale(self, parent, **options):
        self.scale_calls += 1
        return self._make(parent, "scale", **options)


@dataclass
class OverlayUI:
    tk: FakeTkModule

    @property
    def root(self):
        return self.tk.root

    @property
    def widgets(self):
        return self.tk.created

    def widgets_of(self, kind, text=None):
        found = [w for w in self.widgets if w.kind == kind]
        if text is not None:
            found = [w for w in found if w.options.get("text") == text]
        return found

    def one(self, kind, text=None):
        found = self.widgets_of(kind, text=text)
        if len(found) != 1:
            texts = [w.options.get("text") for w in found]
            raise AssertionError(f"expected one {kind} {text!r}, got {len(found)}: {texts}")
        return found[0]

    def button(self, text):
        return self.one("button", text=text)

    def label(self, text):
        return self.one("label", text=text)


_CALLBACK_NAMES = (
    "_ON_START", "_ON_HALT", "_IS_RUNNING", "_ON_STOP_AFTER",
    "_IS_STOP_AFTER", "_IS_IN_GAME", "_IS_STARTING", "_SCORE", "_ON_EXIT",
    "_HUMAN_LIKE", "_CONCEDE_DETECT", "_LIVENESS", "_ACCOUNT",
    "_ON_TOGGLE_ACCOUNT", "_ON_CALIBRATE", "_ON_CALIBRATE_CLOSE",
    "_ON_RESTART", "_ON_EXIT_OVERLAY", "_LOGS", "_ON_OPEN_LOGS",
    "_ON_CLEAR_LOGS", "_ON_EXPORT_FEEDBACK", "_WINDOW_WIDTH",
    "_ON_RESIZE_WINDOW", "_VERSION_INFO",
)


@contextmanager
def overlay_environment(dpi=96, lines=None, tk_module=None, allow_reports=False, **callbacks):
    """Apply fake Tk and overlay callback state without running the window."""
    fake_tk = tk_module or FakeTkModule(dpi=dpi)
    saved = {name: getattr(log_overlay, name) for name in _CALLBACK_NAMES}
    saved.update(
        {
            "_LINES": log_overlay._LINES,
            "_DELAY": log_overlay._DELAY,
            "_STOP": log_overlay._STOP.is_set(),
            "_CLOSING": log_overlay._CLOSING.is_set(),
            "UI_SCALE": log_overlay.UI_SCALE,
        }
    )
    reports = []
    try:
        for name in _CALLBACK_NAMES:
            setattr(log_overlay, name, callbacks.get(name))
        log_overlay._LINES = log_overlay.deque(maxlen=2000) if lines is None else lines
        log_overlay._DELAY = None
        log_overlay._STOP.clear()
        log_overlay._CLOSING.clear()
        with (
            mock.patch.dict(sys.modules, {"tkinter": fake_tk, "tkinter.font": fake_tk.font}),
            mock.patch.object(log_overlay, "_disable_overlay_activation", lambda _root: None),
            mock.patch.object(log_overlay, "_raise_hearthstone", lambda: None),
            mock.patch.object(log_overlay, "_report", reports.append),
        ):
            yield fake_tk, reports
        if reports and not allow_reports:
            raise AssertionError(f"overlay reported errors: {reports}")
    finally:
        for name, value in saved.items():
            if name == "_STOP":
                log_overlay._STOP.set() if value else log_overlay._STOP.clear()
            elif name == "_CLOSING":
                log_overlay._CLOSING.set() if value else log_overlay._CLOSING.clear()
            else:
                setattr(log_overlay, name, value)


@contextmanager
def overlay_harness(dpi=96, lines=None, **callbacks):
    """Run ``log_overlay._run`` against recording widgets."""
    with overlay_environment(dpi=dpi, lines=lines, **callbacks) as (fake_tk, reports):
        log_overlay._run()
        if reports:
            raise AssertionError(f"overlay reported errors: {reports}")
        ui = OverlayUI(fake_tk)
        ui.ui_scale = log_overlay.UI_SCALE
        yield ui
        if reports:
            raise AssertionError(f"overlay reported errors: {reports}")
