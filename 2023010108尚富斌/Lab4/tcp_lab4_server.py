import socket
import os
import time

PORT = int(os.environ.get("LAB4_PORT", 38090))
HOST = '127.0.0.1'

server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind((HOST, PORT))
print(f"server listening on {HOST}:{PORT}")

server_socket.listen(1)
conn, addr = server_socket.accept()
print(f"client connected: {addr}")

try:
    data = b""
    while len(data) < 200000:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    print(f"received total bytes: {len(data)}")

    time.sleep(2)

    response = b"Hello from TCP Server! " * 1000
    conn.sendall(response)
    print(f"response sent, total bytes: {len(response)}")

    time.sleep(10)

finally:
    conn.close()
    server_socket.close()
    print("server closed")