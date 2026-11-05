"""UniversalSpeed - 通用 Unity IL2CPP 游戏倍速工具（Windows x64）

原理：不注入 DLL、不改游戏文件，只用 IL2CPP 运行时的官方 API
（il2cpp_domain_get / il2cpp_domain_assembly_open / il2cpp_class_from_name /
 il2cpp_class_get_method_from_name）在当前进程里调用
UnityEngine.Time.set_timeScale / get_timeScale。

用法：
  UniversalSpeed.exe list                列出正在运行的 Unity 游戏
  UniversalSpeed.exe status              查看当前倍速（自动挑一个游戏）
  UniversalSpeed.exe set 10              一次性设为 10 倍
  UniversalSpeed.exe pin 10              常驻守护，被游戏改回时自动重新钉住
  UniversalSpeed.exe pin 10 --pid 1234   指定进程
  UniversalSpeed.exe pin 10 --game "legend"   按进程名/窗口标题模糊匹配
  UniversalSpeed.exe list --all          连没有窗口的 Unity 进程也列出来

注意：需要对目标进程有 PROCESS_VM_READ/WRITE 权限；游戏以管理员运行时，
本工具也要以管理员运行。带内核级反作弊的联机游戏会拦截，这里不做绕过。
"""
import argparse
import ctypes
import ctypes.wintypes as w
import json
import os
import struct
import sys
import time

try:  # 输出重定向到文件时也按行刷新，方便看 pin 日志
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
u32 = ctypes.WinDLL('user32', use_last_error=True)

PROCESS_ALL = 0x1F0FFF
PROCESS_QUERY_LIMITED = 0x1000
MEM_COMMIT, MEM_RESERVE, MEM_RELEASE = 0x1000, 0x2000, 0x8000
PAGE_EXECUTE_READWRITE = 0x40
PAGE_READWRITE = 0x04
TH32CS_SNAPMODULE = 0x08
TH32CS_SNAPPROCESS = 0x02

k32.OpenProcess.restype = w.HANDLE
k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD, w.DWORD]
k32.VirtualFreeEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD]
k32.WriteProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.CreateRemoteThread.restype = w.HANDLE
k32.CreateRemoteThread.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                   ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k32.GetExitCodeThread.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
k32.CloseHandle.argtypes = [w.HANDLE]
k32.CreateToolhelp32Snapshot.restype = w.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
k32.Module32First.argtypes = [w.HANDLE, ctypes.c_void_p]
k32.Module32Next.argtypes = [w.HANDLE, ctypes.c_void_p]
k32.Process32First.argtypes = [w.HANDLE, ctypes.c_void_p]
k32.Process32Next.argtypes = [w.HANDLE, ctypes.c_void_p]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', w.DWORD), ('th32ModuleID', w.DWORD), ('th32ProcessID', w.DWORD),
                ('GlblcntUsage', w.DWORD), ('ProccntUsage', w.DWORD), ('modBaseAddr', ctypes.c_void_p),
                ('modBaseSize', w.DWORD), ('hModule', w.HMODULE), ('szModule', w.CHAR * 256),
                ('szExePath', w.CHAR * 260)]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', w.DWORD),
                ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', w.DWORD), ('szExeFile', ctypes.c_char * 260)]


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long),
                ('bottom', ctypes.c_long)]


def modules(pid):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | 0x10, pid)
    out = {}
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    me = MODULEENTRY32()
    me.dwSize = ctypes.sizeof(MODULEENTRY32)
    ok = k32.Module32First(snap, ctypes.byref(me))
    while ok:
        out[me.szModule.decode('mbcs', 'ignore').lower()] = (
            me.modBaseAddr or 0, me.modBaseSize, me.szExePath.decode('mbcs', 'ignore'))
        ok = k32.Module32Next(snap, ctypes.byref(me))
    k32.CloseHandle(snap)
    return out


def all_pids():
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out = []
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    ok = k32.Process32First(snap, ctypes.byref(pe))
    while ok:
        out.append((pe.th32ProcessID, pe.szExeFile.decode('mbcs', 'ignore')))
        ok = k32.Process32Next(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    return out


def window_title(pid):
    EnumProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    found = []

    def cb(hwnd, _):
        p = w.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            n = u32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                found.append(buf.value)
        return True

    u32.EnumWindows(EnumProc(cb), 0)
    return found[0] if found else ''


def find_games(include_hidden=False):
    """返回 [{'pid','name','title','assembly'} ...]，只保留加载了 GameAssembly.dll 的进程。"""
    res = []
    for pid, name in all_pids():
        if pid <= 4:
            continue
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if not h:
            continue
        k32.CloseHandle(h)
        mods = modules(pid)
        ga = mods.get('gameassembly.dll')
        if not ga:
            continue
        title = window_title(pid)
        if not title and not include_hidden:
            continue
        res.append({'pid': pid, 'name': name, 'title': title, 'assembly': ga[2],
                    'mono': 'mono-2.0-bindings.dll' in mods})
    return res


def parse_exports(path):
    d = open(path, 'rb').read()
    e = struct.unpack_from('<I', d, 0x3C)[0]
    magic = struct.unpack_from('<H', d, e + 24)[0]
    nsec = struct.unpack_from('<H', d, e + 6)[0]
    sizeopt = struct.unpack_from('<H', d, e + 20)[0]
    opt = e + 24
    dd = opt + (112 if magic == 0x20b else 96)
    exp_rva = struct.unpack_from('<I', d, dd)[0]
    secs = []
    so = opt + sizeopt
    for i in range(nsec):
        s = so + i * 40
        vs, va, rs, rp = struct.unpack_from('<IIII', d, s + 8)
        secs.append((va, vs, rp, rs))

    def r2o(rva):
        for va, vs, rp, rs in secs:
            if va <= rva < va + vs:
                return rp + (rva - va)
        return None

    off = r2o(exp_rva)
    if off is None:
        return {}
    nname = struct.unpack_from('<I', d, off + 24)[0]
    funcs_rva = struct.unpack_from('<I', d, off + 28)[0]
    names_rva = struct.unpack_from('<I', d, off + 32)[0]
    ords_rva = struct.unpack_from('<I', d, off + 36)[0]
    o_names, o_funcs, o_ords = r2o(names_rva), r2o(funcs_rva), r2o(ords_rva)
    out = {}
    for i in range(nname):
        try:
            o = r2o(struct.unpack_from('<I', d, o_names + i * 4)[0])
            if o is None:
                continue
            name = d[o:d.index(b'\0', o)].decode('ascii', 'ignore')
            if not name:
                continue
            ordi = struct.unpack_from('<H', d, o_ords + i * 2)[0]
            out[name] = struct.unpack_from('<I', d, o_funcs + ordi * 4)[0]
        except Exception:
            continue
    return out


MOV_RCX = lambda v: bytes([0x48, 0xB9]) + struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF)
MOV_RDX = lambda v: bytes([0x48, 0xBA]) + struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF)
MOV_R8 = lambda v: bytes([0x49, 0xB8]) + struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF)
MOV_RAX = lambda v: bytes([0x48, 0xB8]) + struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF)
CALL_RAX = bytes([0xFF, 0xD0])
XOR_EAX = bytes([0x31, 0xC0])
RET = bytes([0xC3])
SUB_RSP = lambda n: bytes([0x48, 0x83, 0xEC, n])
ADD_RSP = lambda n: bytes([0x48, 0x83, 0xC4, n])
STORE_RAX = lambda slot: bytes([0x48, 0xA3]) + struct.pack('<Q', slot)
MOVQ_RAX_XMM0 = bytes([0x66, 0x48, 0x0F, 0x7E, 0xC0])
MOVD_XMM0_EAX = bytes([0x66, 0x0F, 0x6E, 0xC0])


def f32(raw):
    return struct.unpack('<f', struct.pack('<I', raw & 0xFFFFFFFF))[0]


class Target:
    def __init__(self, pid):
        self.pid = pid
        self.h = k32.OpenProcess(PROCESS_ALL, False, pid)
        if not self.h:
            err = ctypes.get_last_error()
            raise RuntimeError('OpenProcess(%d) 失败 err=%d（试试以管理员身份运行）' % (pid, err))
        self.mods = modules(pid)

    def base(self, name):
        return self.mods[name.lower()][0]

    def alloc(self, size, prot=PAGE_EXECUTE_READWRITE):
        return k32.VirtualAllocEx(self.h, None, size, MEM_COMMIT | MEM_RESERVE, prot)

    def free(self, addr):
        k32.VirtualFreeEx(self.h, ctypes.c_void_p(addr), 0, MEM_RELEASE)

    def write(self, addr, data):
        n = ctypes.c_size_t(0)
        ok = k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), data, len(data), ctypes.byref(n))
        return bool(ok) and n.value == len(data)

    def write_u64(self, addr, v):
        return self.write(addr, struct.pack('<Q', v))

    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t(0)
        k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
        return buf.raw[:n.value]

    def read_u64(self, addr):
        b = self.read(addr, 8)
        return struct.unpack('<Q', b)[0] if len(b) == 8 else 0

    def read_f32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack('<f', b)[0] if len(b) == 4 else None

    def alloc_string(self, s):
        data = s.encode('ascii') + b'\0'
        p = self.alloc(len(data), PAGE_READWRITE)
        self.write(p, data)
        return p

    def exec_shellcode(self, code, timeout=8000):
        buf = self.alloc(len(code))
        self.write(buf, code)
        tid = w.DWORD(0)
        th = k32.CreateRemoteThread(self.h, None, 0, ctypes.c_void_p(buf), None, 0, ctypes.byref(tid))
        if not th:
            self.free(buf)
            return None
        k32.WaitForSingleObject(th, timeout)
        rc = w.DWORD(0)
        k32.GetExitCodeThread(th, ctypes.byref(rc))
        k32.CloseHandle(th)
        self.free(buf)
        return rc.value

    def call(self, fn, args=(), timeout=8000):
        slot = self.alloc(8, PAGE_READWRITE)
        self.write_u64(slot, 0)
        code = b''
        if len(args) > 0:
            code += MOV_RCX(args[0])
        if len(args) > 1:
            code += MOV_RDX(args[1])
        if len(args) > 2:
            code += MOV_R8(args[2])
        code += MOV_RAX(fn) + SUB_RSP(40) + CALL_RAX + ADD_RSP(40)
        code += STORE_RAX(slot) + XOR_EAX + RET
        self.exec_shellcode(code, timeout)
        val = self.read_u64(slot)
        self.free(slot)
        return val

    def call_method(self, minfo, mptr, factor=None, timeout=8000):
        slot = self.alloc(8, PAGE_READWRITE)
        self.write_u64(slot, 0)
        code = MOV_RCX(minfo)
        if factor is not None:
            code += bytes([0xB8]) + struct.pack('<f', factor) + MOVD_XMM0_EAX
        code += MOV_RAX(mptr) + SUB_RSP(40) + CALL_RAX + ADD_RSP(40)
        code += MOVQ_RAX_XMM0 + STORE_RAX(slot) + XOR_EAX + RET
        self.exec_shellcode(code, timeout)
        raw = self.read_u64(slot)
        self.free(slot)
        return raw


class Runtime:
    """绑定到一个 IL2CPP 进程的 Time 静态类。"""

    def __init__(self, t, assembly_path):
        self.t = t
        self.exp = parse_exports(assembly_path)
        need = ['il2cpp_domain_get', 'il2cpp_domain_assembly_open', 'il2cpp_assembly_get_image',
                'il2cpp_class_from_name', 'il2cpp_class_get_method_from_name']
        missing = [n for n in need if n not in self.exp]
        if missing:
            raise RuntimeError('GameAssembly.dll 里缺少导出：%s（可能不是 IL2CPP 游戏）' % ', '.join(missing))
        self.gb = t.base('GameAssembly.dll')
        self.f = lambda n: self.gb + self.exp[n]
        self.domain = t.call(self.f('il2cpp_domain_get'))
        if not self.domain:
            raise RuntimeError('il2cpp_domain_get 返回空（游戏可能还没初始化完成）')
        asm = t.call(self.f('il2cpp_domain_assembly_open'),
                     (self.domain, t.alloc_string('UnityEngine.CoreModule.dll')))
        if not asm:
            raise RuntimeError('找不到 UnityEngine.CoreModule 程序集')
        img = t.call(self.f('il2cpp_assembly_get_image'), (asm,))
        klass = t.call(self.f('il2cpp_class_from_name'),
                       (img, t.alloc_string('UnityEngine'), t.alloc_string('Time')))
        if not klass:
            raise RuntimeError('找不到 UnityEngine.Time 类')
        self.set_mi, self.set_ptr = self._method(klass, 'set_timeScale', 1)
        self.get_mi, self.get_ptr = self._method(klass, 'get_timeScale', 0)

    def _method(self, klass, name, argc):
        mi = self.t.call(self.f('il2cpp_class_get_method_from_name'),
                         (klass, self.t.alloc_string(name), argc))
        if not mi:
            raise RuntimeError('方法 %s 不存在' % name)
        return mi, self.t.read_u64(mi)

    def get(self):
        return f32(self.t.call_method(self.get_mi, self.get_ptr))

    def set(self, value):
        self.t.call_method(self.set_mi, self.set_ptr, factor=float(value))
        return self.get()


def pick(pid=None, name=None, include_hidden=True):
    games = find_games(include_hidden=include_hidden)
    if pid:
        return [g for g in games if g['pid'] == pid]
    if name:
        low = name.lower()
        return [g for g in games if low in g['name'].lower() or low in (g['title'] or '').lower()]
    return games


def summarize(g):
    return '#%d  %s  [%s]' % (g['pid'], g['name'], g['title'] or '<无窗口>')


def cmd_list(args):
    games = find_games(include_hidden=args.all)
    if not games:
        print('没有发现 Unity IL2CPP 游戏进程（加 --all 可列出无窗口的）')
        return 1
    for g in games:
        tag = ' [MONO 不支持]' if g['mono'] else ''
        print(summarize(g) + tag)
        print('      %s' % g['assembly'])
    return 0


def open_target(args, quiet=False):
    cands = pick(pid=args.pid, name=args.game)
    if not cands:
        print('找不到目标。先运行 `UniversalSpeed list` 看看有哪些。')
        return None
    if len(cands) > 1 and not quiet:
        print('发现多个候选，用第一个；需要指定就用 --pid 或 --game：')
        for g in cands:
            print('   ' + summarize(g))
    g = cands[0]
    if g['mono']:
        print('目标是 Mono 版 Unity，本工具只支持 IL2CPP。')
        return None
    t = Target(g['pid'])
    rt = Runtime(t, g['assembly'])
    return g, t, rt


def cmd_status(args):
    got = open_target(args)
    if not got:
        return 1
    g, t, rt = got
    print('%s  timeScale = %.4f' % (summarize(g), rt.get()))
    return 0


def cmd_set(args):
    got = open_target(args)
    if not got:
        return 1
    g, t, rt = got
    before = rt.get()
    after = rt.set(args.speed)
    print('%s  %.4f -> %.4f' % (summarize(g), before, after))
    return 0


def cmd_pin(args):
    got = open_target(args)
    if not got:
        return 1
    g, t, rt = got
    target = float(args.speed)
    print('%s  目标 %.3f×，Ctrl+C 退出' % (summarize(g), target))
    fixes = 0
    try:
        while True:
            try:
                cur = rt.get()
            except Exception as exc:
                print('读取失败（游戏可能已退出）：%s' % exc)
                return 2
            if abs(cur - target) > 1e-4:
                rt.set(target)
                fixes += 1
                print('[%s] %.3f -> %.3f  (第 %d 次)' % (time.strftime('%H:%M:%S'), cur, target, fixes))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('已停止')


def cmd_diag(args):
    pids = all_pids()
    print('进程枚举: %d 个' % len(pids))
    print('exe 路径: %s' % sys.executable)
    print('frozen: %s' % getattr(sys, 'frozen', False))
    ga = []
    for pid, name in pids:
        try:
            m = modules(pid)
        except Exception:
            continue
        if 'gameassembly.dll' in m:
            ga.append((pid, name, window_title(pid)))
    print('加载 GameAssembly.dll 的进程: %d 个' % len(ga))
    for item in ga:
        print('   #%d %s [%s]' % item)
    return 0


def main(argv=None):
    if os.name != 'nt':
        print('只支持 Windows。')
        return 1
    if struct.calcsize('P') != 8:
        print('需要 64 位 Python / 64 位系统。')
        return 1
    ap = argparse.ArgumentParser(prog='UniversalSpeed', description='通用 Unity IL2CPP 游戏倍速工具')
    sub = ap.add_subparsers(dest='cmd')

    p_list = sub.add_parser('list', help='列出可加速的 Unity 游戏')
    p_list.add_argument('--all', action='store_true', help='包含没有窗口的 Unity 进程')
    sub.add_parser('diag', help='诊断：打印进程枚举与模块探测结果')

    for nm, fn, hlp in (('status', cmd_status, '查看当前倍速'),
                        ('set', cmd_set, '一次性设置倍速'),
                        ('pin', cmd_pin, '常驻守护倍速')):
        p = sub.add_parser(nm, help=hlp)
        p.add_argument('--pid', type=int)
        p.add_argument('--game', type=str, help='进程名或窗口标题的一部分')
        if nm != 'status':
            p.add_argument('speed', type=float, nargs='?', default=10.0 if nm != 'pin' else 10.0,
                           help='倍率，默认 10')
        if nm == 'pin':
            p.add_argument('--interval', type=float, default=0.25, help='轮询间隔秒，默认 0.25')

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 1
    if args.cmd == 'list':
        return cmd_list(args)
    if args.cmd == 'diag':
        return cmd_diag(args)
    return {'status': cmd_status, 'set': cmd_set, 'pin': cmd_pin}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())

