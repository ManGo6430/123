import socket
import struct
import logging
import time

import numpy as np

from util.socketutil import recvexactly, recvall

logger = logging.getLogger(__name__)

def _check_okay(sock):
    result = recvexactly(sock, 4)
    if result != b'OKAY':
        raise RuntimeError(_read_hexlen(sock))


def _read_hexlen(sock):
    textlen = int(recvexactly(sock, 4), 16)
    if textlen == 0:
        return b''
    buf = recvexactly(sock, textlen)
    return buf


class ADBClientSession:
    def __init__(self, server=None, timeout=None):
        if server is None:
            server = ('127.0.0.1', 5037)
        if server[0] == '127.0.0.1' or server[0] == '::1':
            timeout = 0.5
        sock = socket.create_connection(server, timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)
        self.sock: socket.socket = sock

    def close(self):
        self.sock.close()

    def service(self, cmd):
        """make a service request to ADB server, consult ADB sources for available services"""
        cmdbytes = cmd.encode()
        data = b'%04X%b' % (len(cmdbytes), cmdbytes)
        self.sock.send(data)
        _check_okay(self.sock)
        return self

    def read_response(self):
        """read a chunk of length indicated by 4 hex digits"""
        return _read_hexlen(self.sock)

    def devices(self):
        """returns list of devices that the adb server knows"""
        resp = self.service('host:devices').read_response().decode()
        devices = [tuple(line.split('\t')) for line in resp.splitlines()]
        return devices

    def connect(self, device):
        resp = self.service('host:connect:%s' % device).read_response().decode(errors='ignore')
        logger.debug('adb connect %s: %s', device, resp)
        if 'unable' in resp or 'cannot' in resp:
            raise RuntimeError(resp)

    def disconnect(self, device):
        resp = self.service('host:disconnect:%s' % device).read_response().decode(errors='ignore')
        logger.debug('adb disconnect %s: %s', device, resp)
        if 'unable' in resp or 'cannot' in resp:
            raise RuntimeError(resp)

    def device(self, devid=None):
        """switch to a device"""
        if devid is None:
            return self.service('host:transport-any')
        return self.service('host:transport:' + devid)

    def usbdevice(self):
        """switch to a USB-connected device"""
        return self.service('host:transport-usb')

    def emulator(self):
        """switch to an (SDK) emulator device"""
        return self.service('host:transport-local')

    def exec_stream(self, cmd=''):
        """run command in device, with stdout/stdin attached to the socket returned"""
        self.service('exec:' + cmd)
        return self.sock

    def exec(self, cmd):
        """run command in device, returns stdout content after the command exits"""
        if len(cmd) == 0:
            raise ValueError('no command specified for blocking exec')
        sock = self.exec_stream(cmd)
        data = recvall(sock)
        sock.close()
        return data

    def shell_stream(self, cmd=''):
        """run command in device, with pty attached to the socket returned"""
        self.service('shell:' + cmd)
        return self.sock

    def shell(self, cmd):
        """run command in device, returns pty output after the command exits"""
        if len(cmd) == 0:
            raise ValueError('no command specified for blocking shell')
        sock = self.shell_stream(cmd)
        data = recvall(sock)
        sock.close()
        return data

    def push(self, target_path: str, buffer: 'ReadableBuffer', mode=0o100755, mtime: int = None):
        """push data to device"""
        # Python has no type hint for buffer protocol, why?
        self.service('sync:')
        request = b'%s,%d' % (target_path.encode(), mode)
        self.sock.send(b'SEND' + struct.pack("<I", len(request)) + request)
        sendbuf = np.empty(65536+8, dtype=np.uint8)
        sendbuf[0:4] = np.frombuffer(b'DATA', dtype=np.uint8)
        input_arr = np.frombuffer(buffer, dtype=np.uint8)
        for arr in np.array_split(input_arr, np.arange(65536, input_arr.size, 65536)):
            sendbuf[4:8].view('<I')[0] = len(arr)
            sendbuf[8:8+len(arr)] = arr
            self.sock.sendall(sendbuf[0:8+len(arr)])
        if mtime is None:
            mtime = int(time.time())
        self.sock.sendall(b'DONE' + struct.pack("<I", mtime))
        response = recvexactly(self.sock, 8)
        self.sock.close()
        if response[:4] != b'OKAY':
            raise RuntimeError('push failed')
