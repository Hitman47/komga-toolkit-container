from __future__ import annotations

import inspect
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from .runtime import CancellationToken, CancelledError


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)
    cancelled = Signal()
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.token = CancellationToken()

    def cancel(self) -> None:
        self.token.cancel()

    def run(self) -> None:
        try:
            if self.args or self.kwargs:
                result = self.fn(*self.args, **self.kwargs)
            else:
                parameter_count = len(inspect.signature(self.fn).parameters)
                if parameter_count == 0:
                    result = self.fn()
                elif parameter_count == 1:
                    result = self.fn(self.token)
                else:
                    result = self.fn(self.token, self.signals.progress.emit)
            self.token.raise_if_cancelled()
            self.signals.result.emit(result)
        except CancelledError:
            self.signals.cancelled.emit()
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()
