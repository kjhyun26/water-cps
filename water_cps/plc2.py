"""PLC2: 약품 주입량 = 생산 유량 × 주입률 (설계 문서 5-2절).

python plc2.py            받은 설정값을 그대로 쓴다 (보안 끔, 설계 문서 11-2절의 "일부러 남긴 한계")
python plc2.py --secure   PLC 자체 보호를 켠다 (run.USE_SECURITY = True). 게이트웨이를 거치지 않은 쓰기에도 걸린다
    범위: DOSE_SETPOINT 가 5~50 mg/L 밖이면 거부하고 직전에 받아들인 값을 태그에 다시 쓴다.
          (게이트웨이 ⑤의 8~45 보다 넓은 고정 한계. 게이트웨이가 뚫리거나 우회돼도 남는 마지막 선)
    ramp: 실제로 넣는 주입률은 공정 시각 어느 60초 구간에서도 2 mg/L 넘게 바뀌지 않게 목표값을 따라간다.
          공정 시각은 상태 표의 SIM_TIME 으로 잰다 (실제 배율이 60보다 느려도 공정 기준으로 지킨다).
    기록: DOSE_REJECT ... / DOSE_RAMP start ... / DOSE_RAMP done ... 을 logs/plc2.log 에 남긴다.
"""

import collections
import sys
import time

from minicps.devices import PLC

from config import (
    ADDRESS, DEVICE_SLEEP_SECONDS, DOSE_SETPOINT, FIT102, INITIAL_CHEMICAL_PUMP_COMMAND,
    INITIAL_DOSE_SETPOINT, P201, PLC2_TAGS, PRODUCTION_FLOW, SIM_TIME, STATE, server_protocol, to_number,
    write_initial_values,
)

OWN_ADDRESS = ADDRESS['plc2']
PLC1_ADDRESS = ADDRESS['plc1']

DOSE_LIMITS = (5.0, 50.0)          # mg/L, PLC 고정 한계
RAMP_PER_MINUTE = 2.0              # mg/L, 공정 시각 어느 60초 구간에서도 이보다 많이 바꾸지 않는다
RAMP_WINDOW_SECONDS = 60


class Plc2(PLC):

    def pre_loop(self):
        self.secure = '--secure' in sys.argv[1:]
        self.dose_setpoint = INITIAL_DOSE_SETPOINT     # 받아들인 목표값
        self.applied_dose = INITIAL_DOSE_SETPOINT      # 실제로 넣는 주입률 (보안 끔이면 목표값과 같다)
        self.production_flow = PRODUCTION_FLOW
        self.sim_time = None
        self.ramping = False
        self.applied_history = collections.deque()    # (공정 시각, 그때부터 적용한 값), ramp 용
        write_initial_values(self, OWN_ADDRESS, [P201, DOSE_SETPOINT],
                             [INITIAL_CHEMICAL_PUMP_COMMAND, INITIAL_DOSE_SETPOINT], DOSE_SETPOINT)
        print('PLC2 protection: %s' % ('on (range %.0f~%.0f mg/L, ramp %.1f mg/L per process minute)'
                                       % (DOSE_LIMITS[0], DOSE_LIMITS[1], RAMP_PER_MINUTE) if self.secure else 'off'))

    def main_loop(self):
        while True:
            cycle_start = time.time()

            received = to_number(self.receive(DOSE_SETPOINT, OWN_ADDRESS), self.dose_setpoint)
            self.production_flow = to_number(self.receive(FIT102, PLC1_ADDRESS), self.production_flow)

            chemical_pump_command = self.control(received)
            self.set(P201, chemical_pump_command)
            self.send(P201, chemical_pump_command, OWN_ADDRESS)

            time.sleep(DEVICE_SLEEP_SECONDS)
            print('cycle_seconds=%.2f' % (time.time() - cycle_start))

    def control(self, received):
        """받은 설정값으로 이번 주기의 약품 펌프 명령(kg/h)을 정한다."""
        if self.secure:
            self.check_setpoint(received)
            self.ramp()
        else:
            self.dose_setpoint = self.applied_dose = received
        return round(self.production_flow * self.applied_dose / 1000, 4)

    def check_setpoint(self, received):
        """범위 밖이면 거부하고 직전 값을 태그에 다시 쓴다. 범위 안이면 새 목표값으로 받아들인다."""
        if DOSE_LIMITS[0] <= received <= DOSE_LIMITS[1]:
            self.dose_setpoint = received
            return
        self.send(DOSE_SETPOINT, self.dose_setpoint, OWN_ADDRESS)
        print('DOSE_REJECT sim_time=%s value=%s limits=%.0f~%.0f restored=%s'
              % (self.sim_time, received, DOSE_LIMITS[0], DOSE_LIMITS[1], self.dose_setpoint), flush=True)

    def ramp(self):
        """실제 주입률을 목표값 쪽으로 옮긴다. 공정 시각 어느 60초 구간에서도 변화가 2 mg/L 를 넘지 않게 한다.

        지난 공정 60초 동안 적용했던 값(60초 전 시점에 적용 중이던 값 포함)을 모두 기억하고, 새 값이 그 값들
        모두와 2 mg/L 안에 있도록 자른다. PLC2 한 주기가 공정 30초일 때도 90초일 때도 같은 조건이 지켜지고,
        목표값을 받기 전에 흐른 시간은 앞당겨 쓰지 않는다. 주기가 공정 90초면 실제 속도는 2 mg/L / 90초가 된다.
        """
        now = to_number(self.get(SIM_TIME), self.sim_time)
        if now is None:
            return      # 공정 시각을 아직 모른다
        self.sim_time = now
        history = self.applied_history
        if not history:
            history.append((now, self.applied_dose))
        # 60초 전 시점에 적용 중이던 값 하나만 남기고 그보다 오래된 기록은 버린다
        while len(history) > 1 and history[1][0] <= now - RAMP_WINDOW_SECONDS:
            history.popleft()
        values = [value for _, value in history]
        low = max(values) - RAMP_PER_MINUTE
        high = min(values) + RAMP_PER_MINUTE
        target = self.dose_setpoint
        new = min(max(target, low), high)
        if abs(new - target) <= 1e-6:
            new = target
        if new != self.applied_dose:
            if new != target and not self.ramping:
                print('DOSE_RAMP start sim_time=%s from=%s target=%s limit=%.1f per %d process s'
                      % (now, round(self.applied_dose, 4), target, RAMP_PER_MINUTE, RAMP_WINDOW_SECONDS),
                      flush=True)
                self.ramping = True
            self.applied_dose = new
            history.append((now, new))
            if self.ramping:
                print('DOSE_RAMP step sim_time=%s applied=%s target=%s' % (now, round(new, 4), target), flush=True)
        if self.ramping and new == target:
            print('DOSE_RAMP done sim_time=%s applied=%s' % (now, round(new, 4)), flush=True)
            self.ramping = False

if __name__ == '__main__':
    Plc2(name='plc2', state=STATE, protocol=server_protocol('plc2', PLC2_TAGS))
