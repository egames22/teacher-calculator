"""
교사용 계산기 v3
- 창 크기에 따라 버튼·글자 자동 조절
- NumLock 두 번 (1초 이내) → 새 계산기 창 실행
- = 누르면 한글 금액(금 XXX원 정) + 천원 단위 자동 표시
- 설정: 항상 위, 시작 자동실행, 투명도, 한글변환 숨김, X동작
- 만든이: 알잘딱버튼과 동일 스타일
"""

import tkinter as tk
import threading
import keyboard
import time
import sys
import os
import json
import subprocess
import tempfile
import winreg
import ctypes

# ─── 경로 유틸 ───────────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    EXE_PATH = sys.executable
    _ASSET_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_PATH = sys.executable
    _ASSET_DIR = BASE_DIR

SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
NUMLOCK_TMP   = os.path.join(tempfile.gettempdir(), "calc_numlock_last.tmp")
APP_NAME      = "교사용계산기"
APP_DATE      = "2026.5.27."
MY_PID        = os.getpid()
TMPDIR        = tempfile.gettempdir()
SPAWN_LOCK    = os.path.join(TMPDIR, "calc_spawn.tmp")
SPAWN_COOLDOWN = 1.5  # 초: 여러 인스턴스 동시 스폰 방지


def _state_file(pid: int = MY_PID) -> str:
    return os.path.join(TMPDIR, f"calc_state_{pid}.tmp")


def _restore_file(pid: int = MY_PID) -> str:
    return os.path.join(TMPDIR, f"calc_restore_{pid}.tmp")


def _write_state(state: str):
    try:
        with open(_state_file(), "w") as f:
            f.write(state)
    except Exception:
        pass


def _remove_state_files():
    for path in (_state_file(), _restore_file()):
        try:
            os.remove(path)
        except Exception:
            pass


def _is_pid_alive(pid: int) -> bool:
    try:
        # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) + GetExitCodeProcess
        # WaitForSingleObject는 SYNCHRONIZE 권한 필요 → 대신 GetExitCodeProcess 사용
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def _get_tray_and_visible_pids() -> tuple:
    tray_pids, visible_pids = [], []
    try:
        for fname in os.listdir(TMPDIR):
            if not (fname.startswith("calc_state_") and fname.endswith(".tmp")):
                continue
            try:
                pid = int(fname[len("calc_state_"):-4])
            except ValueError:
                continue
            fpath = os.path.join(TMPDIR, fname)
            if not _is_pid_alive(pid):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
                continue
            try:
                with open(fpath) as f:
                    state = f.read().strip()
                (tray_pids if state == "tray" else visible_pids).append(pid)
            except Exception:
                pass
    except Exception:
        pass
    return tray_pids, visible_pids


def _asset(filename: str) -> str:
    return os.path.join(_ASSET_DIR, filename)


def _load_photo(filename: str, size: tuple) -> "tk.PhotoImage | None":
    try:
        from PIL import Image, ImageTk
        img = Image.open(_asset(filename)).resize(size, Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


# ─── 설정 ────────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "close_to_tray":      True,
    "always_on_top":      False,
    "start_with_windows": True,
    "opacity":            100,
    "show_korean":        True,
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(s: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def apply_startup(enable: bool):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                             0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{EXE_PATH}"')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass


# ─── 한글 금액 변환 ──────────────────────────────────────────────────────────

_UNITS = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
_POS   = ["", "십", "백", "천"]
_BIG   = ["", "만", "억", "조"]


def _chunk_to_kor(chunk: int) -> str:
    s = ""
    for i in range(3, -1, -1):
        d = (chunk // (10 ** i)) % 10
        if d == 0:
            continue
        s += _UNITS[d] + _POS[i]
    return s


def to_korean_amount(n: int) -> str:
    if n == 0:
        return "금 영원 정"
    negative = n < 0
    n = abs(n)
    result = ""
    for i, unit in enumerate(_BIG):
        chunk = n % 10000
        if chunk:
            result = _chunk_to_kor(chunk) + unit + result
        n //= 10000
        if n == 0:
            break
    return ("금 마이너스 " if negative else "금 ") + result + "원 정"


def to_cheonwon(val: float) -> str:
    n = int(val // 1000)
    return f"{n:,}천원"


# ─── 커스텀 체크박스 ──────────────────────────────────────────────────────────

class DarkCheckbox(tk.Canvas):
    """다크 테마 체크박스 (색 채움 없이 테두리 + 체크 표시만)."""

    SZ = 17

    def __init__(self, parent: tk.Widget, variable: tk.BooleanVar, **kw):
        bg = kw.pop("bg", parent.cget("bg"))
        super().__init__(
            parent, width=self.SZ, height=self.SZ,
            bg=bg, highlightthickness=0, cursor="hand2", **kw
        )
        self._var = variable
        self._bg  = bg
        variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Button-1>", lambda _: self._var.set(not self._var.get()))
        self._draw()

    def _draw(self):
        self.delete("all")
        s = self.SZ
        # 빈 박스: 어두운 배경 + 회색 테두리
        self.create_rectangle(1, 1, s - 2, s - 2,
                              outline="#888888", width=1.5, fill="#333333")
        if self._var.get():
            # 흰색 체크 마크
            self.create_line(
                3, s // 2,
                s // 2 - 1, s - 4,
                s - 3, 3,
                fill="#ffffff", width=2.5,
                capstyle="round", joinstyle="round",
            )


# ─── 만든이 다이얼로그 ────────────────────────────────────────────────────────

class AboutDialog(tk.Toplevel):
    """알잘딱버튼과 동일 구조의 만든이 창."""

    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("만든이")
        self.configure(bg="#f5f5f5")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._images: list = []  # GC 방지

        self._build()
        self.after(80, self._center)

    def _center(self):
        self.update_idletasks()
        px = self.master.winfo_x() + self.master.winfo_width()  // 2
        py = self.master.winfo_y() + self.master.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px - w//2}+{py - h//2}")

    def _build(self):
        outer = tk.Frame(self, bg="#f5f5f5", padx=20, pady=16)
        outer.pack(fill="both", expand=True)

        # 행 1: 논산여상 로고 + 이름
        row1 = tk.Frame(outer, bg="#f5f5f5")
        row1.pack(fill="x", pady=(0, 12))

        logo1 = _load_photo("논산여상 로고.png", (56, 56))
        if logo1:
            self._images.append(logo1)
            tk.Label(row1, image=logo1, bg="#f5f5f5").pack(side="left")

        tk.Label(
            row1,
            text="장동욱  with  ClaudeCode",
            bg="#f5f5f5", fg="#1a1a1a",
            font=("맑은 고딕", 13, "bold"),
        ).pack(side="left", padx=14)

        # 행 2: 갓쌤에듀 로고 (중앙)
        row2 = tk.Frame(outer, bg="#f5f5f5")
        row2.pack(fill="x", pady=(0, 12))

        logo2 = _load_photo("갓쌤에듀 로고.png", (220, 55))
        if logo2:
            self._images.append(logo2)
            tk.Label(row2, image=logo2, bg="#f5f5f5").pack()
        else:
            tk.Label(row2, text="갓쌤에듀", bg="#f5f5f5", fg="#555",
                     font=("맑은 고딕", 12)).pack()

        # 날짜 + 프로그램명
        tk.Label(
            outer,
            text=f"{APP_DATE}  {APP_NAME}",
            bg="#f5f5f5", fg="#555555",
            font=("맑은 고딕", 10),
        ).pack(pady=(0, 14))

        # 닫기 버튼
        tk.Button(
            outer, text="닫기",
            command=self.destroy,
            bg="#ffffff", fg="#1a1a1a",
            relief="solid", borderwidth=1,
            font=("맑은 고딕", 10),
            width=8, cursor="hand2",
            activebackground="#e8f0fe",
            activeforeground="#0078d4",
        ).pack()


# ─── 설정 다이얼로그 ──────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):

    def __init__(self, parent: "Calculator", settings: dict):
        super().__init__(parent)
        self.parent = parent
        self.title("설정")
        self.configure(bg="#1e1e1e")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._s = dict(settings)

        self._close_tray  = tk.BooleanVar(value=self._s["close_to_tray"])
        self._on_top      = tk.BooleanVar(value=self._s["always_on_top"])
        self._startup     = tk.BooleanVar(value=self._s["start_with_windows"])
        self._opacity     = tk.IntVar(value=self._s["opacity"])
        self._show_kor    = tk.BooleanVar(value=self._s["show_korean"])

        self._build()
        self.after(80, self._center)

    def _center(self):
        self.update_idletasks()
        px = self.parent.winfo_x() + self.parent.winfo_width()  // 2
        py = self.parent.winfo_y() + self.parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px - w//2}+{py - h//2}")

    def _section(self, text: str) -> tk.Frame:
        f = tk.Frame(self, bg="#2a2a2a")
        f.pack(fill="x", padx=10, pady=(10, 2))
        tk.Label(f, text=text, bg="#2a2a2a", fg="#888",
                 font=("맑은 고딕", 9)).pack(anchor="w", padx=8, pady=4)
        return f

    def _row(self, label: str, make_widget):
        f = tk.Frame(self, bg="#1e1e1e")
        f.pack(fill="x", padx=16, pady=5)
        tk.Label(f, text=label, bg="#1e1e1e", fg="#cccccc",
                 font=("맑은 고딕", 10), width=22, anchor="w").pack(side="left")
        make_widget(f).pack(side="left")

    def _build(self):
        # ── 동작
        self._section("동작")
        self._row("X 버튼: 백그라운드 대기",
                  lambda p: DarkCheckbox(p, self._close_tray, bg="#1e1e1e"))
        self._row("항상 위에 표시",
                  lambda p: DarkCheckbox(p, self._on_top, bg="#1e1e1e"))
        self._row("Windows 시작 시 자동 실행",
                  lambda p: DarkCheckbox(p, self._startup, bg="#1e1e1e"))

        # ── 표시
        self._section("표시")
        self._row("한글 금액 변환 표시",
                  lambda p: DarkCheckbox(p, self._show_kor, bg="#1e1e1e"))

        # 투명도 슬라이더
        f_op = tk.Frame(self, bg="#1e1e1e")
        f_op.pack(fill="x", padx=16, pady=5)
        tk.Label(f_op, text="창 투명도", bg="#1e1e1e", fg="#cccccc",
                 font=("맑은 고딕", 10), width=22, anchor="w").pack(side="left")
        self._op_lbl = tk.Label(f_op, text=f"{self._opacity.get()}%",
                                bg="#1e1e1e", fg="#e67e22",
                                font=("맑은 고딕", 10), width=5)
        self._op_lbl.pack(side="right")
        tk.Scale(
            f_op, from_=30, to=100, orient="horizontal",
            variable=self._opacity,
            bg="#1e1e1e", fg="#ccc", troughcolor="#444",
            highlightthickness=0, showvalue=False, length=130,
            command=lambda v: self._op_lbl.configure(text=f"{int(float(v))}%"),
        ).pack(side="left", padx=(0, 4))

        # ── 만든이
        self._section("만든이")
        f_ab = tk.Frame(self, bg="#1e1e1e")
        f_ab.pack(fill="x", padx=16, pady=(4, 10))
        tk.Button(
            f_ab, text="ℹ  만든이 정보",
            command=lambda: AboutDialog(self.parent),
            bg="#2d2d2d", fg="#74b9ff",
            relief="flat", borderwidth=0,
            font=("맑은 고딕", 10), cursor="hand2",
            activebackground="#3d3d3d", activeforeground="#fff",
            padx=10, pady=4,
        ).pack(anchor="w")

        # ── 저장 / 취소
        btn_f = tk.Frame(self, bg="#1e1e1e")
        btn_f.pack(fill="x", padx=10, pady=(4, 12))
        tk.Button(btn_f, text="취소", command=self.destroy,
                  bg="#444", fg="#fff", relief="flat",
                  font=("맑은 고딕", 10), width=8).pack(side="right", padx=4)
        tk.Button(btn_f, text="저장", command=self._save,
                  bg="#e67e22", fg="#fff", relief="flat",
                  font=("맑은 고딕", 10, "bold"), width=8).pack(side="right", padx=4)

    def _save(self):
        self._s["close_to_tray"]      = self._close_tray.get()
        self._s["always_on_top"]      = self._on_top.get()
        self._s["start_with_windows"] = self._startup.get()
        self._s["opacity"]            = self._opacity.get()
        self._s["show_korean"]        = self._show_kor.get()

        save_settings(self._s)
        apply_startup(self._s["start_with_windows"])
        self.parent.apply_settings(self._s)
        self.destroy()


# ─── 계산기 레이아웃 ──────────────────────────────────────────────────────────

BUTTON_LAYOUT = [
    ["C",  "±",  "%",   "÷"],
    ["7",  "8",  "9",   "×"],
    ["4",  "5",  "6",   "−"],
    ["1",  "2",  "3",   "+"],
    ["0",  "",   ".",   "="],
    ["한글", "",  "",   "천원"],
]
BTN_COLORS = {
    "C":   ("#e74c3c", "#fff"),
    "±":   ("#555555", "#fff"),
    "%":   ("#555555", "#fff"),
    "÷":   ("#e67e22", "#fff"),
    "×":   ("#e67e22", "#fff"),
    "−":   ("#e67e22", "#fff"),
    "+":   ("#e67e22", "#fff"),
    "=":   ("#e67e22", "#fff"),
    "한글": ("#1a6b3a", "#fff"),
    "천원": ("#1a4a7a", "#fff"),
}
DEFAULT_CLR = ("#2d2d2d", "#ffffff")


# ─── 계산기 메인 창 ──────────────────────────────────────────────────────────

class Calculator(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("교사용 계산기")
        self.configure(bg="#1a1a1a")
        self.minsize(260, 460)
        self.geometry("340x620")

        self._settings = load_settings()
        self._entry = "0"
        self._num1  = None
        self._op    = None
        self._just_evaluated = False
        self._buttons: dict[str, tk.Button] = {}
        self._tray_icon = None
        self._icon_photo = None
        self._max_dec = 4

        self._build_ui()
        self._apply_window_icon()
        self._bind_keys()
        self.apply_settings(self._settings)
        self.after(80, self._resize_fonts)
        self.bind("<Configure>", lambda _: self.after(60, self._resize_fonts))
        _write_state("visible")
        self.after(500, self._check_restore_req)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_rowconfigure(0, weight=3)
        self.grid_rowconfigure(1, weight=2)
        for r in range(2, 8):
            self.grid_rowconfigure(r, weight=3)
        for c in range(4):
            self.grid_columnconfigure(c, weight=1)

        # 디스플레이 프레임
        disp = tk.Frame(self, bg="#1a1a1a")
        disp.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=8, pady=(8, 0))
        disp.grid_columnconfigure(0, weight=1)
        disp.grid_rowconfigure(1, weight=1)
        disp.grid_rowconfigure(2, weight=2)

        self._gear_btn = tk.Button(
            disp, text="⚙", command=self._open_settings,
            bg="#1a1a1a", fg="#666", relief="flat",
            font=("맑은 고딕", 11), cursor="hand2",
            activebackground="#1a1a1a", activeforeground="#fff",
        )
        self._gear_btn.grid(row=0, column=1, sticky="ne", padx=2)

        self._expr_var = tk.StringVar(value="")
        self._expr_lbl = tk.Label(
            disp, textvariable=self._expr_var,
            bg="#1a1a1a", fg="#888888", anchor="e", justify="right",
        )
        self._expr_lbl.grid(row=1, column=0, columnspan=2, sticky="ew")

        self._disp_var = tk.StringVar(value="0")
        self._disp_lbl = tk.Label(
            disp, textvariable=self._disp_var,
            bg="#1a1a1a", fg="#ffffff", anchor="e", justify="right",
        )
        self._disp_lbl.grid(row=2, column=0, columnspan=2, sticky="ew")

        # 한글 변환 패널
        self._kor_frame = tk.Frame(self, bg="#242424")
        self._kor_frame.grid(row=1, column=0, columnspan=4,
                             sticky="nsew", padx=8, pady=4)
        self._kor_frame.grid_columnconfigure(0, weight=1)

        self._kor_var = tk.StringVar(value="계산 결과가 여기 표시됩니다")
        self._kor_lbl = tk.Label(
            self._kor_frame, textvariable=self._kor_var,
            bg="#242424", fg="#ffd700",
            anchor="w", justify="left", cursor="hand2", wraplength=300,
        )
        self._kor_lbl.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 1))
        self._kor_lbl.bind("<Button-1>", lambda _: self._copy(self._kor_var.get()))

        self._cheon_var = tk.StringVar(value="")
        self._cheon_lbl = tk.Label(
            self._kor_frame, textvariable=self._cheon_var,
            bg="#242424", fg="#74b9ff",
            anchor="w", justify="left", cursor="hand2",
        )
        self._cheon_lbl.grid(row=1, column=0, sticky="ew", padx=8, pady=(1, 4))
        self._cheon_lbl.bind("<Button-1>", lambda _: self._copy(self._cheon_var.get()))

        # 버튼 그리드
        for r, row in enumerate(BUTTON_LAYOUT):
            for c, label in enumerate(row):
                if label == "":
                    continue
                colspan = 3 if label == "한글" else (2 if label == "0" else 1)
                bg, fg = BTN_COLORS.get(label, DEFAULT_CLR)
                btn = tk.Button(
                    self, text=label,
                    bg=bg, fg=fg,
                    activebackground=self._brighten(bg),
                    activeforeground=fg,
                    relief="flat", borderwidth=0,
                    command=lambda l=label: self._press(l),
                )
                btn.grid(row=r + 2, column=c, columnspan=colspan,
                         sticky="nsew", padx=2, pady=2)
                self._buttons[label] = btn

    def _brighten(self, c: str) -> str:
        try:
            return "#{:02x}{:02x}{:02x}".format(
                min(255, int(c[1:3], 16) + 40),
                min(255, int(c[3:5], 16) + 40),
                min(255, int(c[5:7], 16) + 40),
            )
        except Exception:
            return c

    # ── 아이콘 ───────────────────────────────────────────────────────────────

    def _apply_window_icon(self):
        try:
            from PIL import Image, ImageTk
            img = Image.open(_asset("교사용계산기 이미지.png"))
            self._icon_photo = ImageTk.PhotoImage(img)
            self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    # ── 트레이 ───────────────────────────────────────────────────────────────

    def _check_restore_req(self):
        req_file = _restore_file()
        if os.path.exists(req_file):
            try:
                os.remove(req_file)
            except Exception:
                pass
            if self._tray_icon is not None:
                self._restore_from_tray(self._tray_icon)
            else:
                self.deiconify()
                self.lift()
                self.focus_force()
        self.after(500, self._check_restore_req)

    def _hide_to_tray(self):
        # 트레이에 이미 1개 있으면 이 인스턴스는 종료 (설정 무관)
        tray_pids, _ = _get_tray_and_visible_pids()
        if tray_pids:
            self.destroy()
            return
        _write_state("tray")
        self.withdraw()
        if self._tray_icon is not None:
            return
        try:
            import pystray
            from PIL import Image
            img = Image.open(_asset("교사용계산기 이미지.png")).convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem("열기", lambda icon, item: self._restore_from_tray(icon), default=True),
                pystray.MenuItem("종료", lambda icon, item: self._quit_from_tray(icon)),
            )
            self._tray_icon = pystray.Icon("교사용계산기", img, "교사용계산기", menu)
            threading.Thread(target=self._tray_icon.run, daemon=True).start()
        except Exception:
            pass

    def _restore_from_tray(self, icon):
        _write_state("visible")
        icon.stop()
        self._tray_icon = None
        self.after(0, self.deiconify)

    def _quit_from_tray(self, icon):
        _remove_state_files()
        icon.stop()
        self._tray_icon = None
        self.after(0, self.destroy)

    def destroy(self):
        _remove_state_files()
        super().destroy()

    # ── 설정 ─────────────────────────────────────────────────────────────────

    def apply_settings(self, s: dict):
        self._settings = s
        self.attributes("-topmost", s["always_on_top"])
        self.attributes("-alpha", s["opacity"] / 100)
        if s["show_korean"]:
            self._kor_frame.grid()
        else:
            self._kor_frame.grid_remove()
        if s["close_to_tray"]:
            self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        else:
            self.protocol("WM_DELETE_WINDOW", self.destroy)
        apply_startup(s["start_with_windows"])

    def _open_settings(self):
        SettingsDialog(self, self._settings)

    # ── 폰트 자동 조절 ────────────────────────────────────────────────────────

    def _resize_fonts(self):
        w = self.winfo_width()
        disp_fs = max(20, min(56, int(w * 0.14)))
        expr_fs = max(9,  int(disp_fs * 0.38))
        btn_fs  = max(10, min(26, int(w * 0.065)))
        kor_fs  = max(9,  min(16, int(w * 0.038)))

        # 창 너비 기준 최대 소수점 자릿수: 260px→2, 660px→10
        self._max_dec = max(2, min(10, int(2 + (w - 260) / 40)))
        self._refresh()

        self._disp_lbl.configure(font=("맑은 고딕", disp_fs, "bold"))
        self._expr_lbl.configure(font=("맑은 고딕", expr_fs))
        self._kor_lbl.configure(font=("맑은 고딕", kor_fs), wraplength=w - 28)
        self._cheon_lbl.configure(font=("맑은 고딕", kor_fs))
        self._gear_btn.configure(font=("맑은 고딕", max(10, int(w * 0.03))))
        for btn in self._buttons.values():
            btn.configure(font=("맑은 고딕", btn_fs, "bold"))

    # ── 키보드 ───────────────────────────────────────────────────────────────

    def _bind_keys(self):
        self.bind("<Key>", self._on_key)

    def _on_key(self, event):
        ch, sym = event.char, event.keysym
        if ch in "0123456789":          self._press(ch)
        elif ch == ".":                  self._press(".")
        elif ch == "+":                  self._press("+")
        elif ch in ("-", "−"):           self._press("−")
        elif ch == "*":                  self._press("×")
        elif ch == "/":                  self._press("÷")
        elif ch == "%":                  self._press("%")
        elif sym in ("Return","KP_Enter"): self._press("=")
        elif sym == "BackSpace":          self._backspace()
        elif sym == "Escape":             self._press("C")

    # ── 계산 ─────────────────────────────────────────────────────────────────

    def _press(self, label: str):
        if   label == "C":                  self._clear_all()
        elif label in ("÷","×","+","−"):    self._set_op(label)
        elif label == "=":                   self._evaluate()
        elif label == "±":                   self._negate()
        elif label == "%":                   self._percent()
        elif label == ".":                   self._dot()
        elif label == "한글":               self._convert_korean()
        elif label == "천원":               self._convert_cheon()
        else:                                self._digit(label)

    def _clear_all(self):
        self._entry = "0"
        self._num1  = None
        self._op    = None
        self._just_evaluated = False
        self._expr_var.set("")
        self._disp_var.set("0")
        self._kor_var.set("계산 결과가 여기 표시됩니다")
        self._cheon_var.set("")

    def _digit(self, d: str):
        if self._just_evaluated:
            self._entry, self._just_evaluated = d, False
        elif self._entry == "0":
            self._entry = d
        else:
            self._entry += d
        self._refresh()

    def _dot(self):
        if self._just_evaluated:
            self._entry, self._just_evaluated = "0.", False
        elif "." not in self._entry:
            self._entry += "."
        self._refresh()

    def _set_op(self, op: str):
        self._num1 = self._parse(self._entry)
        self._op   = op
        self._just_evaluated = False
        self._expr_var.set(f"{self._fmt(self._num1)} {op}")
        self._entry = "0"

    def _evaluate(self):
        if self._num1 is None or self._op is None:
            return
        a, b = self._num1, self._parse(self._entry)
        try:
            if   self._op == "+": res = a + b
            elif self._op == "−": res = a - b
            elif self._op == "×": res = a * b
            elif self._op == "÷":
                if b == 0:
                    self._disp_var.set("오류: 0으로 나눌 수 없음")
                    return
                res = a / b
            else:
                return
        except Exception:
            self._disp_var.set("오류")
            return

        self._expr_var.set(f"{self._fmt(a)} {self._op} {self._fmt(b)} =")
        self._entry = self._f2s(res)
        self._num1 = None
        self._op   = None
        self._just_evaluated = True
        self._refresh()
        self._update_conversions(res)

    def _negate(self):
        self._entry = self._f2s(self._parse(self._entry) * -1)
        self._refresh()

    def _percent(self):
        self._entry = self._f2s(self._parse(self._entry) / 100)
        self._refresh()

    def _backspace(self):
        if not self._just_evaluated:
            self._entry = self._entry[:-1] or "0"
            self._refresh()

    # ── 변환 ─────────────────────────────────────────────────────────────────

    def _update_conversions(self, val: float):
        if not self._settings.get("show_korean", True):
            return
        try:
            self._kor_var.set(f"{to_korean_amount(int(round(val)))}  ← 클릭하여 복사")
            self._cheon_var.set(f"{to_cheonwon(val)}  ← 클릭하여 복사")
        except Exception:
            self._kor_var.set("")
            self._cheon_var.set("")

    def _convert_korean(self):
        try:
            val = self._parse(self._entry)
            kor   = to_korean_amount(int(round(val)))
            cheon = to_cheonwon(val)
            self._kor_var.set(f"{kor}  ← 클릭하여 복사")
            self._cheon_var.set(f"{cheon}  ← 클릭하여 복사")
            self._copy(kor)
        except Exception:
            pass

    def _convert_cheon(self):
        try:
            val = self._parse(self._entry)
            kor   = to_korean_amount(int(round(val)))
            cheon = to_cheonwon(val)
            self._kor_var.set(f"{kor}  ← 클릭하여 복사")
            self._cheon_var.set(f"{cheon}  ← 클릭하여 복사")
            self._copy(cheon)
        except Exception:
            pass

    def _copy(self, text: str):
        text = text.replace("  ← 클릭하여 복사", "").strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        orig_k = self._kor_lbl.cget("bg")
        orig_c = self._cheon_lbl.cget("bg")
        self._kor_lbl.configure(bg="#27ae60")
        self._cheon_lbl.configure(bg="#27ae60")
        self.after(350, lambda: (
            self._kor_lbl.configure(bg=orig_k),
            self._cheon_lbl.configure(bg=orig_c),
        ))

    # ── 포맷 유틸 ────────────────────────────────────────────────────────────

    def _refresh(self):
        self._disp_var.set(self._fmt_str(self._entry))

    def _parse(self, s: str) -> float:
        try:
            return float(s)
        except Exception:
            return 0.0

    def _f2s(self, val: float) -> str:
        if val == int(val):
            return str(int(val))
        return f"{val:.10f}".rstrip("0")

    def _fmt(self, val: float) -> str:
        return self._fmt_str(self._f2s(val))

    def _fmt_str(self, s: str) -> str:
        try:
            neg = s.startswith("-")
            s2 = s.lstrip("-")
            if s2.endswith("."):
                core = f"{int(s2[:-1]):,}."
            elif "." in s2:
                i, d = s2.split(".", 1)
                i_fmt = f"{int(i):,}"
                d = d.ljust(2, "0")  # 최소 2자리 보장
                max_dec = getattr(self, "_max_dec", 10)
                if len(d) > max_dec:
                    core = f"{i_fmt}.{d[:max_dec]}..."
                else:
                    core = f"{i_fmt}.{d}"
            else:
                core = f"{int(s2):,}"
            return ("-" + core) if neg else core
        except Exception:
            return s


# ─── NumLock 감지 ────────────────────────────────────────────────────────────

def _watch_numlock():
    last = 0.0

    def on_key(event):
        nonlocal last
        if event.name == "num lock" and event.event_type == keyboard.KEY_DOWN:
            now = time.time()
            if now - last <= 1.0:
                _launch_new_instance()
                last = 0.0
            else:
                last = now

    keyboard.hook(on_key)
    keyboard.wait()


def _spawn_new():
    if getattr(sys, "frozen", False):
        subprocess.Popen([EXE_PATH])
    else:
        subprocess.Popen([sys.executable, os.path.abspath(__file__)])


def _launch_new_instance():
    # 여러 인스턴스가 동시에 NumLock을 감지해 중복 스폰하는 것을 방지
    now = time.time()
    try:
        if os.path.exists(SPAWN_LOCK):
            with open(SPAWN_LOCK) as f:
                t = float(f.read().strip())
            if now - t < SPAWN_COOLDOWN:
                return
    except Exception:
        pass

    tray_pids, visible_pids = _get_tray_and_visible_pids()
    if visible_pids:
        try:
            with open(SPAWN_LOCK, "w") as f:
                f.write(str(now))
        except Exception:
            pass
        _spawn_new()
    elif tray_pids:
        # 트레이에만 있으면 첫 번째 것을 복원 요청
        try:
            with open(_restore_file(tray_pids[0]), "w") as f:
                f.write("restore")
        except Exception:
            pass
    else:
        try:
            with open(SPAWN_LOCK, "w") as f:
                f.write(str(now))
        except Exception:
            pass
        _spawn_new()


# ─── 진입점 ──────────────────────────────────────────────────────────────────

def main():
    app = Calculator()
    threading.Thread(target=_watch_numlock, daemon=True).start()
    app.mainloop()


if __name__ == "__main__":
    main()
