
# 配置和使用说明

# 1. 服务端启动
基本启动
```
python server.py --host 0.0.0.0 --port 8000
```
指定最大日志数
```
python server.py --port 8000 --max-logs 5000
```
调试模式
```
python server.py --port 8000 --debug
```
打包后创建windows服务
```
sc create zfmpcinfo binPath= "<server.exe的完整路径> --port 8000 --max-logs 5000" start= auto displayname= "zfmpcinfo"
```
添加服务描述
```
sc description  zfmpcinfo "计算机信息服务"
```
删除服务：
```
sc stop zfmpcinfo && sc delete zfmpcinfo
```

# 2. 客户端启动
运行一次
```
python client.py --server http://192.168.1.100:8000 --once
```
守护进程模式
```
python client.py --server http://192.168.1.100:8000 --daemon --interval 300
```
指定客户端ID
```
python client.py --server http://192.168.1.100:8000 --client-id mypc001 --daemon
```
调试模式（显示输出）
```
python client.py --server http://192.168.1.100:8000 --once --debug
```

# 3. Windows服务安装
安装为Windows系统服务（需要管理员权限）
```
python client.py --server http://192.168.1.100:8000 --install-service
```
安装后会创建服务，可以手动启动/停止
服务名称: SystemMonitor
启动类型: 自动

# 4. 访问监控界面
打开浏览器访问：http://服务器IP:8000

# 系统特点

完全HTTP协议：所有数据都通过HTTP协议传输

多客户端支持：支持同时监控多个客户端

心跳机制：客户端定期发送心跳，服务端可显示在线状态

数据持久化：所有数据都保存到文件系统

安全设计：客户端ID验证，防止未授权访问

静默运行：客户端无界面，不干扰用户

自动清理：服务端自动清理非活跃客户端和旧日志

详细的日志：完整的操作日志记录
