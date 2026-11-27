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

# AI 에이전트 사용 여부. True 이면 hmi 노드에서 hmi.py(화면)와 함께 ai_agent.py 를 띄워
# 주입률 설정값을 자동으로 정한다. False 이면 hmi.py 만 띄운다 (운영자 수동).
USE_AI_AGENT = True
# 원수 공급기 사용 여부. True 이면 scenario_feeder.py 가 원수 시나리오(호우·조류)를 흘려 넣는다.
FEED_SCENARIO = True
# 시나리오를 몇 시간째부터 시작할지. 119.5 이면 5일차 호우 30분 전이라 실제 30초 뒤 호우가 온다.
FEED_START_HOURS = 119.5


class WaterTopology(Topo):
    def build(self):
        switch = self.addSwitch('s1')
        for name in HOST_NAMES:
            host = self.addHost(name, ip=config.ADDRESS[name] + config.NETMASK)
            self.addLink(host, switch)


def clean_up_processes():
    subprocess.run(['pkill', '-f', 'cpppo.server.enip'])
    subprocess.run(['pkill', '-f', 'plc[123].py|sis.py|physical_process.py|hmi.py|ai_agent.py|scenario_feeder.py'])
    # 지난 실행이 남긴 AI 멈춤 표시와 조류 센서 값을 지운다
    for path in ('logs/ai_agent.pause', 'logs/algae_sensor.txt'):
        if os.path.exists(path):
            os.remove(path)


def launch(node, script, log_name, arguments=''):
    node.cmd('%s -u %s %s > logs/%s.log 2>&1 &' % (VENV_PYTHON, script, arguments, log_name))


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


def start_devices(network, use_ai_agent=False, feed_scenario=False):
    """기본값(둘 다 False)은 AI와 원수 공급기 없이 띄운다. tests/verify_system.py 가 이 상태를 시험한다."""
    # PLC1이 준비된 뒤에 PLC2를 띄워야 PLC2가 유량을 0으로 읽지 않는다
    for name in ('plc1', 'plc3', 'sis'):
        launch(network.get(name), name + '.py', name)
    wait_until_ready(['plc1', 'plc3', 'sis'])
    launch(network.get('plc2'), 'plc2.py', 'plc2')
    wait_until_ready(['plc2'])
    # 공정은 마지막에 띄운다. 장치가 준비되기 전에는 공정 시각이 흐르지 않게 하려는 것이다
    launch(network.get('s1'), 'physical_process.py', 'process')
    launch(network.get('hmi'), 'hmi.py', 'hmi')
    if feed_scenario:
        # 원수는 망 밖에서 오는 값이므로 공정과 같이 s1(루트 이름공간)에서 돌린다
        launch(network.get('s1'), 'scenario_feeder.py', 'scenario_feeder',
               '--start-hours %s' % FEED_START_HOURS)
    if use_ai_agent:
        launch(network.get('hmi'), 'ai_agent.py', 'ai_agent')


def stop(network):
    clean_up_processes()
    network.stop()


def main():
    network = build_network()
    try:
        start_devices(network, USE_AI_AGENT, FEED_SCENARIO)
        print('*** 화면: 다른 터미널에서  watch -n 1 cat %s' % os.path.abspath(config.HMI_SCREEN))
        if USE_AI_AGENT:
            print('*** AI 에이전트가 주입률을 자동 제어 중 (logs/ai_agent.log)')
            print('*** 수동 개입: hmi %s ai_agent.py pause  뒤에  hmi %s hmi.py dose 20' % (VENV_PYTHON, VENV_PYTHON))
            print('***           AI 복귀: hmi %s ai_agent.py resume  |  SIS: hmi %s hmi.py sis reset' % (VENV_PYTHON, VENV_PYTHON))
        else:
            print('*** 명령: hmi %s hmi.py dose 20  |  hmi %s hmi.py sis reset' % (VENV_PYTHON, VENV_PYTHON))
        if FEED_SCENARIO:
            print('*** 원수 공급기: 시나리오 %s시간째부터 (logs/scenario_feeder.log)' % FEED_START_HOURS)
        CLI(network)
    finally:
        stop(network)


if __name__ == '__main__':
    main()
