"""UniversalSpeed GUI — 点开关就能用的游戏倍速界面。

双击 UniversalSpeed.exe 直接进界面；带参数启动则走命令行（list/status/set/pin/diag）。
"""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import UniversalSpeed as core

BG = '#1e1f22'
CARD = '#2b2d31'
FG = '#e8e8ea'
DIM = '#9aa0a6'
ACCENT = '#3ba55d'
ACCENT_DIM = '#2d7a45'
BTN = '#3a3d43'
BTN_HOVER = '#474b52'
WARN = '#d9a441'
ERR = '#d9534f'

PRESETS = [2, 3, 5, 10, 20]


def base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def attach_console():
    """窗 exe 从命令行带参数启动时，把输出接回调用者的控制台。"""
    try:
        if ctypes.windll.kernel32.AttachConsole(0xFFFFFFFF):
            sys.stdout = open('CONOUT$', 'w', encoding='mbcs', errors='replace', buffering=1)
            sys.stderr = sys.stdout
            return True
    except Exception:
        pass
    return False


def ensure_output():
    """已经有可用的 stdout（控制台版/被重定向）就不动它；窗口版 exe 才去接管父控制台。"""
    if sys.stdout is not None:
        return True
    return attach_console()


class App:
    def __init__(self, root, auto=False, speed=10.0):
        self.root = root
        self.on = False
        self.speed = float(speed)
        self.restore_pending = False
        self.fixes = 0
        self.q = queue.Queue()
        self.rt = None
        self.game = None
        self.last_err = ''
        self._stop = threading.Event()

        root.title('游戏倍速  UniversalSpeed')
        root.configure(bg=BG)
        root.geometry('470x330')
        root.resizable(False, False)
        try:
            root.iconbitmap(default='')
        except Exception:
            pass

        self.f_big = tkfont.Font(family='Microsoft YaHei UI', size=15, weight='bold')
        self.f = tkfont.Font(family='Microsoft YaHei UI', size=10)
        self.f_small = tkfont.Font(family='Microsoft YaHei UI', size=9)

        pad = 16
        top = tk.Frame(root, bg=BG)
        top.pack(fill='x', padx=pad, pady=(14, 6))
        self.lbl_game = tk.Label(top, text='正在查找游戏…', bg=BG, fg=DIM, font=self.f_small,
                                 anchor='w')
        self.lbl_game.pack(side='left', fill='x', expand=True)
        self.btn_refresh = tk.Button(top, text='重新查找', command=self.refresh, bg=BTN, fg=FG,
                                     font=self.f_small, relief='flat', bd=0, padx=8, pady=3,
                                     activebackground=BTN_HOVER, activeforeground=FG, cursor='hand2')
        self.btn_refresh.pack(side='right')

        self.btn_toggle = tk.Button(root, text='加速：关', command=self.toggle, bg=BTN, fg=FG,
                                    font=self.f_big, relief='flat', bd=0, pady=14,
                                    activebackground=BTN_HOVER, activeforeground=FG, cursor='hand2')
        self.btn_toggle.pack(fill='x', padx=pad, pady=(4, 12))

        row = tk.Frame(root, bg=BG)
        row.pack(fill='x', padx=pad)
        tk.Label(row, text='倍率', bg=BG, fg=DIM, font=self.f_small).pack(side='left', padx=(0, 8))
        self.preset_btns = {}
        for p in PRESETS:
            b = tk.Button(row, text='%d×' % p, bg=BTN, fg=FG, font=self.f, relief='flat', bd=0,
                          padx=10, pady=4, activebackground=BTN_HOVER, activeforeground=FG,
                          cursor='hand2', command=lambda v=p: self.set_speed(v))
            b.pack(side='left', padx=2)
            self.preset_btns[p] = b
        self.ent = tk.Entry(row, width=5, bg=CARD, fg=FG, font=self.f, relief='flat',
                            insertbackground=FG, justify='center')
        self.ent.pack(side='left', padx=(8, 4))
        self.ent.insert(0, '10')
        self.ent.bind('<Return>', lambda e: self.apply_entry())
        tk.Button(row, text='自定义', bg=BTN, fg=FG, font=self.f_small, relief='flat', bd=0,
                  padx=8, pady=4, activebackground=BTN_HOVER, activeforeground=FG,
                  cursor='hand2', command=self.apply_entry).pack(side='left')

        self.lbl_status = tk.Label(root, text='状态：等待中', bg=BG, fg=DIM, font=self.f, anchor='w')
        self.lbl_status.pack(fill='x', padx=pad, pady=(14, 2))
        self.lbl_live = tk.Label(root, text='游戏内 timeScale：—', bg=BG, fg=DIM, font=self.f_small,
                                 anchor='w')
        self.lbl_live.pack(fill='x', padx=pad)

        bottom = tk.Frame(root, bg=BG)
        bottom.pack(fill='x', padx=pad, pady=(10, 4))
        tk.Button(bottom, text='恢复 1×', bg=BTN, fg=FG, font=self.f_small, relief='flat', bd=0,
                  padx=10, pady=4, activebackground=BTN_HOVER, activeforeground=FG,
                  cursor='hand2', command=lambda: self.set_speed(1.0)).pack(side='left')
        self.var_auto = tk.BooleanVar(value=False)
        self.chk = tk.Checkbutton(bottom, text='开机自启（进入游戏自动加速）', variable=self.var_auto,
                                  command=self.toggle_autostart, bg=BG, fg=DIM, font=self.f_small,
                                  selectcolor=CARD, activebackground=BG, activeforeground=FG,
                                  highlightthickness=0, bd=0, cursor='hand2')
        self.chk.pack(side='right')
        self.var_auto.set(self.autostart_enabled())

        self.set_speed(self.speed, toggle_on=False)
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()
        self.root.after(120, self.pump)
        if auto:
            self.speed = float(speed)
            self.set_speed(speed)
            self.toggle(on=True)

    # ---------- 界面动作 ----------
    def toggle(self, on=None):
        want = (not self.on) if on is None else bool(on)
        self.on = want
        if want:
            self.btn_toggle.configure(text='加速：开 · %g×' % self.speed, bg=ACCENT, fg='white',
                                      activebackground=ACCENT_DIM, activeforeground='white')
            self.fixes = 0
            self.restore_pending = False
        else:
            self.btn_toggle.configure(text='加速：关', bg=BTN, fg=FG,
                                      activebackground=BTN_HOVER, activeforeground=FG)
            self.restore_pending = True

    def set_speed(self, v, toggle_on=None):
        try:
            v = float(v)
        except Exception:
            return
        if v <= 0 or v > 100:
            self.lbl_status.configure(text='倍率要在 0.1 ~ 100 之间', fg=WARN)
            return
        self.speed = v
        self.ent.delete(0, 'end')
        self.ent.insert(0, ('%g' % v))
        for p, b in self.preset_btns.items():
            b.configure(bg=ACCENT if abs(p - v) < 1e-6 else BTN,
                        fg='white' if abs(p - v) < 1e-6 else FG)
        if self.on:
            self.btn_toggle.configure(text='加速：开 · %g×' % v)

    def apply_entry(self):
        try:
            self.set_speed(float(self.ent.get().strip().lower().replace('x', '')))
        except Exception:
            self.lbl_status.configure(text='倍率填数字，例如 10', fg=WARN)

    def refresh(self):
        self.rt = None

    # ---------- 开机自启 ----------
    def startup_path(self):
        appdata = os.environ.get('APPDATA', '')
        return os.path.join(appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup',
                            'UniversalSpeed.vbs')

    def autostart_enabled(self):
        return os.path.exists(self.startup_path())

    def toggle_autostart(self):
        path = self.startup_path()
        if self.var_auto.get():
            exe = sys.executable if getattr(sys, 'frozen', False) else os.path.join(base_dir(), 'UniversalSpeed.exe')
            lines = ["' UniversalSpeed 开机自动加速（无窗口）",
                     'Set sh = CreateObject("WScript.Shell")',
                     'sh.Run """" & "%s" & """ --auto --speed %g", 0, False' % (exe, self.speed)]
            with open(path, 'w', encoding='utf-16-le') as fh:
                fh.write('\r\n'.join(lines) + '\r\n')
            self.lbl_status.configure(text='已设置开机自启', fg=DIM)
        else:
            try:
                os.remove(path)
            except Exception:
                pass
            self.lbl_status.configure(text='已取消开机自启', fg=DIM)

    # ---------- 后台线程 ----------
    def worker(self):
        while not self._stop.is_set():
            try:
                games = core.find_games(include_hidden=False)
            except Exception as exc:
                self.q.put(('err', '枚举进程失败：%s' % exc))
                time.sleep(2)
                continue
            if not games:
                self.q.put(('game', None))
                self.rt = None
                time.sleep(1.0)
                continue
            g = games[0]
            if self.rt is None or (self.game and self.game['pid'] != g['pid']):
                try:
                    t = core.Target(g['pid'])
                    self.rt = core.Runtime(t, g['assembly'])
                    self.game = g
                    self.q.put(('game', g))
                except Exception as exc:
                    self.q.put(('err', str(exc)))
                    self.rt = None
                    time.sleep(1.5)
                    continue
            try:
                cur = self.rt.get()
            except Exception:
                self.rt = None
                self.q.put(('err', '读取失败，游戏可能已退出'))
                time.sleep(1.0)
                continue
            self.q.put(('live', cur))
            if self.on and abs(cur - self.speed) > 1e-3:
                try:
                    self.rt.set(self.speed)
                    self.fixes += 1
                    self.q.put(('fix', self.fixes))
                except Exception:
                    self.rt = None
            elif (not self.on) and self.restore_pending:
                try:
                    if abs(cur - 1.0) > 1e-3:
                        self.rt.set(1.0)
                    self.restore_pending = False
                    self.fixes = 0
                except Exception:
                    self.rt = None
            time.sleep(0.25)

    # ---------- UI 轮询 ----------
    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == 'game':
                    if val:
                        self.lbl_game.configure(text='游戏：%s   #%d' % (val['name'], val['pid']), fg=FG)
                    else:
                        self.lbl_game.configure(text='没找到 Unity 游戏，打开游戏后会自己接上', fg=WARN)
                        if self.on:
                            self.lbl_status.configure(text='状态：游戏不在，等待中…', fg=WARN)
                elif kind == 'live':
                    self.lbl_live.configure(text='游戏内 timeScale：%.3f' % val, fg=FG)
                    if self.on:
                        self.lbl_status.configure(
                            text='状态：加速中  目标 %g×   已修正 %d 次' % (self.speed, self.fixes), fg=ACCENT)
                    else:
                        self.lbl_status.configure(text='状态：未加速', fg=DIM)
                elif kind == 'err':
                    self.last_err = val
                    self.lbl_status.configure(text='状态：' + val, fg=WARN)
        except queue.Empty:
            pass
        self.root.after(120, self.pump)


def gui_main(auto=False, speed=10.0):
    root = tk.Tk()
    App(root, auto=auto, speed=speed)
    root.mainloop()


def main(argv):
    gui_flags = [a for a in argv if a in ('--gui', 'gui', '--auto')]
    speed = 10.0
    if '--speed' in argv:
        gui_flags.append('--speed')
        try:
            speed = float(argv[argv.index('--speed') + 1])
        except Exception:
            speed = 10.0
    if gui_flags:
        return gui_main(auto=('--auto' in gui_flags), speed=speed)
    if argv:
        if not ensure_output():
            log = os.path.join(base_dir(), 'UniversalSpeed.log')
            try:
                sys.stdout = open(log, 'w', encoding='utf-8', buffering=1)
                sys.stderr = sys.stdout
            except Exception:
                pass
        return core.main(argv)
    return gui_main()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]) or 0)
