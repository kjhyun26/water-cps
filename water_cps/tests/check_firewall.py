"""방화벽 점검 (보안 조치 2단계): 호스트 7대 → 서버 4대 의 44818 TCP 접속 행렬을 방화벽 없이/건 뒤에 비교한다.
실행: cd ~/water_cps && sudo python3 tests/check_firewall.py     (약 1분)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/ 의 위 폴더
import run  # noqa: E402  (작업 폴더를 water_cps 로 바꾼다)
import config  # noqa: E402

SERVERS = list(config.FIREWALL_ALLOWED)
PROBE = ("import socket,sys\n"
         "try:\n socket.create_connection(('%s', %d), 3).close(); print('OPEN')\n"
         "except Exception as e: print('BLOCKED', type(e).__name__)")


def matrix(network, label, expect):
    bad = 0
    print('\n== %s ==' % label)
    print('%-9s' % 'from\\to' + ''.join('%-9s' % s for s in SERVERS))
    for source in run.HOST_NAMES:
        cells = []
        for server in SERVERS:
            out = network.get(source).cmd(
                "%s -c \"%s\"" % (run.VENV_PYTHON, PROBE % (config.ADDRESS[server], config.ENIP_PORT))).strip()
            opened = out.endswith('OPEN')
            want = expect(source, server)
            bad += opened != want
            cells.append(('open' if opened else 'drop') + ('' if opened == want else '!'))
        print('%-9s' % source + ''.join('%-9s' % c for c in cells))
    return bad


def main():
    network = run.build_network()
    try:
        run.start_devices(network)
        time.sleep(2)
        bad = matrix(network, 'security OFF (모두 열림이어야 함)', lambda s, d: True)
        run.apply_firewall(network)
        bad += matrix(network, 'security ON (허용 목록만 열림)',
                      lambda s, d: s == d or s in config.FIREWALL_ALLOWED[d])
        # 장치가 방화벽 뒤에서도 정상 주기로 도는지: PLC2가 PLC1 유량을 읽고 P201을 계속 쓰는지
        time.sleep(5)
        print('\n' + open('logs/firewall_rules.txt').read())
        tail = open('logs/plc2.log').read().strip().splitlines()[-1]
        print('plc2 last log line:', tail)
        print('\n(! = 기대와 다름)  mismatches=%d' % bad)
        print('RESULT=%s' % ('PASS' if bad == 0 else 'FAIL'))
    finally:
        run.stop(network)


if __name__ == '__main__':
    main()
