"""Deny outbound network connections in the synthetic benchmark process."""
import socket


def localhost_only():
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex

    def check(address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("benchmark outbound connection forbidden")
        if address[1] != 27018:
            raise AssertionError("benchmark connection outside disposable Mongo forbidden")

    def connect(sock, address):
        check(address)
        return original(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_ex(sock, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
