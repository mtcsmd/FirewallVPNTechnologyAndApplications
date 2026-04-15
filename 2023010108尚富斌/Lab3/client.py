import socket
import time  # 按PEP8规范，import统一放在文件顶部

# 创建TCP套接字
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# 连接本地服务器8080端口
client_socket.connect(('127.0.0.1', 8080))

# 发送消息给服务器（修正后）
client_socket.sendall("你好，服务器".encode('utf-8'))

# 接收服务器回复
data = client_socket.recv(1024)
print(f"收到: {data.decode('utf-8')}")

# 关键修改：让客户端也保持10秒，不主动关闭
print("连接保持10秒，方便你执行netstat命令...")
time.sleep(10)  # 等待10秒，期间连接一直是ESTABLISHED状态

# 10秒后关闭连接
client_socket.close()