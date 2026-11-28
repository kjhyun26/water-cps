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
HOST_NAMES = ['plc1', 'plc2', 'plc3', 'sis', 'hmi', 'ai', 'attacker']

# AI 에이전트 사용 여부. True 이면 ai 노드에서 ai_agent.py 를 띄워 예측값 패킷
# (logs/ai_predictions.jsonl)을 낸다. AI는 PLC2에 쓰지 않는다 (ai_agent.DIRECT_WRITE = False).
USE_AI_AGENT = True
# 보안 조치 사용 여부. True 이면 iptables(장치별 출발지 제한), 게이트웨이 검사, PLC2 설정값 검사를
# 함께 켠다. False 이면 셋 다 끈다 (보안 조치 전과 비교용. 게이트웨이는 검사 없이 그대로 전달).
USE_SECURITY = True
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
    subprocess.run(['pkill', '-f', 'plc[123].py|sis.py|physical_process.py|hmi.py|ai_agent.py|scenario_feeder.py|gateway.py'])
    # 지난 실행이 남긴 AI 멈춤 표시, 자-테스트 값, 예측값 패킷, 게이트웨이 기록·모드를 지운다
    # (지난 패킷이 남아 있으면 새 게이트웨이가 오래된 값으로 보고 거부해 바로 폴백한다)
    for path in ('logs/ai_agent.pause', 'logs/jar_test.json', 'logs/ai_predictions.jsonl',
                 'logs/gateway.log', 'logs/gateway_mode.txt'):
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


def firewall_commands(name):
    """장치 하나의 iptables 명령 목록. config.FIREWALL_ALLOWED 의 출발지만 44818 새 연결을 받는다."""
    commands = ['iptables -F INPUT',
                'iptables -A INPUT -i lo -j ACCEPT',
                'iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT']
    for protocol in ('tcp', 'udp'):
        for source in config.FIREWALL_ALLOWED[name]:
            commands.append('iptables -A INPUT -p %s --dport %d -s %s -j ACCEPT'
                            % (protocol, config.ENIP_PORT, config.ADDRESS[source]))
        commands.append('iptables -A INPUT -p %s --dport %d -j DROP' % (protocol, config.ENIP_PORT))
    return commands


def apply_firewall(network):
    """장치마다 자기 이름공간에 규칙을 걸고, 걸린 규칙을 logs/firewall_rules.txt 에 남긴다.
    하나라도 실패하면 멈춘다 (보안을 켰다고 생각했는데 꺼져 있는 상태를 막는다)."""
    dump = []
    for name in config.FIREWALL_ALLOWED:
        node = network.get(name)
        for command in firewall_commands(name):
            output = node.cmd(command + ' 2>&1; echo "exit=$?"').strip()
            if not output.endswith('exit=0'):
                raise RuntimeError('firewall on %s failed: %s -> %s' % (name, command, output))
        dump.append('### %s (%s)\n%s' % (name, config.ADDRESS[name], node.cmd('iptables -S INPUT')))
    with open('logs/firewall_rules.txt', 'w') as rules_file:
        rules_file.write('\n'.join(dump))
    print('*** iptables 적용: %s (logs/firewall_rules.txt)' % ', '.join(config.FIREWALL_ALLOWED))


def start_devices(network, use_ai_agent=False, feed_scenario=False, use_security=False):
    """기본값(모두 False)은 AI·원수 공급기·보안 조치 없이 띄운다. tests/verify_system.py 가 이 상태를 시험한다.

    use_security=True: iptables + 게이트웨이 검사 + PLC2 설정값 검사.
    use_security=False: 셋 다 없음. AI를 띄우면 게이트웨이는 검사 없이 패킷 값을 그대로 쓴다(passthrough)."""
    if use_security:
        # 장치를 띄우기 전에 건다. 장치끼리의 정상 연결은 모두 허용 목록 안이다
        apply_firewall(network)
    # PLC1이 준비된 뒤에 PLC2를 띄워야 PLC2가 유량을 0으로 읽지 않는다
    for name in ('plc1', 'plc3', 'sis'):
        launch(network.get(name), name + '.py', name)
    wait_until_ready(['plc1', 'plc3', 'sis'])
    # 보안을 켜면 PLC2 자체 보호(5~50 mg/L 범위, 공정 1분당 2 mg/L ramp)도 켠다
    launch(network.get('plc2'), 'plc2.py', 'plc2', '--secure' if use_security else '')
    wait_until_ready(['plc2'])
    # 공정은 마지막에 띄운다. 장치가 준비되기 전에는 공정 시각이 흐르지 않게 하려는 것이다
    launch(network.get('s1'), 'physical_process.py', 'process')
    launch(network.get('hmi'), 'hmi.py', 'hmi')
    if feed_scenario:
        # 원수는 망 밖에서 오는 값이므로 공정과 같이 s1(루트 이름공간)에서 돌린다
        launch(network.get('s1'), 'scenario_feeder.py', 'scenario_feeder',
               '--start-hours %s' % FEED_START_HOURS)
    if use_ai_agent:
        launch(network.get('ai'), 'ai_agent.py', 'ai_agent')
    if use_ai_agent or use_security:
        # 게이트웨이: AI 예측값 패킷을 검사해 통과한 값만 PLC2에 쓴다 (게이트웨이 구현안내 7장).
        # 보안을 끄면 검사 없이 그대로 쓴다(passthrough). 비교 시험에서 같은 패킷 경로를 쓰기 위해서다.
        # 콘솔 출력은 gateway_console.log. logs/gateway.log 는 게이트웨이가 직접 쓰는 판정 기록(JSON lines)
        launch(network.get('hmi'), 'gateway.py', 'gateway_console',
               '' if use_security else '--passthrough')


def stop(network):
    clean_up_processes()
    network.stop()


def main():
    network = build_network()
    try:
        start_devices(network, USE_AI_AGENT, FEED_SCENARIO, USE_SECURITY)
        print('*** 화면: 다른 터미널에서  watch -n 1 cat %s' % os.path.abspath(config.HMI_SCREEN))
        print('*** 보안 조치(iptables·게이트웨이 검사·PLC2 검사): %s' % ('켬' if USE_SECURITY else '끔'))
        if USE_AI_AGENT:
            print('*** AI 에이전트: ai 노드, 예측값 패킷만 냄 (logs/ai_agent.log, logs/ai_predictions.jsonl)')
            print('*** 자-테스트 입력: ai %s ai_agent.py jartest 20.5' % VENV_PYTHON)
        if USE_SECURITY:
            print('*** 게이트웨이: tail -f logs/gateway.log  |  폴백 복귀: hmi %s gateway.py resume' % VENV_PYTHON)
        print('*** 명령: hmi %s hmi.py dose 20  |  hmi %s hmi.py sis reset' % (VENV_PYTHON, VENV_PYTHON))
        if FEED_SCENARIO:
            print('*** 원수 공급기: 시나리오 %s시간째부터 (logs/scenario_feeder.log)' % FEED_START_HOURS)
        CLI(network)
    finally:
        stop(network)


if __name__ == '__main__':
    main()
