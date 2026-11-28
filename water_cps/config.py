"""정수장 약품 주입 구간: 상수, 태그, 주소.

minicps와 mininet을 import하지 않는다. run.py(시스템 Python)와
장치 스크립트(가상환경 Python)가 둘 다 불러 쓰기 때문이다.
"""

import math
import sys
import time

# 시간
ACCELERATION = 60                    # 실제 1초 = 공정 60초
PROCESS_STEP_SECONDS = 30            # 공정 한 걸음 (공정 시각)
PROCESS_SLEEP_SECONDS = PROCESS_STEP_SECONDS / ACCELERATION   # 걸음 사이 실제 쉬는 시간
DEVICE_SLEEP_SECONDS = 1.0           # PLC, SIS
HMI_SLEEP_SECONDS = 2.0

# 공정
PRODUCTION_FLOW = 2000.0             # m3/h
INTAKE_FLOW_HIGH = 2400.0            # m3/h, P101 = 2
INTAKE_FLOW_LOW = 1600.0             # m3/h, P101 = 1
TANK_AREA = 200.0                    # m2
TANK_HEIGHT = 5.0                    # m
RAW_PH = 7.4
PH_DROP_PER_DOSE = 0.015             # mg/L 당 pH 하락
CHEMICAL_PUMP_MAX = 250.0            # kg/h
REQUIRED_DOSE_BASE = 9.8             # mg/L
REQUIRED_DOSE_SLOPE = 3.0            # mg/L per ln(NTU)
MINIMUM_RAW_TURBIDITY = 0.1          # NTU, ln 계산용 하한
SETTLING_DELAY_SECONDS = 5 * 3600    # 공정 시각
SETTLED_TURBIDITY_BASE = 0.4         # NTU
UNDERDOSE_COEFFICIENT = 0.15
OVERDOSE_COEFFICIENT = 0.05

# 제어
LEVEL_LOW = 3.3                      # m, 이하면 펌프 강
LEVEL_HIGH = 3.7                     # m, 이상이면 펌프 약
TURBIDITY_LIMIT = 1.0                # NTU
TURBIDITY_ALARM_MINUTES = 30
PH_LOW = 5.8
PH_HIGH = 8.5
SIS_TRIP_COUNT = 2

# 처음 값 (상태 표, 장치 서버, HMI 가 모두 이 값으로 시작한다)
INITIAL_RAW_TURBIDITY = 6.0
INITIAL_LEVEL = 3.5
INITIAL_INTAKE_PUMP = 2                                                        # 강
INITIAL_DOSE_SETPOINT = 17.0
INITIAL_CHEMICAL_PUMP_COMMAND = PRODUCTION_FLOW * INITIAL_DOSE_SETPOINT / 1000  # 34.0 kg/h
INITIAL_DOSED_PH = round(RAW_PH - PH_DROP_PER_DOSE * INITIAL_DOSE_SETPOINT, 4)  # 7.145
INITIAL_SETTLED_TURBIDITY = 0.49                                               # 첫 걸음에 0.4912 로 바뀐다
INITIAL_SHUTOFF_VALVE = 1                                                      # 열림

# 네트워크
ADDRESS = {
    'plc1': '192.168.1.10',
    'plc2': '192.168.1.20',
    'plc3': '192.168.1.30',
    'sis':  '192.168.1.40',
    'hmi':  '192.168.1.50',       # HMI 화면, 게이트웨이(gateway.py)
    'ai':   '192.168.1.60',       # AI 에이전트(ai_agent.py). 예측값 패킷만 쓰고 PLC2에는 쓰지 않는다
    'attacker': '192.168.1.70',   # 공격 시험용 호스트. 아무것도 띄우지 않는다
}
NETMASK = '/24'

# 방화벽 (run.USE_SECURITY = True 일 때 각 장치 이름공간의 iptables INPUT 에 건다)
# EtherNet/IP 포트로 새 연결을 받아도 되는 출발지. 표에 없는 출발지는 버린다(DROP).
# lo(자기 서버에 쓰기)와 ESTABLISHED,RELATED(내가 연 연결의 응답)는 항상 허용한다.
ENIP_PORT = 44818
FIREWALL_ALLOWED = {
    'plc1': ('plc2', 'hmi', 'ai'),   # PLC2는 유량, hmi(화면·게이트웨이)와 AI는 탁도·공정 시각을 읽는다
    'plc2': ('hmi',),                # 주입률 설정값은 hmi(게이트웨이·운영자)만 쓴다
    'plc3': ('hmi',),
    'sis':  ('hmi',),
}

# 물리 층 태그 (이름, 장치 번호)
SIM_TIME = ('SIM_TIME', 0)
AIT101 = ('AIT101', 1)
LIT101 = ('LIT101', 1)
FIT102 = ('FIT102', 1)
P101 = ('P101', 1)
P201 = ('P201', 2)
AIT301 = ('AIT301', 3)
AIT202 = ('AIT202', 4)
XV201 = ('XV201', 4)

# 통신 층 태그 (이름, 서버를 가진 장치 번호). 물리 층과 이름·번호가 같은 것은 그대로 쓴다
SIM_TIME_PLC1 = ('SIM_TIME', 1)
DOSE_SETPOINT = ('DOSE_SETPOINT', 2)
TURBIDITY_OVER_MINUTES = ('TURBIDITY_OVER_MINUTES', 3)
TURBIDITY_ALARM = ('TURBIDITY_ALARM', 3)
SIS_RESET_REQUEST = ('SIS_RESET_REQUEST', 4)

# 서버 선언 (이름, 번호, 자료형)
PLC1_TAGS = (('LIT101', 1, 'REAL'), ('FIT102', 1, 'REAL'), ('AIT101', 1, 'REAL'),
             ('P101', 1, 'INT'), ('SIM_TIME', 1, 'REAL'))
PLC2_TAGS = (('P201', 2, 'REAL'), ('DOSE_SETPOINT', 2, 'REAL'))
PLC3_TAGS = (('AIT301', 3, 'REAL'), ('TURBIDITY_OVER_MINUTES', 3, 'REAL'),
             ('TURBIDITY_ALARM', 3, 'INT'))
SIS_TAGS = (('AIT202', 4, 'REAL'), ('XV201', 4, 'INT'), ('SIS_RESET_REQUEST', 4, 'INT'))


def server_protocol(device_name, tags):
    """서버가 있는 장치의 통신 설정."""
    return {'name': 'enip', 'mode': 1,
            'server': {'address': ADDRESS[device_name], 'tags': tags}}


CLIENT_PROTOCOL = {'name': 'enip', 'mode': 0, 'server': ''}

# 상태 표
STATE = {'name': 'water_cps', 'path': 'water_cps.sqlite'}
SCHEMA = """
CREATE TABLE water_cps (
    name  TEXT NOT NULL,
    pid   INTEGER NOT NULL,
    value TEXT,
    PRIMARY KEY (name, pid)
);
"""
INITIAL_TABLE = [
    (SIM_TIME, 0), (AIT101, INITIAL_RAW_TURBIDITY), (LIT101, INITIAL_LEVEL), (FIT102, PRODUCTION_FLOW),
    (P101, INITIAL_INTAKE_PUMP), (P201, INITIAL_CHEMICAL_PUMP_COMMAND),
    (AIT301, INITIAL_SETTLED_TURBIDITY), (AIT202, INITIAL_DOSED_PH), (XV201, INITIAL_SHUTOFF_VALVE),
]
SCHEMA_INIT = ''.join("INSERT INTO water_cps VALUES ('%s', %d, '%s');\n" % (name, pid, value)
                      for (name, pid), value in INITIAL_TABLE)

PROCESS_LOG = 'logs/process.csv'
HMI_SCREEN = 'logs/hmi_screen.txt'


def to_number(text, previous):
    """읽은 글자를 실수로 바꾼다. 못 바꾸거나 nan·inf면 직전 값을 돌려준다."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return previous
    return value if math.isfinite(value) else previous


def to_numbers(texts, previous_values):
    """receive_multiple 결과를 실수 목록으로 바꾼다.
    길이가 물은 개수와 다르면 칸이 어긋났을 수 있으므로 전부 직전 값을 쓴다."""
    if not isinstance(texts, list) or len(texts) != len(previous_values):
        return list(previous_values)
    return [to_number(text, previous) for text, previous in zip(texts, previous_values)]


def write_initial_values(device, address, tags, values, check_tag, attempts=20, wait_seconds=0.5):
    """자기 서버에 처음 값을 쓰고, 확인 태그를 읽어 같은 값이 나올 때까지 되풀이한다 (설계 문서 7절 규칙 2).
    서버가 뜨기 전에 한 쓰기는 사라지므로 매번 쓰기부터 다시 한다. 끝내 안 되면 프로그램을 끝낸다."""
    expected = values[list(tags).index(check_tag)]
    for _ in range(attempts):
        device.send_multiple(list(tags), list(values), address)
        if abs(to_number(device.receive(check_tag, address), float('inf')) - expected) <= 0.01:
            print('initial values written to %s' % address)
            return
        time.sleep(wait_seconds)
    print('ERROR could not write initial values to %s' % address)
    sys.exit(1)


assert INITIAL_CHEMICAL_PUMP_COMMAND == 34.0 and INITIAL_DOSED_PH == 7.145
assert to_number("b'", 2000.0) == 2000.0
assert to_number('nan', 6.0) == 6.0 and to_number('inf', 6.0) == 6.0
assert to_numbers([], [3.5, 2.0]) == [3.5, 2.0]
assert to_numbers([b'0.0'], [3.5, 2.0, 6.0]) == [3.5, 2.0, 6.0]
assert to_numbers([b'3.4', b'1'], [3.5, 2.0]) == [3.4, 1.0]
