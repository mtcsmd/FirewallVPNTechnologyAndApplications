import socket

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect(('example.com', 80))

request = (
    'GET / HTTP/1.0\r\n'
    'Host: example.com\r\n'
    '\r\n'
)
client.send(request.encode())

response = b''
while True:
    chunk = client.recv(4096)
    if not chunk:
        break
    response += chunk

print(response.decode(errors='ignore')[:500])
client.close()