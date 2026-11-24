"""가상 네트워크를 만들고 장치를 띄운 뒤 Mininet 명령창을 연다 (설계 문서 8-6절).

실행: sudo python3 run.py   (시스템 Python. minicps 를 import하지 않는다)
"""

import os
import subprocess
import time

from mininet.cli import CLI
from mininet.net import Mininet
from mininet.node import OVSBridge
from mininet.topo import Topo

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import init  # noqa: E402

VENV_PYTHON = os.path.abspath('venv/bin/python')
HOST_NAMES = ['plc1', 'plc2', 'plc3', 'sis', 'hmi']


class WaterTopology(Topo):
    def build(self):
        switch = self.addSwitch('s1')
        for name in HOST_NAMES:
            host = self.addHost(name, ip=config.ADDRESS[name] + config.NETMASK)
            self.addLink(host, switch)


def clean_up_processes():
    subprocess.run(['pkill', '-f', 'cpppo.server.enip'])
    subprocess.run(['pkill', '-f', 'plc[123].py|sis.py|physical_process.py|hmi.py'])


def launch(node, script, log_name):
    node.cmd('%s -u %s > logs/%s.log 2>&1 &' % (VENV_PYTHON, script, log_name))


def build_network():
    clean_up_processes()
    init.create_state()
    network = Mininet(topo=WaterTopology(), switch=OVSBridge, controller=None)
    network.start()
    network.pingAll()
    return network


def wait_until_ready(names, timeout_seconds=20):
    """장치 기록에 처음 값 쓰기 성공(config.write_initial_values)이 찍힐 때까지 기다린다.
    20초는 write_initial_values 의 최대 시도(20번 × 0.5초 + 통신)보다 길게 잡은 값이다."""
    deadline = time.time() + timeout_seconds
    waiting = set(names)
    while waiting and time.time() < deadline:
        for name in list(waiting):
            try:
                with open('logs/%s.log' % name, errors='replace') as log_file:
                    text = log_file.read()
            except FileNotFoundError:
                continue
            if 'initial values written' in text:
                waiting.discard(name)
            elif 'ERROR could not write initial values' in text:
                raise RuntimeError('%s could not write its initial values, see logs/%s.log' % (name, name))
        time.sleep(0.2)
    if waiting:
        raise RuntimeError('not ready after %d s: %s' % (timeout_seconds, ', '.join(sorted(waiting))))


def start_devices(network):
    # PLC1이 준비된 뒤에 PLC2를 띄워야 PLC2가 유량을 0으로 읽지 않는다
    for name in ('plc1', 'plc3', 'sis'):
        launch(network.get(name), name + '.py', name)
    wait_until_ready(['plc1', 'plc3', 'sis'])
    launch(network.get('plc2'), 'plc2.py', 'plc2')
    wait_until_ready(['plc2'])
    # 공정은 마지막에 띄운다. 장치가 준비되기 전에는 공정 시각이 흐르지 않게 하려는 것이다
    launch(network.get('s1'), 'physical_process.py', 'process')
    launch(network.get('hmi'), 'hmi.py', 'hmi')


def stop(network):
    clean_up_processes()
    network.stop()


def main():
    network = build_network()
    try:
        start_devices(network)
        print('*** 화면: 다른 터미널에서  watch -n 1 cat %s' % os.path.abspath(config.HMI_SCREEN))
        print('*** 명령: hmi %s hmi.py dose 20  |  hmi %s hmi.py sis reset' % (VENV_PYTHON, VENV_PYTHON))
        CLI(network)
    finally:
        stop(network)


if __name__ == '__main__':
    main()
