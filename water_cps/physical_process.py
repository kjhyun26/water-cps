"""공정 프로그램 (물리 계층): 착수정 수위, 주입 후 pH, 5시간 뒤 침전수 탁도를 계산한다.

한 걸음에 공정 30초를 전진한다. 설계 문서 4절.
"""

import collections
import csv
import math
import time

from minicps.devices import Tank

from config import (
    AIT101, AIT202, AIT301, CHEMICAL_PUMP_MAX, FIT102, INITIAL_CHEMICAL_PUMP_COMMAND,
    INITIAL_DOSE_SETPOINT, INITIAL_INTAKE_PUMP, INITIAL_LEVEL, INITIAL_RAW_TURBIDITY,
    INITIAL_SHUTOFF_VALVE, INTAKE_FLOW_HIGH, INTAKE_FLOW_LOW, LIT101, MINIMUM_RAW_TURBIDITY,
    OVERDOSE_COEFFICIENT, P101, P201, PH_DROP_PER_DOSE, PROCESS_LOG, PROCESS_SLEEP_SECONDS,
    PROCESS_STEP_SECONDS, PRODUCTION_FLOW, RAW_PH, REQUIRED_DOSE_BASE, REQUIRED_DOSE_SLOPE,
    SETTLED_TURBIDITY_BASE, SETTLING_DELAY_SECONDS, SIM_TIME, STATE, TANK_AREA, TANK_HEIGHT,
    UNDERDOSE_COEFFICIENT, XV201, to_number,
)

LOG_COLUMNS = [
    'sim_time_seconds', 'raw_turbidity_ntu', 'level_m', 'intake_pump',
    'chemical_pump_command_kg_per_hour', 'shutoff_valve', 'actual_dose_mg_per_liter',
    'required_dose_mg_per_liter', 'dosed_ph', 'settled_turbidity_ntu',
]


def required_dose(raw_turbidity):
    """원수 탁도(NTU) → 필요 주입률(mg/L). 센서로는 알 수 없는, 공정만 아는 값."""
    return REQUIRED_DOSE_BASE + REQUIRED_DOSE_SLOPE * math.log(max(raw_turbidity, MINIMUM_RAW_TURBIDITY))


def dosed_ph(actual_dose):
    """실제 주입률(mg/L) → 주입 후 pH."""
    return RAW_PH - PH_DROP_PER_DOSE * actual_dose


def settled_turbidity(dose_difference):
    """5시간 전의 (실제 주입률 − 필요 주입률) → 침전수 탁도(NTU). 모자라면 가파르게, 넘치면 완만하게 오른다."""
    if dose_difference < 0:
        return SETTLED_TURBIDITY_BASE + UNDERDOSE_COEFFICIENT * dose_difference ** 2
    return SETTLED_TURBIDITY_BASE + OVERDOSE_COEFFICIENT * dose_difference


class WaterProcess(Tank):

    def pre_loop(self):
        # 설계 문서 4-3절 표의 값으로 수식을 점검한다
        assert abs(required_dose(6) - 15.18) < 0.01
        assert abs(dosed_ph(110) - 5.75) < 0.01
        assert abs(settled_turbidity(-2) - 1.0) < 0.01
        assert abs(settled_turbidity(2) - 0.5) < 0.01

        self.level = INITIAL_LEVEL
        self.sim_time = 0
        initial_difference = INITIAL_DOSE_SETPOINT - required_dose(INITIAL_RAW_TURBIDITY)
        queue_length = SETTLING_DELAY_SECONDS // PROCESS_STEP_SECONDS
        # maxlen을 주지 않는다: append 뒤 popleft로 길이를 늘 queue_length로 유지한다
        self.difference_queue = collections.deque([initial_difference] * queue_length)

        # 상태 표가 깨진 값을 줄 때 쓸 직전 값
        self.raw_turbidity = INITIAL_RAW_TURBIDITY
        self.intake_pump = INITIAL_INTAKE_PUMP
        self.chemical_pump_command = INITIAL_CHEMICAL_PUMP_COMMAND
        self.shutoff_valve = INITIAL_SHUTOFF_VALVE

        self.log_file = open(PROCESS_LOG, 'w', newline='')
        self.log_writer = csv.writer(self.log_file)
        self.log_writer.writerow(LOG_COLUMNS)
        self.log_file.flush()

    def main_loop(self):
        while True:
            self.step()
            time.sleep(PROCESS_SLEEP_SECONDS)

    def step(self):
        # 0. 읽기
        self.raw_turbidity = to_number(self.get(AIT101), self.raw_turbidity)
        self.intake_pump = int(to_number(self.get(P101), self.intake_pump))
        self.chemical_pump_command = to_number(self.get(P201), self.chemical_pump_command)
        self.shutoff_valve = int(to_number(self.get(XV201), self.shutoff_valve))

        # 1. 수위
        intake_flow = INTAKE_FLOW_HIGH if self.intake_pump == 2 else INTAKE_FLOW_LOW
        self.level += (intake_flow - PRODUCTION_FLOW) / TANK_AREA * (PROCESS_STEP_SECONDS / 3600)
        self.level = min(max(self.level, 0.0), TANK_HEIGHT)

        # 2. 약품과 pH (차단밸브가 닫히면 펌프 명령과 상관없이 0)
        if self.shutoff_valve == 0:
            chemical_flow = 0.0
        else:
            chemical_flow = min(max(self.chemical_pump_command, 0.0), CHEMICAL_PUMP_MAX)
        actual_dose = chemical_flow * 1000 / PRODUCTION_FLOW
        ph = dosed_ph(actual_dose)

        # 3. 필요 주입률
        needed_dose = required_dose(self.raw_turbidity)

        # 4. 침전수 탁도 (5시간 지연)
        self.difference_queue.append(actual_dose - needed_dose)
        settled = settled_turbidity(self.difference_queue.popleft())

        # 5. 쓰기와 기록
        self.sim_time += PROCESS_STEP_SECONDS
        self.set(LIT101, round(self.level, 4))
        self.set(FIT102, round(PRODUCTION_FLOW, 4))
        self.set(AIT202, round(ph, 4))
        self.set(AIT301, round(settled, 4))
        self.set(SIM_TIME, self.sim_time)

        self.log_writer.writerow([
            self.sim_time, round(self.raw_turbidity, 4), round(self.level, 4), self.intake_pump,
            round(self.chemical_pump_command, 4), self.shutoff_valve, round(actual_dose, 4),
            round(needed_dose, 4), round(ph, 4), round(settled, 4),
        ])
        self.log_file.flush()


if __name__ == '__main__':
    WaterProcess(name='process', protocol=None, state=STATE, section=TANK_AREA, level=INITIAL_LEVEL)
