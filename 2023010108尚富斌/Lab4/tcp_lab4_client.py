import socket
import os
import time

PORT = int(os.environ.get("LAB4_PORT", 38090))
SERVER_HOST = '127.0.0.1'

client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print("socket created")

client_socket.bind(('127.0.0.1', 0))
local_addr = client_socket.getsockname()
print(f"local socket = {local_addr}")

print("calling connect()")
connect_start = time.time()

client_socket.connect((SERVER_HOST, PORT))

connect_end = time.time()
print(f"connect() returned, cost: {connect_end - connect_start:.3f}s")

request_data = b"X" * 200000
print(f"sendall() start, request bytes={len(request_data)}")
client_socket.sendall(request_data)

print("calling recv() and waiting for response")
recv_start = time.time()

response = b""
while len(response) < 18000:
    chunk = client_socket.recv(4096)
    if not chunk:
        break
    response += chunk

recv_end = time.time()
wait_time = recv_end - recv_start
print(f"first recv() returned after {wait_time:.3f}s")
print(f"received response length: {len(response)}")
print(f"response preview: {response[:50]}...")

client_socket.close()
print("client closed")