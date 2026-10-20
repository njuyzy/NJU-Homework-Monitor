"""Interrupt model HTTP waits, including requests without a time limit."""
import socket
import threading
import time
from contextlib import contextmanager

import requests
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool


class Cancelled(Exception):
    pass


class RequestControl:
    def __init__(self, cancel, timeout):
        self.cancel = cancel
        self.deadline = time.monotonic() + timeout if timeout else None
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.connections = []
        self.sockets = []

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RuntimeError('模型请求超过设置的超时时间。')

    def watch(self):
        while not self.finished.wait(.05):
            if not self.cancel.is_set() and (self.deadline is None or time.monotonic() < self.deadline):
                continue
            with self.lock:
                sockets = self.sockets + [conn.sock for conn in self.connections if conn.sock is not None]
            for connection in sockets:
                try:
                    # shutdown wakes a blocked read; Response owns closing the socket.
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class InterruptibleAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, control):
        class ConnectionMixin:
            def getresponse(self):
                # HTTP/1.0 may detach conn.sock after the headers. Retain it so
                # cancellation still interrupts a silent response body.
                with control.lock:
                    if self.sock is not None:
                        control.sockets.append(self.sock)
                control.check()
                return super().getresponse()

        class PlainConnection(ConnectionMixin, HTTPConnection):
            pass

        class SecureConnection(ConnectionMixin, HTTPSConnection):
            pass

        class PoolMixin:
            def _new_conn(self):
                control.check()
                conn = super()._new_conn()
                with control.lock:
                    control.connections.append(conn)
                return conn

        class PlainPool(PoolMixin, HTTPConnectionPool):
            ConnectionCls = PlainConnection

        class SecurePool(PoolMixin, HTTPSConnectionPool):
            ConnectionCls = SecureConnection

        self.pool_classes = {'http': PlainPool, 'https': SecurePool}
        super().__init__(max_retries=0)

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = self.pool_classes

    def proxy_manager_for(self, *args, **kwargs):
        manager = super().proxy_manager_for(*args, **kwargs)
        manager.pool_classes_by_scheme = self.pool_classes
        return manager


@contextmanager
def model_response(endpoint, cancel, timeout, **kwargs):
    control = RequestControl(cancel, timeout)
    control.check()
    watcher = threading.Thread(target=control.watch, name='model-request-control', daemon=True)
    with requests.Session() as session:
        session.mount('http://', InterruptibleAdapter(control))
        session.mount('https://', InterruptibleAdapter(control))
        watcher.start()
        try:
            # None disables both connection and read timeouts. The watcher can
            # still interrupt an established connection before headers or body.
            with session.post(endpoint, timeout=timeout or None, **kwargs) as response:
                yield response
        finally:
            control.finished.set()
            watcher.join()
            control.check()
