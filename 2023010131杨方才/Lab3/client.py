import socket

# 阶段 1：创建套接字
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # 前两个空白

# 阶段 2：连接服务器
client.connect(('127.0.0.1', 8080))  # 第三个空白

# 阶段 3：发送数据
client.send('你好，服务器'.encode())  # 第四个空白

# 阶段 3：接收响应
data = client.recv(1024)  # 第五个空白（含参数）
print('收到：', data.decode())

# 阶段 4：断开
client.close()  # 第六个空白