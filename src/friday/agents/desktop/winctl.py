"""Win32-обёртки для управления окнами и вводом (ctypes, без внешних зависимостей).

Работает только на Windows — каждая функция начинается с проверки платформы и на
других ОС кидает RuntimeError. mypy на Linux считает код после проверки недостижимым
и не проверяет — это ожидаемо (WinAPI-типов на Linux нет).

Возможности: список видимых окон, поиск по подстроке заголовка, фокус,
minimize/maximize/restore/close, ввод текста в активное окно (SendInput, Unicode).
"""

from __future__ import annotations

import sys
import time
from typing import Any

_SW_MINIMIZE = 6
_SW_MAXIMIZE = 3
_SW_RESTORE = 9
_WM_CLOSE = 0x0010

_INPUT_KEYBOARD = 1
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002


def list_windows() -> list[dict[str, Any]]:
    """Видимые окна с непустым заголовком: [{"hwnd": int, "title": str}, …]."""
    if sys.platform != "win32":
        raise RuntimeError("Win32 API доступно только на Windows")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[dict[str, Any]] = []

    def _on_window(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value.strip():
            found.append({"hwnd": int(hwnd), "title": buffer.value})
        return True

    # не декоратором: mypy strict считает WINFUNCTYPE(...) нетипизированным декоратором
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_on_window)
    user32.EnumWindows(enum_proc, 0)
    return found


def find_window(title_substr: str) -> dict[str, Any]:
    """Первое окно, чей заголовок содержит подстроку (без учёта регистра)."""
    needle = title_substr.strip().lower()
    if not needle:
        raise ValueError("нужна непустая подстрока заголовка окна")
    for win in list_windows():
        if needle in str(win["title"]).lower():
            return win
    raise RuntimeError(f"окно с заголовком, содержащим '{title_substr}', не найдено")


def focus_window(title_substr: str) -> dict[str, Any]:
    """Развернуть (если свёрнуто) и вывести окно на передний план.

    RuntimeError, если фокус реально не передан: иначе type_text напечатал бы
    в чужое окно (живой тест 2026-07-05 — текст ушёл мимо блокнота).
    """
    if sys.platform != "win32":
        raise RuntimeError("Win32 API доступно только на Windows")
    import ctypes

    win = find_window(title_substr)
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = int(win["hwnd"])
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    if user32.GetForegroundWindow() == hwnd:
        return win

    # Фоновому процессу Windows запрещает SetForegroundWindow (молча игнорирует).
    # Обход: прицепиться к input-очереди потока текущего foreground-окна через
    # AttachThreadInput — тогда система считает нас «активными» и разрешает передачу.
    foreground = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    our_thread = kernel32.GetCurrentThreadId()
    attached = bool(
        fg_thread
        and fg_thread != our_thread
        and user32.AttachThreadInput(our_thread, fg_thread, True)
    )
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, fg_thread, False)

    for _ in range(10):  # смена фокуса асинхронна — даём до ~0.5 с
        if user32.GetForegroundWindow() == hwnd:
            return win
        time.sleep(0.05)
    raise RuntimeError(f"не удалось передать фокус окну '{win['title']}' — ввод не будет доставлен")


def manage_window(title_substr: str, action: str) -> dict[str, Any]:
    """minimize | maximize | restore | close для окна по подстроке заголовка."""
    if sys.platform != "win32":
        raise RuntimeError("Win32 API доступно только на Windows")
    import ctypes

    win = find_window(title_substr)
    user32 = ctypes.windll.user32
    hwnd = int(win["hwnd"])
    if action == "minimize":
        user32.ShowWindow(hwnd, _SW_MINIMIZE)
    elif action == "maximize":
        user32.ShowWindow(hwnd, _SW_MAXIMIZE)
    elif action == "restore":
        user32.ShowWindow(hwnd, _SW_RESTORE)
    elif action == "close":
        user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
    else:
        raise ValueError(f"неизвестное действие '{action}' (minimize/maximize/restore/close)")
    return {**win, "action": action}


def send_text(text: str) -> int:
    """Напечатать текст в сфокусированный контрол активного окна.

    Основной путь — WM_CHAR напрямую в контрол с фокусом (GetGUIThreadInfo):
    живой тест 2026-07-05 показал, что SendInput через глобальную очередь ввода
    на машинах с кейборд-хуками (оверлеи NVIDIA/Discord) искажает текст —
    повторы и потери символов. Fallback — SendInput (Unicode), если контрол
    с фокусом определить не удалось.

    Возвращает число отправленных символов. Перевод строки шлётся как CR (0x0D) —
    большинство приложений трактуют его как Enter.
    """
    if sys.platform != "win32":
        raise RuntimeError("Win32 API доступно только на Windows")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    prepared = text.replace("\n", "\r")

    class _GuiThreadInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        )

    info = _GuiThreadInfo()
    info.cbSize = ctypes.sizeof(_GuiThreadInfo)
    foreground = user32.GetForegroundWindow()
    thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    focus_hwnd = 0
    if thread and user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
        focus_hwnd = int(info.hwndFocus or info.hwndActive or 0)
    if focus_hwnd:
        _WM_CHAR = 0x0102
        raw = prepared.encode("utf-16-le")  # по UTF-16-юнитам — переживают и эмодзи
        for i in range(0, len(raw), 2):
            unit = int.from_bytes(raw[i : i + 2], "little")
            user32.SendMessageW(focus_hwnd, _WM_CHAR, unit, 0)
        return len(text)

    ulong_ptr = ctypes.c_size_t

    class _KeybdInput(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        )

    class _MouseInput(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        )

    class _InputUnion(ctypes.Union):
        _fields_ = (("ki", _KeybdInput), ("mi", _MouseInput))

    class _Input(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", _InputUnion))

    events: list[_Input] = []
    for char in prepared:
        code = ord(char)
        for flags in (_KEYEVENTF_UNICODE, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP):
            key = _KeybdInput(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=0)
            events.append(_Input(type=_INPUT_KEYBOARD, union=_InputUnion(ki=key)))

    # Мелкими пачками с паузой: один большой SendInput приложения искажают
    # (живой тест 2026-07-05 — Notepad дублировал и терял символы)
    chunk_size = 16  # событий = 8 символов
    for start in range(0, len(events), chunk_size):
        part = events[start : start + chunk_size]
        array = (_Input * len(part))(*part)
        sent = user32.SendInput(len(part), array, ctypes.sizeof(_Input))
        if sent != len(part):
            raise RuntimeError(f"SendInput отправил {sent} из {len(part)} событий")
        time.sleep(0.01)
    return len(text)


def idle_seconds() -> float:
    """Секунды с последнего ввода пользователя (GetLastInputInfo)."""
    if sys.platform != "win32":
        raise RuntimeError("Win32 API доступно только на Windows")
    import ctypes
    from ctypes import wintypes

    class _LastInputInfo(ctypes.Structure):
        _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))

    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(_LastInputInfo)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise RuntimeError("GetLastInputInfo не сработал")
    elapsed_ms = int(ctypes.windll.kernel32.GetTickCount()) - int(info.dwTime)
    return max(0, elapsed_ms) / 1000.0
