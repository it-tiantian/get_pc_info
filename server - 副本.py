from flask import Flask, request, jsonify, render_template_string, send_from_directory
from flask_cors import CORS
import os
import base64
import json
import argparse
import threading
import hashlib
import time
from datetime import datetime
from collections import deque, defaultdict
import logging
from logging.handlers import RotatingFileHandler
import sys
import re

# 获取程序运行的根目录
if getattr(sys, 'frozen', False):
    # 如果是打包后的exe
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # 如果是源代码运行
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

print(f"程序运行目录: {BASE_DIR}")

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 确保日志目录存在
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# 创建文件处理器
log_file = os.path.join(LOG_DIR, 'server.log')
file_handler = RotatingFileHandler(log_file, maxBytes=10485760, backupCount=5)
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

app = Flask(__name__)
CORS(app)  # 允许跨域请求

class ClientManager:
    """管理客户端信息"""
    def __init__(self):
        self.clients = {}  # client_id -> 客户端信息
        self.lock = threading.Lock()
    
    def register_client(self, client_info):
        """注册或更新客户端"""
        with self.lock:
            client_id = client_info.get('client_id')
            if not client_id:
                client_id = self._generate_client_id(client_info)
                client_info['client_id'] = client_id
            
            client_info['last_seen'] = datetime.now().isoformat()
            self.clients[client_id] = client_info
            
            logger.info(f"客户端注册/更新: {client_id} - {client_info.get('hostname')}")
            return client_id
    
    def update_client_heartbeat(self, client_id):
        """更新客户端心跳"""
        with self.lock:
            if client_id in self.clients:
                self.clients[client_id]['last_seen'] = datetime.now().isoformat()
                return True
            return False
    
    def is_safe_client_id(self, client_id):
        """确保 client_id 仅包含合规的 md5 12位字符，杜绝路径穿越"""
        if not client_id or not isinstance(client_id, str):
            return False
        return bool(re.match(r'^[a-f0-9]{12}$', client_id))

    def get_client(self, client_id):
        """获取客户端信息"""
        with self.lock:
            return self.clients.get(client_id)
    
    def get_all_clients(self):
        """获取所有客户端"""
        with self.lock:
            # 清理长时间未活跃的客户端（超过24小时）
            current_time = datetime.now()
            inactive_clients = []
            for client_id, client in list(self.clients.items()):
                last_seen = datetime.fromisoformat(client['last_seen'])
                if (current_time - last_seen).total_seconds() > 86400:  # 24小时
                    inactive_clients.append(client_id)
            
            for client_id in inactive_clients:
                del self.clients[client_id]
                logger.info(f"清理非活跃客户端: {client_id}")
            
            return list(self.clients.values())
    
    def _generate_client_id(self, client_info):
        """生成客户端ID"""
        unique_str = f"{client_info.get('hostname')}_{client_info.get('mac_address', '')}_{datetime.now().timestamp()}"
        return hashlib.md5(unique_str.encode()).hexdigest()[:12]

class DataStorage:
    def __init__(self, max_entries=1000):
        self.max_entries = max_entries
        self.client_data = defaultdict(lambda: {
            'hardware_logs': deque(maxlen=max_entries),
            'software_logs': deque(maxlen=max_entries),
            'screenshot_logs': deque(maxlen=max_entries)
        })
        self.lock = threading.Lock()
    
    def add_hardware_log(self, client_id, data):
        with self.lock:
            log_entry = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'data': data
            }
            self.client_data[client_id]['hardware_logs'].appendleft(log_entry)
            return len(self.client_data[client_id]['hardware_logs'])
    
    def add_software_log(self, client_id, data):
        with self.lock:
            log_entry = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'data': data
            }
            self.client_data[client_id]['software_logs'].appendleft(log_entry)
            return len(self.client_data[client_id]['software_logs'])
    
    def add_screenshot_log(self, client_id, filename):
        with self.lock:
            log_entry = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'filename': filename,
                'url': f'/screenshots/{filename}'
            }
            self.client_data[client_id]['screenshot_logs'].appendleft(log_entry)
            return len(self.client_data[client_id]['screenshot_logs'])
    
    def get_client_logs(self, client_id):
        with self.lock:
            if client_id not in self.client_data:
                return {
                    'hardware': [],
                    'software': [],
                    'screenshot': []
                }
            data = self.client_data[client_id]
            return {
                'hardware': list(data['hardware_logs']),
                'software': list(data['software_logs']),
                'screenshot': list(data['screenshot_logs'])
            }
    
    def get_all_logs(self):
        """获取所有客户端的汇总日志（用于监控面板）"""
        with self.lock:
            all_hardware = []
            all_software = []
            all_screenshot = []
            
            for client_id, data in self.client_data.items():
                all_hardware.extend(data['hardware_logs'])
                all_software.extend(data['software_logs'])
                all_screenshot.extend(data['screenshot_logs'])
            
            # 按时间排序，最新的在前面
            all_hardware.sort(key=lambda x: x['timestamp'], reverse=True)
            all_software.sort(key=lambda x: x['timestamp'], reverse=True)
            all_screenshot.sort(key=lambda x: x['timestamp'], reverse=True)
            
            # 限制数量
            return {
                'hardware': list(all_hardware)[:self.max_entries],
                'software': list(all_software)[:self.max_entries],
                'screenshot': list(all_screenshot)[:self.max_entries]
            }
    
    def clear_client_logs(self, client_id):
        with self.lock:
            if client_id in self.client_data:
                self.client_data[client_id]['hardware_logs'].clear()
                self.client_data[client_id]['software_logs'].clear()
                self.client_data[client_id]['screenshot_logs'].clear()
                return True
            return False
    
    def clear_all_logs(self):
        with self.lock:
            self.client_data.clear()
            return True

# 全局实例
client_manager = ClientManager()
storage = DataStorage(max_entries=1000)

# HTML模板 - 修复了图片URL路径
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>系统信息监控平台</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            min-height: 100vh;
            padding: 20px;
            color: #333;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: white;
            border-radius: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .header h1 {
            font-size: 2.2em;
            color: #2c3e50;
            margin-bottom: 10px;
        }
        .header .subtitle {
            color: #7f8c8d;
            margin-bottom: 20px;
        }
        .client-list {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .client-card {
            background: white;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            border-left: 5px solid #3498db;
        }
        .client-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 20px rgba(0,0,0,0.15);
        }
        .client-card.active { border-left-color: #2ecc71; }
        .client-card.inactive { border-left-color: #e74c3c; }
        .client-card h3 {
            color: #2c3e50;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .client-id {
            font-size: 0.8em;
            background: #ecf0f1;
            padding: 2px 8px;
            border-radius: 10px;
            color: #7f8c8d;
        }
        .client-info {
            font-size: 0.9em;
            color: #7f8c8d;
            margin: 5px 0;
        }
        .client-info i { margin-right: 8px; }
        .last-seen {
            font-size: 0.85em;
            color: #95a5a6;
            margin-top: 10px;
            text-align: right;
        }
        .dashboard {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .panel {
            background: white;
            border-radius: 15px;
            overflow: hidden;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .panel-header {
            padding: 15px 20px;
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
            color: white;
            font-weight: bold;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .panel-header.hardware { background: linear-gradient(135deg, #2ecc71 0%, #27ae60 100%); }
        .panel-header.software { background: linear-gradient(135deg, #9b59b6 0%, #8e44ad 100%); }
        .panel-header.screenshot { background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); }
        .panel-count {
            background: rgba(255,255,255,0.2);
            padding: 2px 10px;
            border-radius: 15px;
            font-size: 0.9em;
        }
        .panel-content {
            height: 400px;
            overflow-y: auto;
            padding: 15px;
        }
        .log-entry {
            padding: 12px;
            margin-bottom: 10px;
            background: #f8f9fa;
            border-radius: 10px;
            border-left: 4px solid #3498db;
            transition: all 0.3s ease;
        }
        .log-entry:hover {
            background: #edf2f7;
            transform: translateX(3px);
        }
        .log-entry.hardware { border-left-color: #2ecc71; }
        .log-entry.software { border-left-color: #9b59b6; }
        .log-entry.screenshot { border-left-color: #e74c3c; }
        .log-timestamp {
            font-size: 0.8em;
            color: #7f8c8d;
            margin-bottom: 5px;
            display: flex;
            align-items: center;
        }
        .log-timestamp i { margin-right: 5px; }
        .log-data {
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
            white-space: pre-wrap;
            word-break: break-all;
            background: white;
            padding: 8px;
            border-radius: 5px;
            border: 1px solid #e9ecef;
            max-height: 150px;
            overflow-y: auto;
        }
        .screenshot-preview {
            max-width: 100%;
            max-height: 150px;
            border-radius: 8px;
            margin-top: 8px;
            cursor: pointer;
            transition: transform 0.3s ease;
        }
        .screenshot-preview:hover {
            transform: scale(1.02);
        }
        .screenshot-link {
            display: inline-block;
            margin-top: 8px;
            color: #3498db;
            text-decoration: none;
            font-size: 0.9em;
        }
        .screenshot-link:hover {
            text-decoration: underline;
        }
        .controls {
            display: flex;
            justify-content: center;
            gap: 15px;
            margin-top: 30px;
            flex-wrap: wrap;
        }
        .btn {
            padding: 12px 25px;
            border: none;
            border-radius: 25px;
            background: #3498db;
            color: white;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        .btn-refresh { background: #2ecc71; }
        .btn-clear { background: #e74c3c; }
        .btn-auto { background: #9b59b6; }
        .btn-auto.active { background: #e74c3c; }
        .status {
            text-align: center;
            margin-top: 15px;
            color: #7f8c8d;
            font-size: 0.9em;
        }
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .modal-content {
            max-width: 90%;
            max-height: 90%;
            border-radius: 10px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        .modal-close {
            position: absolute;
            top: 20px;
            right: 30px;
            color: white;
            font-size: 40px;
            cursor: pointer;
            background: none;
            border: none;
        }
        @media (max-width: 768px) {
            .dashboard { grid-template-columns: 1fr; }
            .client-list { grid-template-columns: 1fr; }
            .panel-content { height: 300px; }
            .header h1 { font-size: 1.8em; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🖥️ 系统信息监控平台</h1>
            <p class="subtitle">实时监控多台客户端系统的硬件、软件和屏幕截图信息</p>
            <div class="controls">
                <button class="btn btn-refresh" onclick="refreshAll()">
                    🔄 刷新数据
                </button>
                <button class="btn btn-clear" onclick="clearAllData()">
                    🗑️ 清空数据
                </button>
                <button class="btn btn-auto" id="auto-refresh-btn" onclick="toggleAutoRefresh()">
                    🔁 自动刷新
                </button>
            </div>
            <div class="status" id="status">就绪</div>
        </div>

        <!-- 客户端列表 -->
        <div id="clients-section">
            <h2 style="margin-bottom: 15px; color: #2c3e50;">🌐 在线客户端</h2>
            <div class="client-list" id="client-list">
                <div class="client-card">
                    <div class="client-info">正在加载客户端列表...</div>
                </div>
            </div>
        </div>

        <!-- 数据面板 -->
        <div id="dashboard" style="display: none;">
            <h2 style="margin-bottom: 15px; color: #2c3e50;">📊 实时数据监控</h2>
            <div class="dashboard">
                <div class="panel">
                    <div class="panel-header hardware">
                        <span>💻 硬件信息</span>
                        <span class="panel-count" id="hardware-count">0</span>
                    </div>
                    <div class="panel-content" id="hardware-logs">
                        <div class="log-entry">等待数据...</div>
                    </div>
                </div>
                
                <div class="panel">
                    <div class="panel-header software">
                        <span>🔧 软件信息</span>
                        <span class="panel-count" id="software-count">0</span>
                    </div>
                    <div class="panel-content" id="software-logs">
                        <div class="log-entry">等待数据...</div>
                    </div>
                </div>
                
                <div class="panel">
                    <div class="panel-header screenshot">
                        <span>📷 屏幕截图</span>
                        <span class="panel-count" id="screenshot-count">0</span>
                    </div>
                    <div class="panel-content" id="screenshot-logs">
                        <div class="log-entry">等待数据...</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- 图片预览模态框 -->
    <div class="modal" id="imageModal" onclick="closeModal()">
        <button class="modal-close" onclick="closeModal()">&times;</button>
        <img class="modal-content" id="modalImage">
    </div>

    <script>
        let autoRefreshInterval = null;
        let autoRefreshEnabled = false;
        let selectedClientId = null;
        
        // 加载客户端列表
        async function loadClients() {
            try {
                const response = await fetch('/api/clients');
                if (!response.ok) throw new Error(`HTTP错误: ${response.status}`);
                const data = await response.json();
                
                const container = document.getElementById('client-list');
                if (!data.clients || data.clients.length === 0) {
                    container.innerHTML = '<div class="client-card"><div class="client-info">暂无在线客户端</div></div>';
                    document.getElementById('dashboard').style.display = 'none';
                    return;
                }
                
                container.innerHTML = data.clients.map(client => {
                    const lastSeen = new Date(client.last_seen);
                    const now = new Date();
                    const minutesAgo = Math.floor((now - lastSeen) / 60000);
                    const isActive = minutesAgo < 5;
                    
                    return `
                        <div class="client-card ${isActive ? 'active' : 'inactive'}" 
                             onclick="selectClient('${client.client_id}', '${client.hostname}')"
                             style="cursor: pointer;">
                            <h3>
                                <span>${client.hostname || '未知主机'}</span>
                                <span class="client-id">${client.client_id}</span>
                            </h3>
                            <div class="client-info">
                                <i>👤</i> ${client.username || '未知用户'}
                            </div>
                            <div class="client-info">
                                <i>💻</i> ${client.os || '未知系统'}
                            </div>
                            <div class="client-info">
                                <i>📡</i> ${client.ip_address || '未知IP'}
                            </div>
                            <div class="last-seen">
                                最后活跃: ${minutesAgo}分钟前
                            </div>
                        </div>
                    `;
                }).join('');
                
                // 默认选择第一个客户端
                if (!selectedClientId && data.clients.length > 0) {
                    selectClient(data.clients[0].client_id, data.clients[0].hostname);
                }
                
            } catch (error) {
                console.error('加载客户端失败:', error);
                showStatus('加载客户端失败: ' + error.message, 'error');
            }
        }
        
        // 选择客户端
        function selectClient(clientId, hostname) {
            selectedClientId = clientId;
            document.getElementById('dashboard').style.display = 'block';
            document.querySelectorAll('.client-card').forEach(card => {
                card.style.boxShadow = '0 5px 15px rgba(0,0,0,0.1)';
            });
            
            // 高亮选中的客户端
            event.currentTarget.style.boxShadow = '0 0 0 3px #3498db';
            
            // 加载该客户端的数据
            loadClientData(clientId);
            showStatus(`已选择客户端: ${hostname} (${clientId})`, 'success');
        }
        
        // 加载客户端数据
        async function loadClientData(clientId) {
            try {
                const response = await fetch(`/api/logs/${clientId}`);
                if (!response.ok) throw new Error(`HTTP错误: ${response.status}`);
                const data = await response.json();
                
                // 更新计数
                document.getElementById('hardware-count').textContent = data.hardware.length;
                document.getElementById('software-count').textContent = data.software.length;
                document.getElementById('screenshot-count').textContent = data.screenshot.length;
                
                // 渲染数据
                renderLogs('hardware', data.hardware);
                renderLogs('software', data.software);
                renderLogs('screenshot', data.screenshot);
                
            } catch (error) {
                console.error('加载客户端数据失败:', error);
                showStatus('加载数据失败: ' + error.message, 'error');
            }
        }
        
        // 渲染日志
        function renderLogs(type, logs) {
            const container = document.getElementById(`${type}-logs`);
            if (!logs || logs.length === 0) {
                container.innerHTML = '<div class="log-entry">暂无数据</div>';
                return;
            }
            
            container.innerHTML = logs.map(log => {
                if (type === 'screenshot') {
                    return `
                        <div class="log-entry screenshot">
                            <div class="log-timestamp">
                                <i>🕒</i> ${log.timestamp}
                            </div>
                            <div>📄 ${log.filename}</div>
                            <img src="${log.url}" alt="${log.filename}" 
                                 class="screenshot-preview" 
                                 onclick="previewImage('${log.url}')">
                            <a href="${log.url}" target="_blank" class="screenshot-link">
                                查看原图
                            </a>
                        </div>
                    `;
                } else {
                    let dataStr = '';
                    try {
                        if (typeof log.data === 'string') {
                            dataStr = log.data;
                        } else {
                            dataStr = JSON.stringify(log.data, null, 2);
                        }
                    } catch (e) {
                        dataStr = String(log.data);
                    }
                    
                    return `
                        <div class="log-entry ${type}">
                            <div class="log-timestamp">
                                <i>🕒</i> ${log.timestamp}
                            </div>
                            <div class="log-data">${escapeHtml(dataStr)}</div>
                        </div>
                    `;
                }
            }).join('');
        }
        
        // 刷新所有数据
        function refreshAll() {
            loadClients();
            if (selectedClientId) {
                loadClientData(selectedClientId);
            }
            showStatus('数据已刷新', 'success');
        }
        
        // 清空所有数据
        async function clearAllData() {
            if (!confirm('确定要清空所有数据吗？此操作不可恢复！')) {
                return;
            }
            
            try {
                const response = await fetch('/api/logs/clear', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                
                if (!response.ok) throw new Error('清空失败');
                
                const result = await response.json();
                showStatus('所有数据已清空', 'success');
                refreshAll();
                
            } catch (error) {
                console.error('清空数据失败:', error);
                showStatus('清空失败: ' + error.message, 'error');
            }
        }
        
        // 切换自动刷新
        function toggleAutoRefresh() {
            const btn = document.getElementById('auto-refresh-btn');
            
            if (autoRefreshEnabled) {
                clearInterval(autoRefreshInterval);
                btn.textContent = '🔁 自动刷新';
                btn.classList.remove('active');
                autoRefreshEnabled = false;
                showStatus('自动刷新已关闭', 'info');
            } else {
                autoRefreshInterval = setInterval(refreshAll, 5000); // 5秒刷新
                btn.textContent = '⏸️ 停止刷新';
                btn.classList.add('active');
                autoRefreshEnabled = true;
                showStatus('自动刷新已开启 (5秒间隔)', 'success');
            }
        }
        
        // 图片预览
        function previewImage(url) {
            const modal = document.getElementById('imageModal');
            const modalImg = document.getElementById('modalImage');
            modal.style.display = 'flex';
            modalImg.src = url;
        }
        
        function closeModal() {
            document.getElementById('imageModal').style.display = 'none';
        }
        
        // 显示状态信息
        function showStatus(message, type = 'info') {
            const statusEl = document.getElementById('status');
            statusEl.textContent = message;
            statusEl.style.color = type === 'error' ? '#e74c3c' : 
                                 type === 'success' ? '#2ecc71' : '#3498db';
            
            setTimeout(() => {
                statusEl.textContent = '就绪';
                statusEl.style.color = '#7f8c8d';
            }, 3000);
        }
        
        // HTML转义
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // 页面初始化
        document.addEventListener('DOMContentLoaded', () => {
            loadClients();
            // 默认开启自动刷新
            toggleAutoRefresh();
        });
        
        // 页面可见性变化时处理自动刷新
        document.addEventListener('visibilitychange', () => {
            if (autoRefreshEnabled) {
                if (document.hidden) {
                    clearInterval(autoRefreshInterval);
                } else {
                    autoRefreshInterval = setInterval(refreshAll, 5000);
                }
            }
        });
        
        // 点击模态框背景关闭
        document.getElementById('imageModal').addEventListener('click', function(e) {
            if (e.target === this) closeModal();
        });
    </script>
</body>
</html>
'''

# 全局变量存储截图目录路径
SCREENSHOT_DIR = os.path.join(BASE_DIR, 'screenshots')
DATA_DIR = os.path.join(BASE_DIR, 'data')

def create_directories():
    """创建必要的目录结构"""
    directories = [
        SCREENSHOT_DIR,
        os.path.join(DATA_DIR, 'hardware'),
        os.path.join(DATA_DIR, 'software'),
        LOG_DIR
    ]
    
    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            print(f"创建目录: {directory}")
        except Exception as e:
            print(f"创建目录失败 {directory}: {e}")

# API路由
@app.route('/')
def index():
    """显示监控面板"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/clients')
def get_clients():
    """获取所有客户端列表"""
    clients = client_manager.get_all_clients()
    return jsonify({
        'clients': clients,
        'count': len(clients),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/logs/<client_id>')
def get_client_logs(client_id):
    """获取特定客户端的日志"""
    logs = storage.get_client_logs(client_id)
    return jsonify(logs)

@app.route('/api/logs/clear', methods=['POST'])
def clear_all_logs():
    """清空所有日志"""
    success = storage.clear_all_logs()
    if success:
        logger.info("所有日志已清空")
        return jsonify({
            'status': 'success',
            'message': '所有日志已清空',
            'timestamp': datetime.now().isoformat()
        })
    else:
        return jsonify({'status': 'error', 'message': '清空失败'}), 500

# 数据接收路由
@app.route('/api/receive/heartbeat', methods=['POST'])
def receive_heartbeat():
    """接收客户端心跳"""
    try:
        data = request.json
        if not data or 'client_id' not in data:
            return jsonify({'status': 'error', 'message': '无效的心跳数据'}), 400
        
        success = client_manager.update_client_heartbeat(data['client_id'])
        if success:
            return jsonify({'status': 'success', 'message': '心跳已接收'})
        else:
            return jsonify({'status': 'error', 'message': '客户端未注册'}), 404
            
    except Exception as e:
        logger.error(f"处理心跳时出错: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/receive/register', methods=['POST'])
def register_client():
    """客户端注册"""
    try:
        data = request.json
        if not data:
            return jsonify({'status': 'error', 'message': '无效的注册数据'}), 400
        
        # 确保必要的字段存在
        if 'hostname' not in data:
            data['hostname'] = 'Unknown'
        if 'username' not in data:
            data['username'] = 'Unknown'
        if 'os' not in data:
            data['os'] = 'Unknown'
        
        client_id = client_manager.register_client(data)
        
        logger.info(f"新客户端注册: {client_id} ({data.get('hostname')})")
        
        return jsonify({
            'status': 'success',
            'client_id': client_id,
            'message': '客户端注册成功',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"客户端注册时出错: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/receive/hardware', methods=['POST'])
def receive_hardware():
    """接收硬件信息"""
    try:
        data = request.json
        if not data:
            return jsonify({'status': 'error', 'message': '没有收到数据'}), 400
        
        client_id = data.get('client_id')
        if not client_id:
            return jsonify({'status': 'error', 'message': '缺少client_id'}), 400
        
        # 更新客户端心跳
        client_manager.update_client_heartbeat(client_id)
        
        # 存储硬件信息
        log_count = storage.add_hardware_log(client_id, data)
        
        logger.info(f"接收硬件信息: {client_id} - 日志数: {log_count}")
        
        # 保存到文件
        save_to_file('hardware', client_id, data)
        
        return jsonify({
            'status': 'success',
            'message': '硬件信息已接收',
            'log_count': log_count,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"接收硬件信息时出错: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/receive/software', methods=['POST'])
def receive_software():
    """接收软件信息"""
    try:
        data = request.json
        if not data:
            return jsonify({'status': 'error', 'message': '没有收到数据'}), 400
        
        client_id = data.get('client_id')
        if not client_id:
            return jsonify({'status': 'error', 'message': '缺少client_id'}), 400
        
        # 更新客户端心跳
        client_manager.update_client_heartbeat(client_id)
        
        # 存储软件信息
        log_count = storage.add_software_log(client_id, data)
        
        logger.info(f"接收软件信息: {client_id} - 日志数: {log_count}")
        
        # 保存到文件
        save_to_file('software', client_id, data)
        
        return jsonify({
            'status': 'success',
            'message': '软件信息已接收',
            'log_count': log_count,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"接收软件信息时出错: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/receive/screenshot', methods=['POST'])
def receive_screenshot():
    """接收屏幕截图"""
    try:
        if 'file' in request.files:
            # 文件上传方式
            file = request.files['file']
            metadata = json.loads(request.form.get('metadata', '{}'))
            
            client_id = metadata.get('client_id')
            if not client_manager.is_safe_client_id(client_id):
                return jsonify({'status': 'error', 'message': '缺少client_id'}), 400
            
            # 生成唯一文件名
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
            filename = f"{client_id}_{timestamp}.png"
            filepath = os.path.join(SCREENSHOT_DIR, filename)
            
            # 确保目录存在
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 保存文件
            file.save(filepath)
            print(f"截图已保存到: {filepath}")
            
            # 更新客户端心跳
            client_manager.update_client_heartbeat(client_id)
            
            # 存储截图记录
            log_count = storage.add_screenshot_log(client_id, filename)
            
            logger.info(f"接收截图: {client_id} - 文件名: {filename}")
            
            return jsonify({
                'status': 'success',
                'filename': filename,
                'log_count': log_count,
                'timestamp': datetime.now().isoformat()
            })
            
        elif request.is_json:
            # Base64方式
            data = request.json
            client_id = data.get('client_id')
            image_data = data.get('image')
            
            if not client_manager.is_safe_client_id(client_id) or not image_data:
                return jsonify({'status': 'error', 'message': '缺少必要字段'}), 400
            
            # Base64解码
            try:
                image_bytes = base64.b64decode(image_data)
            except Exception as decode_error:
                print(f"Base64解码失败: {decode_error}")
                return jsonify({'status': 'error', 'message': 'Base64解码失败'}), 400
            
            # 生成唯一文件名
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
            filename = f"{client_id}_{timestamp}.png"
            filepath = os.path.join(SCREENSHOT_DIR, filename)
            
            # 确保目录存在
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 保存文件
            try:
                with open(filepath, 'wb') as f:
                    f.write(image_bytes)
                print(f"截图(Base64)已保存到: {filepath}")
            except Exception as save_error:
                print(f"保存截图失败: {save_error}")
                return jsonify({'status': 'error', 'message': f'保存文件失败: {save_error}'}), 500
            
            # 更新客户端心跳
            client_manager.update_client_heartbeat(client_id)
            
            # 存储截图记录
            log_count = storage.add_screenshot_log(client_id, filename)
            
            logger.info(f"接收截图(Base64): {client_id} - 文件名: {filename}")
            
            return jsonify({
                'status': 'success',
                'filename': filename,
                'log_count': log_count,
                'timestamp': datetime.now().isoformat()
            })
            
        else:
            return jsonify({'status': 'error', 'message': '无效的请求格式'}), 400
        
    except Exception as e:
        logger.error(f"接收截图时出错: {e}")
        print(f"接收截图时出错: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/screenshots/<filename>')
def serve_screenshot(filename):
    """提供截图文件的静态访问"""
    # 安全验证
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(SCREENSHOT_DIR, safe_filename)
    
    if not os.path.exists(filepath):
        print(f"截图文件不存在: {filepath}")
        return jsonify({'status': 'error', 'message': '截图文件不存在'}), 404
    
    try:
        return send_from_directory(SCREENSHOT_DIR, safe_filename)
    except Exception as e:
        logger.error(f"发送截图文件时出错: {e}")
        print(f"发送截图文件时出错: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

def save_to_file(data_type, client_id, data):
    """将数据保存到文件"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{data_type}_{client_id}_{timestamp}.json"
        filepath = os.path.join(DATA_DIR, data_type, filename)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        print(f"数据保存到文件: {filepath}")
        logger.debug(f"数据保存到文件: {filepath}")
    except Exception as e:
        print(f"保存文件时出错: {e}")
        logger.error(f"保存文件时出错: {e}")

# 添加静态文件服务
@app.route('/static/<path:path>')
def serve_static(path):
    """提供静态文件服务"""
    static_dir = os.path.join(BASE_DIR, 'static')
    return send_from_directory(static_dir, path)

if __name__ == '__main__':
    # 命令行参数解析
    parser = argparse.ArgumentParser(description='系统信息监控服务器')
    parser.add_argument('--host', default='0.0.0.0', help='服务器监听地址')
    parser.add_argument('--port', type=int, default=8000, help='服务器端口')
    parser.add_argument('--max-logs', type=int, default=1000, help='最大日志条数')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    
    args = parser.parse_args()
    
    # 更新配置
    storage.max_entries = args.max_logs
    
    # 创建目录
    create_directories()
    
    print(f"""
    ============================================
    系统信息监控服务器 v2.0
    启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    
    程序目录: {BASE_DIR}
    截图目录: {SCREENSHOT_DIR}
    数据目录: {DATA_DIR}
    日志目录: {LOG_DIR}
    
    访问地址: http://{args.host}:{args.port}
    监听端口: {args.port}
    最大日志数: {args.max_logs}
    调试模式: {'开启' if args.debug else '关闭'}
    
    API端点:
      - GET  /                    # Web监控界面
      - GET  /api/clients         # 获取客户端列表
      - GET  /api/logs/<client_id> # 获取客户端日志
      - POST /api/receive/register # 客户端注册
      - POST /api/receive/heartbeat # 客户端心跳
      - POST /api/receive/hardware  # 接收硬件信息
      - POST /api/receive/software  # 接收软件信息
      - POST /api/receive/screenshot # 接收屏幕截图
      - GET  /screenshots/<filename> # 获取截图
    ============================================
    """)
    
    try:
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug,
            threaded=True,
            use_reloader=False
        )
    except KeyboardInterrupt:
        logger.info("服务器正在关闭...")
        print("服务器正在关闭...")
    except Exception as e:
        logger.error(f"服务器启动失败: {e}")
        print(f"服务器启动失败: {e}")