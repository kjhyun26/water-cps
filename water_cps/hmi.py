"""HMI: 네 장치의 값을 글자 화면으로 만들고, 주입률 설정값과 SIS 복귀 요청을 전달한다 (설계 문서 6절).

python hmi.py               화면 갱신 루프. logs/hmi_screen.txt 를 덮어쓴다
python hmi.py dose 20       주입률 설정값을 PLC2에 쓴다
python hmi.py sis reset     SIS에 복귀 요청을 쓴다
"""

import math
import os
import sys
import time

from minicps.devices import HMI

from config import (
    ADDRESS, AIT101, AIT202, AIT301, CLIENT_PROTOCOL, DOSE_SETPOINT, FIT102, HMI_SCREEN,
    HMI_SLEEP_SECONDS, INITIAL_CHEMICAL_PUMP_COMMAND, INITIAL_DOSE_SETPOINT, INITIAL_DOSED_PH,
    INITIAL_INTAKE_PUMP, INITIAL_LEVEL, INITIAL_RAW_TURBIDITY, INITIAL_SETTLED_TURBIDITY,
    INITIAL_SHUTOFF_VALVE, LIT101, P101, P201, PRODUCTION_FLOW, SIM_TIME_PLC1, SIS_RESET_REQUEST,
    STATE, TURBIDITY_ALARM, TURBIDITY_OVER_MINUTES, XV201, to_numbers,
)

USAGE = 'usage: python hmi.py | python hmi.py dose <mg/L> | python hmi.py sis reset'

# 장치마다 묻는 태그와 순서, 직전 값의 처음 값 (설계 문서 8-5절)
READS = {
    'plc1': ([SIM_TIME_PLC1, LIT101, P101, FIT102, AIT101],
             [0.0, INITIAL_LEVEL, INITIAL_INTAKE_PUMP, PRODUCTION_FLOW, INITIAL_RAW_TURBIDITY]),
    'plc2': ([DOSE_SETPOINT, P201], [INITIAL_DOSE_SETPOINT, INITIAL_CHEMICAL_PUMP_COMMAND]),
    'plc3': ([AIT301, TURBIDITY_OVER_MINUTES, TURBIDITY_ALARM], [INITIAL_SETTLED_TURBIDITY, 0.0, 0.0]),
    'sis': ([AIT202, XV201], [INITIAL_DOSED_PH, INITIAL_SHUTOFF_VALVE]),
}


def format_sim_time(seconds):
    day = int(seconds // 86400) + 1
    minutes_of_day = int(seconds % 86400) // 60
    return '%d일차 %02d:%02d' % (day, minutes_of_day // 60, minutes_of_day % 60)


def render_screen(values):
    sim_time, level, intake_pump, production_flow, raw_turbidity = values['plc1']
    dose_setpoint, chemical_pump_command = values['plc2']
    settled_turbidity, over_minutes, turbidity_alarm = values['plc3']
    dosed_ph, shutoff_valve = values['sis']

    alarms = []
    if int(turbidity_alarm) == 1:
        alarms.append('침전수 탁도 30분 넘게 초과')
    if int(shutoff_valve) == 0:
        alarms.append('SIS 작동: 약품 차단밸브 닫힘')

    lines = [
        '정수장 HMI                                  공정 시각 %s' % format_sim_time(sim_time),
        '',
        '착수정',
        '  수위           %.2f m' % level,
        '  취수 펌프      %s' % ('강' if int(intake_pump) == 2 else '약'),
        '  생산 유량      %d m³/h' % round(production_flow),
        '원수',
        '  탁도           %.1f NTU' % raw_turbidity,
        '약품 주입',
        '  주입률 설정    %.1f mg/L' % dose_setpoint,
        '  주입량         %.1f kg/h' % chemical_pump_command,
        '  주입 후 pH     %.2f  (허용 5.8~8.5)' % dosed_ph,
        '  차단밸브       %s' % ('열림' if int(shutoff_valve) == 1 else '닫힘'),
        '침전수',
        '  탁도           %.2f NTU  (목표 1.0 이하, 초과 %d분)' % (settled_turbidity, int(over_minutes)),
        '경보',
    ]
    lines += ['  ' + alarm for alarm in alarms] or ['  없음']
    return '\n'.join(lines) + '\n'


class Hmi(HMI):

    def main_loop(self):
        arguments = sys.argv[1:]
        if not arguments:
            self.refresh_screen_forever()
        elif len(arguments) == 2 and arguments[0] == 'dose':
            try:
                dose = round(float(arguments[1]), 4)
            except ValueError:
                print(USAGE)
                return
            # nan·inf 는 cpppo 가 정수로 읽으려다 실패하는데, 실패해도 알려 주지 않는다
            if not math.isfinite(dose):
                print(USAGE)
                return
            self.send(DOSE_SETPOINT, dose, ADDRESS['plc2'])
            print('DOSE_SETPOINT <- %s' % dose)
        elif arguments == ['sis', 'reset']:
            self.send(SIS_RESET_REQUEST, 1, ADDRESS['sis'])
            print('SIS_RESET_REQUEST <- 1')
        else:
            print(USAGE)

    def refresh_screen_forever(self):
        values = {device: list(initial) for device, (_, initial) in READS.items()}
        while True:
            for device, (tags, _) in READS.items():
                values[device] = to_numbers(self.receive_multiple(tags, ADDRESS[device]), values[device])
            temporary_path = HMI_SCREEN + '.tmp'
            with open(temporary_path, 'w', encoding='utf-8') as screen_file:
                screen_file.write(render_screen(values))
            os.replace(temporary_path, HMI_SCREEN)
            time.sleep(HMI_SLEEP_SECONDS)


if __name__ == '__main__':
    Hmi(name='hmi', state=STATE, protocol=CLIENT_PROTOCOL)
